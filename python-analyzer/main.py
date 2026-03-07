"""
ETH Crypto Prediction System - 主入口
整合数据采集、技术分析、ML预测、可视化、提醒

Usage:
    python main.py                # 完整运行（分析 + 仪表板）
    python main.py --analyze      # 仅运行分析
    python main.py --train        # 仅训练模型
    python main.py --dashboard    # 仅启动仪表板
    python main.py --predict      # 仅输出预测
"""
import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime

import pandas as pd

from data_collector import DataCollector
from technical_analysis import TechnicalAnalyzer
from ml_predictor import MLPredictor
from visualization import Visualization
from alerts import AlertManager
import config


class ETHPredictionSystem:
    """ETH 预测系统主控"""

    def __init__(self):
        print("=" * 60)
        print("  🔮 ETH Crypto Prediction System v1.0")
        print(f"  启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)

        self.collector = DataCollector()
        self.analyzer = TechnicalAnalyzer()
        self.predictor = MLPredictor()
        self.visualizer = Visualization()
        self.alert_mgr = AlertManager()

        # 缓存数据
        self.cache = {
            "klines_df": pd.DataFrame(),
            "analyzed_df": pd.DataFrame(),
            "traders_df": pd.DataFrame(),
            "trades_df": pd.DataFrame(),
            "prediction": {},
            "analysis": {},
            "price_levels": [],
            "market_data": {},
            # 新增：按 timeframe 缓存不同周期的分析/预测，避免互相覆盖
            "timeframes": {},
        }

        # 尝试加载已有模型
        self.predictor.load_models()

    def collect_data(self):
        """采集所有数据"""
        print("\n[SYSTEM] ===== 开始数据采集 =====")

        # 1. 从 Go Collector 获取交易员数据
        traders_dict = self.collector.get_traders_from_collector()
        trades_dict = self.collector.get_trades_from_collector()
        price_levels = self.collector.get_price_levels_from_collector()
        market_data = self.collector.get_market_data_from_collector()

        # 转换为 DataFrame
        self.cache["traders_df"] = self.collector.traders_to_dataframe(traders_dict)
        self.cache["trades_df"] = self.collector.trades_to_dataframe(trades_dict)
        self.cache["price_levels"] = price_levels
        self.cache["market_data"] = market_data

        # 2. 获取K线数据 (直接从交易所或本地文件)
        df = self.collector.fetch_multi_exchange_ohlcv(symbol="ETH/USDT", timeframe="1h", limit=500)

        if df.empty:
            print("[SYSTEM] 无法从交易所获取数据，尝试本地文件...")
            df = self._generate_demo_data()

        self.cache["klines_df"] = df

        print(
            f"[SYSTEM] 数据采集完成: {len(df)} 条K线, "
            f"{len(self.cache['traders_df'])} 个交易员, "
            f"{len(self.cache['trades_df'])} 条交易"
        )

    def run_analysis(self):
        """运行技术分析"""
        print("\n[SYSTEM] ===== 开始技术分析 =====")

        df = self.cache["klines_df"]
        if df.empty:
            print("[SYSTEM] 无K线数据可分析")
            return

        # 技术分析
        analyzed = self.analyzer.analyze(df)
        self.cache["analyzed_df"] = analyzed

        # 获取最新分析摘要
        analysis = self.analyzer.get_latest_analysis(analyzed)
        self.cache["analysis"] = analysis

        # 支撑/阻力位
        sr = self.analyzer.get_support_resistance(analyzed)
        self.cache["support_resistance"] = sr

        print(f"[SYSTEM] 技术分析完成:")
        print(f"  - 当前信号: {analysis.get('signal', 'N/A')}")
        print(f"  - 信号强度: {analysis.get('signal_score', 0):.1f}")
        print(f"  - RSI: {analysis.get('rsi', 0):.1f}")
        print(f"  - MACD: {analysis.get('macd', 0):.4f}")
        if sr:
            print(f"  - 支撑位: {sr.get('support', [])[:3]}")
            print(f"  - 阻力位: {sr.get('resistance', [])[:3]}")

    def train_models(self):
        """训练/重新训练ML模型"""
        print("\n[SYSTEM] ===== 开始模型训练 =====")

        df = self.cache.get("analyzed_df")
        if df is None or df.empty:
            df = self.cache.get("klines_df")
            if df is not None and not df.empty:
                df = self.analyzer.analyze(df)

        if df is None or df.empty:
            print("[SYSTEM] 无数据可训练")
            return

        # 获取交易员数据
        trades_dict = self.collector.get_trades_from_collector()

        # 自动学习
        results = self.predictor.auto_learn(df, trades_dict)

        if results:
            print("[SYSTEM] 模型训练完成:")
            for name, result in results.items():
                print(f"  - {name}: 准确率 {result.get('accuracy', 0):.4f}")

    def run_prediction(self):
        """运行预测"""
        print("\n[SYSTEM] ===== 运行预测 =====")

        df = self.cache.get("analyzed_df")
        if df is None or df.empty:
            print("[SYSTEM] ���分析数据可预测")
            return

        if not self.predictor.is_trained:
            print("[SYSTEM] 模型未训练，先训练模型...")
            self.train_models()

        # 准备特征
        df_features = self.predictor.prepare_features(df)
        if df_features.empty:
            print("[SYSTEM] 特征准备失败")
            return

        # 执行预测
        prediction = self.predictor.predict(df_features)
        self.cache["prediction"] = prediction

        # 获取行动建议
        analysis = self.cache.get("analysis", {})
        recommendation = self.alert_mgr.get_action_recommendation(analysis, prediction)

        print(f"\n[SYSTEM] 预测结果:")
        print(f"  - 方向: {prediction.get('direction', 'N/A')}")
        print(f"  - 置信度: {prediction.get('confidence', 0):.1%}")
        print(f"  - 投票比: {prediction.get('vote_ratio', 0):.1%}")
        if prediction.get("details"):
            for model, detail in prediction["details"].items():
                print(f"  - {model}: {detail['direction']} ({detail['confidence']:.1%})")

        print(f"\n[SYSTEM] 行动建议:")
        print(f"  - 建议: {recommendation['action']}")
        print(f"  - 理由: {recommendation['reason']}")
        print(f"  - 买方权重: {recommendation['buy_score']:.1f}")
        print(f"  - 卖方权重: {recommendation['sell_score']:.1f}")

        # 检查提醒
        market_data = self.cache.get("market_data", {})
        self.alert_mgr.check_signals(analysis, prediction, market_data)

        return prediction

    def start_dashboard(self):
        """启动可视化仪表板（支持 timeframe 切换）"""
        print("\n[SYSTEM] ===== 启动可视化仪表板 =====")

        def ensure_timeframe_cached(timeframe: str):
            """
            确保某个 timeframe 的 K线/分析/预测已缓存。
            规则：如果缓存里没有该 timeframe，则拉取 -> 分析 -> 预测 -> 写入缓存。
            """
            tf = timeframe or "1h"
            if tf in self.cache["timeframes"]:
                return

            print(f"[SYSTEM] [TF] 初始化缓存: timeframe={tf}")

            df = self.collector.fetch_multi_exchange_ohlcv(symbol="ETH/USDT", timeframe=tf, limit=500)
            if df.empty:
                print(f"[SYSTEM] [TF] timeframe={tf} 获取K线为空，使用 demo 数据回退")
                df = self._generate_demo_data()

            analyzed = self.analyzer.analyze(df)
            analysis = self.analyzer.get_latest_analysis(analyzed)

            prediction = {}
            try:
                # 若模型未训练，这里不强制 train（避免 dashboard 首次启动很慢）
                # 如果你希望自动训练：把 pass 改成 self.train_models()
                if not self.predictor.is_trained:
                    pass

                df_features = self.predictor.prepare_features(analyzed)
                if not df_features.empty:
                    prediction = self.predictor.predict(df_features)
            except Exception as e:
                print(f"[SYSTEM] [TF] timeframe={tf} 预测失败: {e}")

            self.cache["timeframes"][tf] = {
                "klines_df": analyzed,
                "analysis": analysis,
                "prediction": prediction,
            }

        def data_callback(timeframe="1h"):
            """仪表板数据回调（Visualization 会传入 timeframe）"""
            ensure_timeframe_cached(timeframe)

            tf_pack = self.cache["timeframes"].get(timeframe, {})

            return {
                # 优先返回该 timeframe 的 analyzed_df
                "klines_df": tf_pack.get("klines_df", pd.DataFrame()),
                "prediction": tf_pack.get("prediction", {}),
                "analysis": tf_pack.get("analysis", {}),
                # trader 数据仍走全局 cache（由 collect_data/auto_update 维护）
                "traders_df": self.cache.get("traders_df", pd.DataFrame()),
                "trades_df": self.cache.get("trades_df", pd.DataFrame()),
                "price_levels": self.cache.get("price_levels", []),
                "market_data": self.cache.get("market_data", {}),
            }

        self.visualizer.start_dashboard(data_callback)

    def run_full_cycle(self):
        """运行完整分析周期"""
        self.collect_data()
        self.run_analysis()
        self.run_prediction()

    def run_auto_update(self, interval: int = 300):
        """后台自动更新循环"""
        def update_loop():
            while True:
                try:
                    print(f"\n[SYSTEM] 自动更新 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                    self.run_full_cycle()
                except Exception as e:
                    print(f"[SYSTEM] 更新出错: {e}")
                time.sleep(interval)

        thread = threading.Thread(target=update_loop, daemon=True)
        thread.start()
        print(f"[SYSTEM] 自动更新已启动 (每 {interval} 秒)")

    def save_charts(self):
        """生成并保存所有图表"""
        print("\n[SYSTEM] 生成图表...")

        df = self.cache.get("analyzed_df", pd.DataFrame())
        prediction = self.cache.get("prediction", {})
        traders_df = self.cache.get("traders_df", pd.DataFrame())
        trades_df = self.cache.get("trades_df", pd.DataFrame())
        price_levels = self.cache.get("price_levels", [])

        charts = {}

        if not df.empty:
            charts["candlestick"] = self.visualizer.create_candlestick_chart(df)
            charts["prediction"] = self.visualizer.create_prediction_chart(df, prediction)

        if not traders_df.empty:
            charts["traders"] = self.visualizer.create_trader_analysis_chart(traders_df, trades_df)

        if price_levels:
            charts["price_levels"] = self.visualizer.create_price_level_chart(price_levels)

        if charts:
            self.visualizer.save_charts(charts)
            print(f"[SYSTEM] {len(charts)} 个图表已保存")

    def print_trader_price_analysis(self):
        """打印各价格阶段交易员买卖情况"""
        trades_df = self.cache.get("trades_df", pd.DataFrame())
        if trades_df.empty:
            print("[SYSTEM] 无交易数据")
            return

        # 过滤ETH交易
        eth_trades = trades_df[trades_df["symbol"].str.contains("ETH", case=False, na=False)].copy()

        if eth_trades.empty:
            print("[SYSTEM] 无ETH交易数据")
            return

        # 按价格区间分组
        if "price" in eth_trades.columns:
            eth_trades["price_range"] = (eth_trades["price"].astype(float) // 50) * 50
            eth_trades["price_label"] = eth_trades["price_range"].apply(lambda x: f"${x:.0f} - ${x+50:.0f}")

            print("\n" + "=" * 80)
            print("ETH 各价格阶段交易员买卖统计")
            print("=" * 80)

            for price_range in sorted(eth_trades["price_label"].unique()):
                range_trades = eth_trades[eth_trades["price_label"] == price_range]
                buyers = range_trades[range_trades["side"] == "BUY"]
                sellers = range_trades[range_trades["side"] == "SELL"]

                print(f"\n📊 价格区间: {price_range}")
                print(f"  买入交易员 ({len(buyers)} 笔):")
                for _, t in buyers.head(10).iterrows():
                    tid = t.get("trader_id", "Unknown")[:15]
                    ex = t.get("exchange", "")
                    amt = t.get("amount", 0)
                    ts = t.get("open_time", "")
                    print(f"    [{ex}] {tid} - ${float(amt):,.2f} @ {ts}")

                print(f"  卖出交易员 ({len(sellers)} 笔):")
                for _, t in sellers.head(10).iterrows():
                    tid = t.get("trader_id", "Unknown")[:15]
                    ex = t.get("exchange", "")
                    amt = t.get("amount", 0)
                    ts = t.get("open_time", "")
                    print(f"    [{ex}] {tid} - ${float(amt):,.2f} @ {ts}")

    def _generate_demo_data(self) -> pd.DataFrame:
        """生成演示数据用于无API Key时测试"""
        import numpy as np

        print("[SYSTEM] 生成演示K线数据...")
        n = 500
        dates = pd.date_range(end=datetime.now(), periods=n, freq="1h")

        # 模拟ETH价格走势
        np.random.seed(42)
        base_price = 2500
        returns = np.random.normal(0.0001, 0.02, n)
        prices = base_price * np.exp(np.cumsum(returns))

        df = pd.DataFrame(
            {
                "open": prices * (1 + np.random.uniform(-0.005, 0.005, n)),
                "high": prices * (1 + np.random.uniform(0.001, 0.015, n)),
                "low": prices * (1 - np.random.uniform(0.001, 0.015, n)),
                "close": prices,
                "volume": np.random.uniform(1000, 50000, n),
            },
            index=dates,
        )

        return df


def main():
    parser = argparse.ArgumentParser(description="ETH Crypto Prediction System")
    parser.add_argument("--analyze", action="store_true", help="仅运行分析")
    parser.add_argument("--train", action="store_true", help="仅训练模型")
    parser.add_argument("--predict", action="store_true", help="仅输出预测")
    parser.add_argument("--dashboard", action="store_true", help="仅启动仪表板")
    parser.add_argument("--save-charts", action="store_true", help="生成并保存图表")
    parser.add_argument("--traders", action="store_true", help="显示交易员分析")
    args = parser.parse_args()

    system = ETHPredictionSystem()

    if args.analyze:
        system.collect_data()
        system.run_analysis()
        system.print_trader_price_analysis()

    elif args.train:
        system.collect_data()
        system.run_analysis()
        system.train_models()

    elif args.predict:
        system.collect_data()
        system.run_analysis()
        system.run_prediction()

    elif args.dashboard:
        system.collect_data()
        system.run_analysis()
        system.run_prediction()
        system.run_auto_update(interval=300)
        system.start_dashboard()

    elif args.save_charts:
        system.collect_data()
        system.run_analysis()
        system.run_prediction()
        system.save_charts()

    elif args.traders:
        system.collect_data()
        system.print_trader_price_analysis()

    else:
        system.collect_data()
        system.run_analysis()
        system.train_models()
        system.run_prediction()
        system.save_charts()
        system.print_trader_price_analysis()

        system.run_auto_update(interval=300)
        system.start_dashboard()


if __name__ == "__main__":
    main()
