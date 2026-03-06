"""
ETH Crypto Prediction System - Configuration
"""
import os
from dotenv import load_dotenv

load_dotenv()

# === 交易所 API 配置 ===
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
OKX_API_KEY = os.getenv("OKX_API_KEY", "")
OKX_API_SECRET = os.getenv("OKX_API_SECRET", "")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE", "")
COINBASE_API_KEY = os.getenv("COINBASE_API_KEY", "")
COINBASE_API_SECRET = os.getenv("COINBASE_API_SECRET", "")

# === Go Collector API ===
COLLECTOR_API_URL = os.getenv("COLLECTOR_API_URL", "http://localhost:8080")

# === 数据配置 ===
DATA_DIR = os.getenv("DATA_DIR", "../data")
MODEL_DIR = os.getenv("MODEL_DIR", "../models")
SYMBOL = "ETHUSDT"
TOP_TRADERS = 50
TRADE_HISTORY = 100

# === 技术分析参数 ===
TA_CONFIG = {
    "sma_periods": [7, 25, 99, 200],
    "ema_periods": [12, 26, 50, 200],
    "rsi_period": 14,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "bb_period": 20,
    "bb_std": 2,
    "atr_period": 14,
    "stoch_k": 14,
    "stoch_d": 3,
    "adx_period": 14,
    "cci_period": 20,
    "williams_r_period": 14,
    "ichimoku_tenkan": 9,
    "ichimoku_kijun": 26,
    "ichimoku_senkou_b": 52,
}

# === 机器学习参数 ===
ML_CONFIG = {
    "train_test_split": 0.8,
    "lookback_period": 60,        # 回看60个周期
    "prediction_horizon": [1, 4, 12, 24],  # 预测1h, 4h, 12h, 24h
    "lstm_units": 128,
    "lstm_layers": 3,
    "dropout": 0.2,
    "batch_size": 32,
    "epochs": 100,
    "learning_rate": 0.001,
    "early_stopping_patience": 10,
    "xgboost_n_estimators": 500,
    "xgboost_max_depth": 8,
    "xgboost_learning_rate": 0.05,
    "lightgbm_n_estimators": 500,
    "lightgbm_max_depth": 8,
    "lightgbm_learning_rate": 0.05,
}

# === 提醒配置 ===
ALERT_CONFIG = {
    "price_change_threshold": 3.0,    # 价格变化>3%触发提醒
    "volume_spike_threshold": 2.0,    # 成交量超过均值2倍触发
    "rsi_overbought": 70,
    "rsi_oversold": 30,
    "confidence_threshold": 0.7,      # 预测置信度>70%才发送信号
    "check_interval_seconds": 60,     # 每60秒检查一次
}

# === 可视化配置 ===
VIS_CONFIG = {
    "dash_host": "0.0.0.0",
    "dash_port": 8050,
    "refresh_interval": 30000,        # 30秒刷新
    "chart_height": 600,
    "theme": "plotly_dark",
}

# 确保目录存在
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(MODEL_DIR, exist_ok=True)
