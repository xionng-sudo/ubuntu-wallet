"""
ETH Crypto Prediction System - 技术分析模块
包含多种经典技术指标和高级分析工具
"""
import numpy as np
import pandas as pd

import config

TA_CFG = config.TA_CONFIG


class TechnicalAnalyzer:
    """全面的技术分析引擎"""

    def __init__(self):
        self.signals = []

    def analyze(self, df: pd.DataFrame) -> pd.DataFrame:
        """对K线数据执行全部技术分析"""
        if df.empty or len(df) < 30:
            print("[TA] 数据不足，无法进行技术分析")
            return df

        df = df.copy()

        # ─── 趋势指标 ───
        df = self._calc_sma(df)
        df = self._calc_ema(df)
        df = self._calc_macd(df)
        df = self._calc_adx(df)
        df = self._calc_ichimoku(df)
        df = self._calc_parabolic_sar(df)

        # ─── 动量指标 ───
        df = self._calc_rsi(df)
        df = self._calc_stochastic(df)
        df = self._calc_williams_r(df)
        df = self._calc_cci(df)
        df = self._calc_roc(df)
        df = self._calc_mfi(df)

        # ─── 波动率指标 ───
        df = self._calc_bollinger_bands(df)
        df = self._calc_atr(df)
        df = self._calc_keltner_channel(df)

        # ─── 成交量指标 ───
        df = self._calc_obv(df)
        df = self._calc_vwap(df)
        df = self._calc_volume_profile(df)

        # ─── 综合信号 ───
        df = self._generate_signals(df)

        return df

    # ================================================================
    # 趋势指标
    # ================================================================

    def _calc_sma(self, df: pd.DataFrame) -> pd.DataFrame:
        """简单移动平均线 (SMA)"""
        for period in TA_CFG["sma_periods"]:
            df[f"sma_{period}"] = df["close"].rolling(window=period).mean()
        return df

    def _calc_ema(self, df: pd.DataFrame) -> pd.DataFrame:
        """指数移动平均线 (EMA)"""
        for period in TA_CFG["ema_periods"]:
            df[f"ema_{period}"] = df["close"].ewm(span=period, adjust=False).mean()
        return df

    def _calc_macd(self, df: pd.DataFrame) -> pd.DataFrame:
        """MACD 指标"""
        fast = TA_CFG["macd_fast"]
        slow = TA_CFG["macd_slow"]
        signal = TA_CFG["macd_signal"]

        ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=slow, adjust=False).mean()

        df["macd"] = ema_fast - ema_slow
        df["macd_signal"] = df["macd"].ewm(span=signal, adjust=False).mean()
        df["macd_hist"] = df["macd"] - df["macd_signal"]

        return df

    def _calc_adx(self, df: pd.DataFrame) -> pd.DataFrame:
        """平均趋向指数 (ADX)"""
        period = TA_CFG["adx_period"]

        high = df["high"]
        low = df["low"]
        close = df["close"]

        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0

        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        atr = tr.rolling(window=period).mean()
        plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr)

        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)
        df["adx"] = dx.rolling(window=period).mean()
        df["plus_di"] = plus_di
        df["minus_di"] = minus_di

        return df

    def _calc_ichimoku(self, df: pd.DataFrame) -> pd.DataFrame:
        """一目均衡表 (Ichimoku Cloud)"""
        tenkan = TA_CFG["ichimoku_tenkan"]
        kijun = TA_CFG["ichimoku_kijun"]
        senkou_b = TA_CFG["ichimoku_senkou_b"]

        high = df["high"]
        low = df["low"]

        # 转换线
        df["ichimoku_tenkan"] = (high.rolling(tenkan).max() + low.rolling(tenkan).min()) / 2
        # 基准线
        df["ichimoku_kijun"] = (high.rolling(kijun).max() + low.rolling(kijun).min()) / 2
        # 先行带A
        df["ichimoku_senkou_a"] = ((df["ichimoku_tenkan"] + df["ichimoku_kijun"]) / 2).shift(kijun)
        # 先行带B
        df["ichimoku_senkou_b"] = ((high.rolling(senkou_b).max() + low.rolling(senkou_b).min()) / 2).shift(kijun)
        # 迟行带
        df["ichimoku_chikou"] = df["close"].shift(-kijun)

        return df

    def _calc_parabolic_sar(self, df: pd.DataFrame) -> pd.DataFrame:
        """抛物线SAR"""
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values
        n = len(close)

        sar = np.zeros(n)
        af = 0.02
        max_af = 0.20
        trend = 1  # 1 = up, -1 = down
        ep = high[0]
        sar[0] = low[0]

        for i in range(1, n):
            if trend == 1:
                sar[i] = sar[i - 1] + af * (ep - sar[i - 1])
                sar[i] = min(sar[i], low[i - 1])
                if i >= 2:
                    sar[i] = min(sar[i], low[i - 2])

                if high[i] > ep:
                    ep = high[i]
                    af = min(af + 0.02, max_af)

                if low[i] < sar[i]:
                    trend = -1
                    sar[i] = ep
                    ep = low[i]
                    af = 0.02
            else:
                sar[i] = sar[i - 1] + af * (ep - sar[i - 1])
                sar[i] = max(sar[i], high[i - 1])
                if i >= 2:
                    sar[i] = max(sar[i], high[i - 2])

                if low[i] < ep:
                    ep = low[i]
                    af = min(af + 0.02, max_af)

                if high[i] > sar[i]:
                    trend = 1
                    sar[i] = ep
                    ep = high[i]
                    af = 0.02

        df["parabolic_sar"] = sar
        return df

    # ================================================================
    # 动量指标
    # ================================================================

    def _calc_rsi(self, df: pd.DataFrame) -> pd.DataFrame:
        """相对强弱指数 (RSI)"""
        period = TA_CFG["rsi_period"]
        delta = df["close"].diff()

        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)

        avg_gain = gain.rolling(window=period).mean()
        avg_loss = loss.rolling(window=period).mean()

        rs = avg_gain / avg_loss.replace(0, np.nan)
        df["rsi"] = 100 - (100 / (1 + rs))

        return df

    def _calc_stochastic(self, df: pd.DataFrame) -> pd.DataFrame:
        """随机振荡器 (Stochastic Oscillator)"""
        k_period = TA_CFG["stoch_k"]
        d_period = TA_CFG["stoch_d"]

        low_min = df["low"].rolling(window=k_period).min()
        high_max = df["high"].rolling(window=k_period).max()

        df["stoch_k"] = 100 * (df["close"] - low_min) / (high_max - low_min).replace(0, np.nan)
        df["stoch_d"] = df["stoch_k"].rolling(window=d_period).mean()

        return df

    def _calc_williams_r(self, df: pd.DataFrame) -> pd.DataFrame:
        """威廉指标 (Williams %R)"""
        period = TA_CFG["williams_r_period"]

        high_max = df["high"].rolling(window=period).max()
        low_min = df["low"].rolling(window=period).min()

        df["williams_r"] = -100 * (high_max - df["close"]) / (high_max - low_min).replace(0, np.nan)

        return df

    def _calc_cci(self, df: pd.DataFrame) -> pd.DataFrame:
        """商品频道指数 (CCI)"""
        period = TA_CFG["cci_period"]

        tp = (df["high"] + df["low"] + df["close"]) / 3
        ma = tp.rolling(window=period).mean()
        mad = tp.rolling(window=period).apply(lambda x: np.mean(np.abs(x - np.mean(x))))

        df["cci"] = (tp - ma) / (0.015 * mad.replace(0, np.nan))

        return df

    def _calc_roc(self, df: pd.DataFrame) -> pd.DataFrame:
        """变动率 (Rate of Change)"""
        df["roc_12"] = df["close"].pct_change(periods=12) * 100
        df["roc_24"] = df["close"].pct_change(periods=24) * 100
        return df

    def _calc_mfi(self, df: pd.DataFrame) -> pd.DataFrame:
        """资金流量指数 (Money Flow Index)"""
        period = 14
        tp = (df["high"] + df["low"] + df["close"]) / 3
        raw_mf = tp * df["volume"]

        mf_pos = raw_mf.where(tp > tp.shift(1), 0.0)
        mf_neg = raw_mf.where(tp < tp.shift(1), 0.0)

        mf_pos_sum = mf_pos.rolling(window=period).sum()
        mf_neg_sum = mf_neg.rolling(window=period).sum()

        mfr = mf_pos_sum / mf_neg_sum.replace(0, np.nan)
        df["mfi"] = 100 - (100 / (1 + mfr))

        return df

    # ================================================================
    # 波动率指标
    # ================================================================

    def _calc_bollinger_bands(self, df: pd.DataFrame) -> pd.DataFrame:
        """布林带 (Bollinger Bands)"""
        period = TA_CFG["bb_period"]
        std_dev = TA_CFG["bb_std"]

        sma = df["close"].rolling(window=period).mean()
        std = df["close"].rolling(window=period).std()

        df["bb_upper"] = sma + std_dev * std
        df["bb_middle"] = sma
        df["bb_lower"] = sma - std_dev * std
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / sma.replace(0, np.nan)
        df["bb_pct"] = (df["close"] - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"]).replace(0, np.nan)

        return df

    def _calc_atr(self, df: pd.DataFrame) -> pd.DataFrame:
        """真实波幅 (ATR)"""
        period = TA_CFG["atr_period"]

        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        df["atr"] = tr.rolling(window=period).mean()

        return df

    def _calc_keltner_channel(self, df: pd.DataFrame) -> pd.DataFrame:
        """肯特纳通道 (Keltner Channel)"""
        ema_20 = df["close"].ewm(span=20, adjust=False).mean()
        atr = df.get("atr", df["close"].rolling(14).std())

        df["keltner_upper"] = ema_20 + 2 * atr
        df["keltner_middle"] = ema_20
        df["keltner_lower"] = ema_20 - 2 * atr

        return df

    # ================================================================
    # 成交量指标
    # ================================================================

    def _calc_obv(self, df: pd.DataFrame) -> pd.DataFrame:
        """能量潮 (On-Balance Volume)"""
        direction = np.where(df["close"] > df["close"].shift(1), 1,
                             np.where(df["close"] < df["close"].shift(1), -1, 0))
        df["obv"] = (df["volume"] * direction).cumsum()
        return df

    def _calc_vwap(self, df: pd.DataFrame) -> pd.DataFrame:
        """成交量加权平均价格 (VWAP)"""
        tp = (df["high"] + df["low"] + df["close"]) / 3
        cumvol = df["volume"].cumsum()
        cumtp = (tp * df["volume"]).cumsum()
        df["vwap"] = cumtp / cumvol
        return df

    def _calc_volume_profile(self, df: pd.DataFrame) -> pd.DataFrame:
        """成交量分布"""
        df["volume_sma_20"] = df["volume"].rolling(window=20).mean()
        df["volume_ratio"] = df["volume"] / df["volume_sma_20"]
        return df

    # ================================================================
    # 信号生成
    # ================================================================

    def _generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """综合多个指标生成交易信号"""
        df["signal_score"] = 0.0

        # RSI 信号
        if "rsi" in df.columns:
            df.loc[df["rsi"] < 30, "signal_score"] += 1  # 超卖 = 买入信号
            df.loc[df["rsi"] > 70, "signal_score"] -= 1  # 超买 = 卖出信号
            df.loc[(df["rsi"] > 40) & (df["rsi"] < 60), "signal_score"] += 0  # 中性

        # MACD 信号
        if "macd" in df.columns and "macd_signal" in df.columns:
            df.loc[df["macd"] > df["macd_signal"], "signal_score"] += 1
            df.loc[df["macd"] < df["macd_signal"], "signal_score"] -= 1
            # MACD 交叉
            macd_cross = (df["macd"] > df["macd_signal"]) & (df["macd"].shift(1) <= df["macd_signal"].shift(1))
            df.loc[macd_cross, "signal_score"] += 2

        # 布林带信号
        if "bb_lower" in df.columns:
            df.loc[df["close"] < df["bb_lower"], "signal_score"] += 1  # 接近下轨
            df.loc[df["close"] > df["bb_upper"], "signal_score"] -= 1  # 接近上轨

        # 均线交叉
        if "ema_12" in df.columns and "ema_26" in df.columns:
            golden_cross = (df["ema_12"] > df["ema_26"]) & (df["ema_12"].shift(1) <= df["ema_26"].shift(1))
            death_cross = (df["ema_12"] < df["ema_26"]) & (df["ema_12"].shift(1) >= df["ema_26"].shift(1))
            df.loc[golden_cross, "signal_score"] += 2
            df.loc[death_cross, "signal_score"] -= 2

        # ADX 趋势强度
        if "adx" in df.columns:
            df.loc[df["adx"] > 25, "signal_score"] *= 1.5  # 强趋势放大信号

        # Stochastic
        if "stoch_k" in df.columns:
            df.loc[df["stoch_k"] < 20, "signal_score"] += 0.5
            df.loc[df["stoch_k"] > 80, "signal_score"] -= 0.5

        # 成交量确认
        if "volume_ratio" in df.columns:
            df.loc[df["volume_ratio"] > 2.0, "signal_score"] *= 1.3  # 放量确认

        # CCI 信号
        if "cci" in df.columns:
            df.loc[df["cci"] < -100, "signal_score"] += 0.5
            df.loc[df["cci"] > 100, "signal_score"] -= 0.5

        # 归一化信号到 [-10, 10]
        max_score = df["signal_score"].abs().max()
        if max_score > 0:
            df["signal_score"] = df["signal_score"] / max_score * 10

        # 最终信号
        df["signal"] = "HOLD"
        df.loc[df["signal_score"] > 3, "signal"] = "BUY"
        df.loc[df["signal_score"] > 6, "signal"] = "STRONG_BUY"
        df.loc[df["signal_score"] < -3, "signal"] = "SELL"
        df.loc[df["signal_score"] < -6, "signal"] = "STRONG_SELL"

        return df

    def get_latest_analysis(self, df: pd.DataFrame) -> dict:
        """获取最新一行的分析摘要"""
        if df.empty:
            return {}

        latest = df.iloc[-1]
        result = {
            "timestamp": str(latest.name) if hasattr(latest, "name") else "",
            "price": latest.get("close", 0),
            "signal": latest.get("signal", "HOLD"),
            "signal_score": latest.get("signal_score", 0),
            "rsi": latest.get("rsi", 0),
            "macd": latest.get("macd", 0),
            "macd_signal": latest.get("macd_signal", 0),
            "macd_hist": latest.get("macd_hist", 0),
            "bb_upper": latest.get("bb_upper", 0),
            "bb_lower": latest.get("bb_lower", 0),
            "adx": latest.get("adx", 0),
            "atr": latest.get("atr", 0),
            "obv": latest.get("obv", 0),
            "volume_ratio": latest.get("volume_ratio", 0),
        }

        for key in result:
            if isinstance(result[key], (np.floating, np.integer)):
                result[key] = float(result[key])

        return result

    def get_support_resistance(self, df: pd.DataFrame, window: int = 20) -> dict:
        """计算支撑位和阻力位"""
        if df.empty or len(df) < window:
            return {"support": [], "resistance": []}

        highs = df["high"].rolling(window=window, center=True).max()
        lows = df["low"].rolling(window=window, center=True).min()

        resistance_levels = df.loc[df["high"] == highs, "high"].dropna().unique()
        support_levels = df.loc[df["low"] == lows, "low"].dropna().unique()

        # 取最近的5个
        resistance = sorted(resistance_levels, reverse=True)[:5]
        support = sorted(support_levels)[:5]

        return {
            "support": [float(s) for s in support],
            "resistance": [float(r) for r in resistance],
        }
