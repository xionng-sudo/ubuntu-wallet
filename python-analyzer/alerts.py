"""
ETH Crypto Prediction System - 预警提醒模块
发送买卖信号提醒
"""
import json
import os
import time
from datetime import datetime

import config


class AlertManager:
    """交易信号提醒管理器"""

    def __init__(self):
        self.alerts = []
        self.alert_log_path = os.path.join(config.DATA_DIR, "alerts.json")
        self.alert_meta_path = os.path.join(config.DATA_DIR, "alerts_meta.json")
        self.cfg = config.ALERT_CONFIG

        # ✅ 冷却/去重（持久化）
        # dedupe_key -> last_timestamp_epoch
        self._last_sent = {}
        self._cooldown_seconds = int(self.cfg.get("cooldown_seconds", 180))

        # ✅ 方案3：按价格档位去重
        self._price_bucket_size = float(self.cfg.get("dedupe_price_bucket_size", 10.0) or 10.0)
        if self._price_bucket_size <= 0:
            self._price_bucket_size = 10.0

        self._load_alerts()
        self._load_meta()

    # ================================================================
    # Public APIs
    # ================================================================

    def check_signals(self, analysis: dict, prediction: dict, market_data: dict) -> list:
        """
        检查是否有需要发送的提醒
        返回新的提醒列表（已去重、已保存的）
        """
        new_alerts = []

        price = analysis.get("price", 0)
        signal = analysis.get("signal", "HOLD")
        signal_score = analysis.get("signal_score", 0)
        rsi = analysis.get("rsi", 50)
        pred_direction = prediction.get("direction", "HOLD")
        pred_confidence = prediction.get("confidence", 0)

        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        # ─── 1. 技术分析信号 ───
        if signal in ["STRONG_BUY", "STRONG_SELL"]:
            alert = {
                "type": "TECHNICAL_SIGNAL",
                "signal": signal,
                "price": price,
                "score": signal_score,
                "message": f"⚡ 强信号! {signal} @ ${price:,.2f} (强度: {signal_score:.1f})",
                "timestamp": now_str,
                "priority": "HIGH",
            }
            new_alerts.append(alert)

        elif signal in ["BUY", "SELL"]:
            alert = {
                "type": "TECHNICAL_SIGNAL",
                "signal": signal,
                "price": price,
                "score": signal_score,
                "message": f"📊 信号: {signal} @ ${price:,.2f} (强度: {signal_score:.1f})",
                "timestamp": now_str,
                "priority": "MEDIUM",
            }
            new_alerts.append(alert)

        # ─── 2. RSI 超买超卖 ───
        if rsi and rsi > self.cfg["rsi_overbought"]:
            alert = {
                "type": "RSI_OVERBOUGHT",
                "signal": "SELL",
                "price": price,
                "rsi": rsi,
                "message": f"🔴 RSI 超买! RSI={rsi:.1f} @ ${price:,.2f} - 考虑卖出",
                "timestamp": now_str,
                "priority": "HIGH",
            }
            new_alerts.append(alert)
        elif rsi and rsi < self.cfg["rsi_oversold"]:
            alert = {
                "type": "RSI_OVERSOLD",
                "signal": "BUY",
                "price": price,
                "rsi": rsi,
                "message": f"🟢 RSI 超卖! RSI={rsi:.1f} @ ${price:,.2f} - 考虑买入",
                "timestamp": now_str,
                "priority": "HIGH",
            }
            new_alerts.append(alert)

        # ─── 3. ML 预测信号 ───
        if pred_confidence >= self.cfg["confidence_threshold"]:
            action = "🟢 买入" if pred_direction == "UP" else "🔴 卖出"
            alert = {
                "type": "ML_PREDICTION",
                "signal": "BUY" if pred_direction == "UP" else "SELL",
                "price": price,
                "confidence": pred_confidence,
                "message": (f"🤖 AI预测: {action} @ ${price:,.2f} "
                           f"(置信度: {pred_confidence:.1%})"),
                "timestamp": now_str,
                "priority": "HIGH",
                "details": prediction.get("details", {}),
            }
            new_alerts.append(alert)

        # ─── 4. 成交量异常 ───
        vol_ratio = analysis.get("volume_ratio", 1.0)
        if vol_ratio and vol_ratio > self.cfg["volume_spike_threshold"]:
            alert = {
                "type": "VOLUME_SPIKE",
                "signal": "ALERT",
                "price": price,
                "volume_ratio": vol_ratio,
                "message": f"📈 成交量异常! 当前为均值的 {vol_ratio:.1f}x @ ${price:,.2f}",
                "timestamp": now_str,
                "priority": "MEDIUM",
            }
            new_alerts.append(alert)

        # ─── 5. 价格大幅变化 ───
        if market_data:
            change = market_data.get("change_24h", 0)
            if abs(change) > self.cfg["price_change_threshold"]:
                direction = "暴涨" if change > 0 else "暴跌"
                alert = {
                    "type": "PRICE_SPIKE",
                    "signal": "BUY" if change < -5 else "SELL" if change > 5 else "ALERT",
                    "price": price,
                    "change": change,
                    "message": f"💥 价格{direction}! 24h变化: {change:+.2f}% @ ${price:,.2f}",
                    "timestamp": now_str,
                    "priority": "CRITICAL",
                }
                new_alerts.append(alert)

        # 保存新提醒（带去重/冷却）
        saved_alerts = []
        now_epoch = time.time()

        for alert in new_alerts:
            dedupe_key = self._make_dedupe_key(alert)
            last_ts = float(self._last_sent.get(dedupe_key, 0))

            if now_epoch - last_ts < self._cooldown_seconds:
                continue

            self._last_sent[dedupe_key] = now_epoch

            self.alerts.append(alert)
            saved_alerts.append(alert)
            self._print_alert(alert)

        # ✅ 分开保存：alerts 内容 + meta(last_sent)
        self._save_alerts()
        self._save_meta()

        return saved_alerts

    def get_action_recommendation(self, analysis: dict, prediction: dict) -> dict:
        """
        综合技术分析和ML预测，给出行动建议
        """
        signal = analysis.get("signal", "HOLD")
        signal_score = analysis.get("signal_score", 0)
        rsi = analysis.get("rsi", 50)
        pred_direction = prediction.get("direction", "HOLD")
        pred_confidence = prediction.get("confidence", 0)
        price = analysis.get("price", 0)

        buy_score = 0
        sell_score = 0

        # 技术分析贡献
        if signal in ["BUY", "STRONG_BUY"]:
            buy_score += abs(signal_score) * 2
        elif signal in ["SELL", "STRONG_SELL"]:
            sell_score += abs(signal_score) * 2

        # RSI 贡献
        if rsi:
            if rsi < 30:
                buy_score += 3
            elif rsi < 40:
                buy_score += 1
            elif rsi > 70:
                sell_score += 3
            elif rsi > 60:
                sell_score += 1

        # ML 预测贡献
        if pred_direction == "UP":
            buy_score += pred_confidence * 10
        elif pred_direction == "DOWN":
            sell_score += pred_confidence * 10

        total = buy_score + sell_score
        if total == 0:
            return {
                "action": "HOLD",
                "reason": "信号不足",
                "price": price,
                "buy_score": buy_score,
                "sell_score": sell_score,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

        if buy_score > sell_score * 1.5:
            action = "BUY"
            reason = "技术指标和AI预测均看涨"
        elif sell_score > buy_score * 1.5:
            action = "SELL"
            reason = "技术指标和AI预测均看跌"
        elif buy_score > sell_score:
            action = "WEAK_BUY"
            reason = "轻微看涨信号，建议轻仓"
        elif sell_score > buy_score:
            action = "WEAK_SELL"
            reason = "轻微看跌信号，建议减仓"
        else:
            action = "HOLD"
            reason = "多空均衡，建议观望"

        return {
            "action": action,
            "reason": reason,
            "price": price,
            "buy_score": float(buy_score),
            "sell_score": float(sell_score),
            "confidence": float(max(buy_score, sell_score) / max(total, 1)),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def get_recent_alerts(self, n: int = 20) -> list:
        """获取最近 n 条提醒"""
        return self.alerts[-n:]

    # ================================================================
    # Internals
    # ================================================================

    def _make_dedupe_key(self, alert: dict) -> str:
        """
        方案3：type|signal|price_bucket
        price_bucket 规则：按 bucket_size 向下取整
        """
        a_type = alert.get("type", "") or ""
        a_signal = alert.get("signal", "") or ""

        price = alert.get("price", 0)
        try:
            price = float(price or 0.0)
        except Exception:
            price = 0.0

        bucket = self._price_to_bucket(price)
        return f"{a_type}|{a_signal}|{bucket}"

    def _price_to_bucket(self, price: float) -> str:
        try:
            # 向下取整到档位：例如 3058 -> 3050（bucket=10）
            b = (price // self._price_bucket_size) * self._price_bucket_size
            # 用整数显示更干净（如果 bucket_size 是 10/50 这种）
            if abs(b - round(b)) < 1e-9:
                return str(int(round(b)))
            return str(round(b, 6))
        except Exception:
            return "0"

    def _print_alert(self, alert: dict):
        """在终端打印提醒"""
        priority = alert.get("priority", "LOW")
        colors = {
            "CRITICAL": "\033[91m",  # 红色
            "HIGH": "\033[93m",      # 黄色
            "MEDIUM": "\033[96m",    # 青色
            "LOW": "\033[92m",       # 绿色
        }
        reset = "\033[0m"
        color = colors.get(priority, "")

        print(f"\n{color}{'='*60}")
        print(f"[{priority}] {alert.get('message', '')}")
        print(f"时间: {alert.get('timestamp', '')}")
        print(f"{'='*60}{reset}\n")

    def _atomic_write_json(self, path: str, payload):
        """原子写入 JSON，减少文件损坏风险"""
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)

    def _save_alerts(self):
        """保存提醒到文件"""
        recent = self.alerts[-500:]  # 只保留最近 500 条
        self._atomic_write_json(self.alert_log_path, recent)

    def _load_alerts(self):
        """从文件加载历史提醒"""
        if os.path.exists(self.alert_log_path):
            try:
                with open(self.alert_log_path, "r", encoding="utf-8") as f:
                    self.alerts = json.load(f) or []
            except Exception:
                self.alerts = []

    def _save_meta(self):
        """保存去重 meta（last_sent）"""
        try:
            # 只保存最近一部分 key，避免无限增长
            # 这里简单按时间排序截断
            items = []
            for k, ts in self._last_sent.items():
                try:
                    items.append((k, float(ts)))
                except Exception:
                    continue
            items.sort(key=lambda x: x[1], reverse=True)
            limited = dict(items[:2000])

            payload = {
                "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "cooldown_seconds": self._cooldown_seconds,
                "dedupe_price_bucket_size": self._price_bucket_size,
                "last_sent": limited,
            }
            self._atomic_write_json(self.alert_meta_path, payload)
        except Exception:
            # meta 写失败不影响主流程
            pass

    def _load_meta(self):
        """加载去重 meta（last_sent）"""
        if not os.path.exists(self.alert_meta_path):
            return
        try:
            with open(self.alert_meta_path, "r", encoding="utf-8") as f:
                payload = json.load(f) or {}
            last_sent = payload.get("last_sent", {}) or {}
            # 强制转换成 float
            cleaned = {}
            for k, ts in last_sent.items():
                try:
                    cleaned[str(k)] = float(ts)
                except Exception:
                    continue
            self._last_sent = cleaned
        except Exception:
            self._last_sent = {}
