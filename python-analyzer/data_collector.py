"""
ETH Crypto Prediction System - 数据采集模块
通过 ccxt 库从 Binance/OKX/Coinbase 获取数据，
也可从 Go Collector API 获取已采集的交易员数据。
"""
import json
import os
import time
from datetime import datetime, timedelta
from typing import Optional

import ccxt
import pandas as pd
import requests

import config


class DataCollector:
    """统一数据采集器，支持 Binance / OKX / Coinbase"""

    def __init__(self):
        self.exchanges = {}
        self._init_exchanges()
        self.collector_api = config.COLLECTOR_API_URL

    def _init_exchanges(self):
        """初始化交易所连接"""
        # Binance
        self.exchanges["binance"] = ccxt.binance({
            "apiKey": config.BINANCE_API_KEY,
            "secret": config.BINANCE_API_SECRET,
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })

        # OKX
        if not all([config.OKX_API_KEY, config.OKX_API_SECRET, config.OKX_PASSPHRASE]):
            print("⚠️警告：OKX账号配置不完整，请检查 config.py 配置！")

        self.exchanges["okx"] = ccxt.okx({
            "apiKey": config.OKX_API_KEY,
            "secret": config.OKX_API_SECRET,
            "password": config.OKX_PASSPHRASE,
            "enableRateLimit": True,
        })

        # Coinbase
        self.exchanges["coinbase"] = ccxt.coinbase({
            "apiKey": config.COINBASE_API_KEY,
            "secret": config.COINBASE_API_SECRET,
            "enableRateLimit": True,
        })

    # ─── 从 Go Collector API 获取数据 ───

    def get_traders_from_collector(self) -> dict:
        """从 Go Collector 获取交易员数据"""
        try:
            resp = requests.get(f"{self.collector_api}/api/traders", timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"[DataCollector] 从 Collector API 获取交易员失败: {e}")
            return self._load_local_data("traders.json") or {}

    def get_trades_from_collector(self, exchange: Optional[str] = None) -> dict:
        """从 Go Collector 获取交易数据"""
        try:
            url = f"{self.collector_api}/api/trades"
            if exchange:
                url += f"?exchange={exchange}"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"[DataCollector] 从 Collector API 获取交易失败: {e}")
            return self._load_local_data("trades.json") or {}

    def get_price_levels_from_collector(self) -> list:
        """从 Go Collector 获取价格层级分析"""
        try:
            resp = requests.get(f"{self.collector_api}/api/price-levels", timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"[DataCollector] 获取价格层级失败: {e}")
            return self._load_local_data("price_levels.json") or []

    def get_market_data_from_collector(self) -> dict:
        """从 Go Collector 获取市场数据"""
        try:
            resp = requests.get(f"{self.collector_api}/api/market", timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"[DataCollector] 获取市场数据失败: {e}")
            return self._load_local_data("market_data.json") or {}

    # ─── 直接从交易所获取市场数据 ───

    def fetch_ohlcv(self, exchange_name: str, symbol: str = "ETH/USDT",
                    timeframe: str = "1h", limit: int = 500) -> pd.DataFrame:
        """获取K线数据并返回 DataFrame"""
        try:
            exchange = self.exchanges.get(exchange_name)
            if not exchange:
                print(f"[DataCollector] 未知交易所: {exchange_name}")
                return pd.DataFrame()

            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)

            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df.set_index("timestamp", inplace=True)
            df["exchange"] = exchange_name
            df["symbol"] = symbol
            df["interval"] = timeframe

            return df

        except Exception as e:
            print(f"[DataCollector] 获取 {exchange_name} OHLCV 失败: {e}")
            return pd.DataFrame()

    def fetch_ticker(self, exchange_name: str, symbol: str = "ETH/USDT") -> dict:
        """获取最新行情"""
        try:
            exchange = self.exchanges.get(exchange_name)
            if not exchange:
                return {}
            return exchange.fetch_ticker(symbol)
        except Exception as e:
            print(f"[DataCollector] 获取 {exchange_name} ticker 失败: {e}")
            return {}

    def fetch_order_book(self, exchange_name: str, symbol: str = "ETH/USDT",
                         limit: int = 50) -> dict:
        """获取订单簿数据"""
        try:
            exchange = self.exchanges.get(exchange_name)
            if not exchange:
                return {}
            return exchange.fetch_order_book(symbol, limit)
        except Exception as e:
            print(f"[DataCollector] 获取 {exchange_name} order book 失败: {e}")
            return {}

    def fetch_recent_trades(self, exchange_name: str, symbol: str = "ETH/USDT",
                            limit: int = 100) -> list:
        """获取最近成交"""
        try:
            exchange = self.exchanges.get(exchange_name)
            if not exchange:
                return []
            return exchange.fetch_trades(symbol, limit=limit)
        except Exception as e:
            print(f"[DataCollector] 获取 {exchange_name} trades 失败: {e}")
            return []

    def fetch_multi_exchange_ohlcv(self, symbol: str = "ETH/USDT",
                                   timeframe: str = "1h", limit: int = 500) -> pd.DataFrame:
        """从多个交易所获取K线并合并（以Binance为主）"""
        all_data = []
        for name in ["binance", "okx", "coinbase"]:
            df = self.fetch_ohlcv(name, symbol, timeframe, limit)
            if not df.empty:
                all_data.append(df)
            time.sleep(0.5)

        if not all_data:
            # 尝试从本地文件恢复
            return self._load_klines_from_file(timeframe)

        # 以Binance为主数据
        primary = all_data[0]
        return primary

    # ─── 交易员交易数据转 DataFrame ───

    def trades_to_dataframe(self, trades_dict: dict) -> pd.DataFrame:
        """将交易字典转换为 DataFrame"""
        all_trades = []
        for trader_id, trades in (trades_dict or {}).items():
            for trade in trades or []:
                if isinstance(trade, dict):
                    trade["trader_id"] = trader_id
                    all_trades.append(trade)

        if not all_trades:
            return pd.DataFrame()

        df = pd.DataFrame(all_trades)

        # 解析时间：统一解析成 tz-aware UTC，避免混合时区/空字符串导致 NaT 泛滥
        for col in ["open_time", "close_time", "update_time"]:
            if col in df.columns:
                s = df[col]
                # 标准化空字符串/None
                s = s.replace("", pd.NA)
                df[col] = pd.to_datetime(s, errors="coerce", utc=True)

        return df

    def traders_to_dataframe(self, traders_dict: dict) -> pd.DataFrame:
        """将交易员字典转换为 DataFrame"""
        all_traders = []
        for exchange, traders in (traders_dict or {}).items():
            for trader in traders or []:
                if isinstance(trader, dict):
                    trader["exchange"] = exchange
                    all_traders.append(trader)

        if not all_traders:
            return pd.DataFrame()

        return pd.DataFrame(all_traders)

    # ─── 本地数据读写 ───

    def _load_local_data(self, filename: str):
        """从本地文件加载数据"""
        filepath = os.path.join(config.DATA_DIR, filename)
        if os.path.exists(filepath):
            with open(filepath, "r") as f:
                return json.load(f)
        return None

    def _load_klines_from_file(self, interval: str) -> pd.DataFrame:
        """从本地文件加载K线数据"""
        filepath = os.path.join(config.DATA_DIR, f"klines_{interval}.json")
        if os.path.exists(filepath):
            with open(filepath, "r") as f:
                data = json.load(f)
            df = pd.DataFrame(data)
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
                df.set_index("timestamp", inplace=True)
            return df
        return pd.DataFrame()

    def save_dataframe(self, df: pd.DataFrame, filename: str):
        """保存 DataFrame 到文件"""
        filepath = os.path.join(config.DATA_DIR, filename)
        df.to_csv(filepath, index=True)
        print(f"[DataCollector] 数据已保存到 {filepath}")
