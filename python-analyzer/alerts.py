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

        # dedupe_key -> last_timestamp_epoch
        self._last_sent = {}

        # 全局冷却（兜底）
        self._cooldown_seconds_default = int(self.cfg.get("cooldown_seconds", 180) or 180)
        # 按类型冷却（可选）
        self._cooldown_by_type = self.cfg.get("cooldown_seconds_by_type", {}) or {}

        # 按价格档位去重
        self._price_bucket_size = float(self.cfg.get("dedupe_price_bucket_size", 15.0) or 15.0)
        if self._price_bucket_size <= 0:
            self._price_bucket_size = 15.0

        # PRICE_SPIKE 的 action 阈值
        self._price_spike_action_threshold = float(self.cfg.get("price_spike_action_threshold", 5.0) or 5.0)

        # 节流：限制 check_signals 执行频率
        self._last_check_ts = 0.0
        self._check_interval_seconds = int(self.cfg.get("check_interval_seconds", 0) or 0)

        self._load_alerts()
        self._load_meta()

    def _cooldown_for_alert(self, alert: dict) -> int:
        a_type = str(alert.get("type", "") or "")
        v = self._cooldown_by_type.get(a_type, None)
        try:
            if v is None:
                return self._cooldown_seconds_default
            v = int(v)
            if v <= 0:
                return 0
            return v
        except Exception:
            return self._cooldown_seconds_default

    def check_signals(self, analysis: dict, prediction: dict, market_data: dict, timeframe: str = "1h") -> list:
        """
        检查是否有需要发送的提醒
        返回新的提醒列表（已去重、已保存的）
        """
        now_epoch = time.time()

        # 节流
        if self._check_interval_seconds > 0 and (now_epoch - self._last_check_ts) < self._check_interval_seconds:
            return []
        self._last_check_ts = now_epoch

        tf = (timeframe or "1h").strip() or "1h"

        new_alerts = []

        price = analysis.get("price", 0)
        signal = analysis.get("signal", "HOLD")
        signal_score = analysis.get("signal_score", 0)
        rsi = analysis.get("rsi", 50)
        pred_direction = prediction.get("direction", "HOLD")
        pred_confidence = prediction.get("confidence", 0)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1) 技术分析信号
        if signal in ["STRONG_BUY", "STRONG_SELL"]:
            new_alerts.append(
                {
                    "type": "TECHNICAL_SIGNAL",
                    "signal": signal,
                    "price": price,
                    "score": signal_score,
                    "timeframe": tf,
                    "message": f"⚡ 强信号! {signal} [{tf}] @ ${price:,.2f} (强度: {signal_score:.1f})",
                    "timestamp": now_str,
                    "priority": "HIGH",
                }
            )
        elif signal in ["BUY", "SELL"]:
            new_alerts.append(
                {
                    "type": "TECHNICAL_SIGNAL",
                    "signal": signal,
                    "price": price,
                    "score": signal_score,
                    "timeframe": tf,
                    "message": f"📊 信号: {signal} [{tf}] @ ${price:,.2f} (强度: {signal_score:.1f})",
                    "timestamp": now_str,
                    "priority": "MEDIUM",
                }
            )

        # 2) RSI 超买超卖
        if rsi and rsi > self.cfg["rsi_overbought"]:
            new_alerts.append(
                {
                    "type": "RSI_OVERBOUGHT",
                    "signal": "SELL",
                    "price": price,
                    "rsi": rsi,
                    "timeframe": tf,
                    "message": f"🔴 RSI 超买! RSI={rsi:.1f} [{tf}] @ ${price:,.2f} - 考虑卖出",
                    "timestamp": now_str,
                    "priority": "HIGH",
                }
            )
        elif rsi and rsi < self.cfg["rsi_oversold"]:
            new_alerts.append(
                {
                    "type": "RSI_OVERSOLD",
                    "signal": "BUY",
                    "price": price,
                    "rsi": rsi,
                    "timeframe": tf,
                    "message": f"🟢 RSI 超卖! RSI={rsi:.1f} [{tf}] @ ${price:,.2f} - 考虑买入",
                    "timestamp": now_str,
                    "priority": "HIGH",
                }
            )

        # 3) ML 预测信号
        if pred_confidence >= self.cfg["confidence_threshold"]:
            action = "🟢 买入" if pred_direction == "UP" else "🔴 卖出"
            new_alerts.append(
                {
                    "type": "ML_PREDICTION",
                    "signal": "BUY" if pred_direction == "UP" else "SELL",
                    "price": price,
                    "confidence": pred_confidence,
                    "timeframe": tf,
                    "message": f"🤖 AI预测: {action} [{tf}] @ ${price:,.2f} (置信度: {pred_confidence:.1%})",
                    "timestamp": now_str,
                    "priority": "HIGH",
                    "details": prediction.get("details", {}),
                }
            )

        # 4) 成交量异常
        vol_ratio = analysis.get("volume_ratio", 1.0)
        if vol_ratio and vol_ratio > self.cfg["volume_spike_threshold"]:
            new_alerts.append(
                {
                    "type": "VOLUME_SPIKE",
                    "signal": "ALERT",
                    "price": price,
                    "volume_ratio": vol_ratio,
                    "timeframe": tf,
                    "message": f"📈 成交量异常! [{tf}] 当前为均值的 {vol_ratio:.1f}x @ ${price:,.2f}",
                    "timestamp": now_str,
                    "priority": "MEDIUM",
                }
            )

        # 5) 价格大幅变化（24h）
        if market_data:
            change = market_data.get("change_24h", 0)
            if abs(change) > self.cfg["price_change_threshold"]:
                direction = "暴涨" if change > 0 else "暴跌"
                if change < -self._price_spike_action_threshold:
                    sig = "BUY"
                elif change > self._price_spike_action_threshold:
                    sig = "SELL"
                else:
                    sig = "ALERT"

                new_alerts.append(
                    {
                        "type": "PRICE_SPIKE",
                        "signal": sig,
                        "price": price,
                        "change": change,
                        "timeframe": tf,
                        "message": f"💥 价格{direction}! [{tf}] 24h变化: {change:+.2f}% @ ${price:,.2f}",
                        "timestamp": now_str,
                        "priority": "CRITICAL",
                    }
                )

        saved_alerts = []
        for alert in new_alerts:
            dedupe_key = self._make_dedupe_key(alert)
            last_ts = float(self._last_sent.get(dedupe_key, 0))

            cooldown = self._cooldown_for_alert(alert)
            if cooldown > 0 and (now_epoch - last_ts) < cooldown:
                continue

            self._last_sent[dedupe_key] = now_epoch
            self.alerts.append(alert)
            saved_alerts.append(alert)
            self._print_alert(alert)

        self._save_alerts()
        self._save_meta()

        return saved_alerts

    def get_action_recommendation(self, analysis: dict, prediction: dict) -> dict:
        signal = analysis.get("signal", "HOLD")
        signal_score = analysis.get("signal_score", 0)
        rsi = analysis.get("rsi", 50)
        pred_direction = prediction.get("direction", "HOLD")
        pred_confidence = prediction.get("confidence", 0)
        price = analysis.get("price", 0)

        buy_score = 0
        sell_score = 0

        if signal in ["BUY", "STRONG_BUY"]:
            buy_score += abs(signal_score) * 2
        elif signal in ["SELL", "STRONG_SELL"]:
            sell_score += abs(signal_score) * 2

        if rsi:
            if rsi < 30:
                buy_score += 3
            elif rsi < 40:
                buy_score += 1
            elif rsi > 70:
                sell_score += 3
            elif rsi > 60:
                sell_score += 1

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

    def _make_dedupe_key(self, alert: dict) -> str:
        a_type = alert.get("type", "") or ""
        a_signal = alert.get("signal", "") or ""
        tf = alert.get("timeframe", "") or "1h"

        price = alert.get("price", 0)
        try:
            price = float(price or 0.0)
        except Exception:
            price = 0.0

        bucket = self._price_to_bucket(price)
        return f"{a_type}|{a_signal}|{tf}|{bucket}"

    def _price_to_bucket(self, price: float) -> str:
        try:
            b = (price // self._price_bucket_size) * self._price_bucket_size
            if abs(b - round(b)) < 1e-9:
                return str(int(round(b)))
            return str(round(b, 6))
        except Exception:
            return "0"

    def _print_alert(self, alert: dict):
        priority = alert.get("priority", "LOW")
        colors = {
            "CRITICAL": "\033[91m",
            "HIGH": "\033[93m",
            "MEDIUM": "\033[96m",
            "LOW": "\033[92m",
        }
        reset = "\033[0m"
        color = colors.get(priority, "")
        print(f"\n{color}{'='*60}")
        print(f"[{priority}] {alert.get('message', '')}")
        print(f"时间: {alert.get('timestamp', '')}")
        print(f"{'='*60}{reset}\n")

    def _atomic_write_json(self, path: str, payload):
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)

    def _save_alerts(self):
        recent = self.alerts[-500:]
        self._atomic_write_json(self.alert_log_path, recent)

    def _load_alerts(self):
        if os.path.exists(self.alert_log_path):
            try:
                with open(self.alert_log_path, "r", encoding="utf-8") as f:
                    self.alerts = json.load(f) or []
            except Exception as e:
                print(f"[AlertManager] 读取 alerts.json 失败: {e}")
                self.alerts = []

    def _save_meta(self):
        try:
            items = []
            for k, ts in self._last_sent.items():
                try:
                    items.append((str(k), float(ts)))
                except Exception:
                    continue

            items.sort(key=lambda x: x[1], reverse=True)
            limited = dict(items[:2000])

            payload = {
                "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "cooldown_seconds_default": self._cooldown_seconds_default,
                "cooldown_seconds_by_type": self._cooldown_by_type,
                "dedupe_price_bucket_size": self._price_bucket_size,
                "last_sent": limited,
            }
            self._atomic_write_json(self.alert_meta_path, payload)
        except Exception as e:
            print(f"[AlertManager] 保存 alerts_meta.json 失败: {e}")

    def _load_meta(self):
        if not os.path.exists(self.alert_meta_path):
            return
        try:
            with open(self.alert_meta_path, "r", encoding="utf-8") as f:
                payload = json.load(f) or {}
            last_sent = payload.get("last_sent", {}) or {}

            cleaned = {}
            for k, ts in last_sent.items():
                try:
                    cleaned[str(k)] = float(ts)
                except Exception:
                    continue
            self._last_sent = cleaned
        except Exception as e:
            print(f"[AlertManager] 读取 alerts_meta.json 失败: {e}")
            self._last_sent = {}

    def get_recent_alerts(self, n: int = 20) -> list:
        return self.alerts[-n:]
