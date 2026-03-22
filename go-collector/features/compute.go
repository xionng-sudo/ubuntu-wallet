package features

import (
	"errors"
	"math"
	"time"

	"github.com/ubuntu-wallet/go-collector/models"
)

type TraderFlow struct {
	BuyRatio  float64
	SellRatio float64
	NetFlow   float64
}

// PickClosedCandle returns the last CLOSED candle.
// If the latest candle is too recent (< intervalDuration), we use the previous one.
func PickClosedCandle(klines []models.OHLCV, intervalDuration time.Duration, now time.Time) (models.OHLCV, []models.OHLCV, error) {
	if len(klines) < 2 {
		return models.OHLCV{}, nil, errors.New("not enough klines")
	}
	last := klines[len(klines)-1]
	if now.Sub(last.Timestamp) < intervalDuration {
		last = klines[len(klines)-2]
		klines = klines[:len(klines)-1]
	}
	return last, klines, nil
}

func pctChange(curr, prev float64) float64 {
	if prev == 0 {
		return 0
	}
	return (curr - prev) / prev
}

func safe(v float64) float64 {
	if math.IsNaN(v) || math.IsInf(v, 0) {
		return 0
	}
	return v
}

func closesHighsLowsVolumes(series []models.OHLCV) (closes, highs, lows, vols []float64) {
	closes = make([]float64, 0, len(series))
	highs = make([]float64, 0, len(series))
	lows = make([]float64, 0, len(series))
	vols = make([]float64, 0, len(series))
	for _, k := range series {
		closes = append(closes, k.Close)
		highs = append(highs, k.High)
		lows = append(lows, k.Low)
		vols = append(vols, k.Volume)
	}
	return
}

func rollingVolatility(closes []float64, n int) float64 {
	if len(closes) < n+1 {
		return 0
	}
	rets := make([]float64, 0, n)
	for i := len(closes) - n; i < len(closes); i++ {
		rets = append(rets, safe(pctChange(closes[i], closes[i-1])))
	}
	return safe(stddev(rets))
}

func lagReturn(closes []float64, lag int) float64 {
	if lag <= 0 || len(closes) < lag+1 {
		return 0
	}
	return safe(pctChange(closes[len(closes)-1], closes[len(closes)-1-lag]))
}

func lagVolume(vols []float64, lag int) float64 {
	if lag <= 0 || len(vols) < lag+1 {
		return 0
	}
	prev := vols[len(vols)-1-lag]
	if prev == 0 {
		return 0
	}
	return safe((vols[len(vols)-1] - prev) / prev)
}

func meanLast(vals []float64, n int) float64 {
	if n <= 0 || len(vals) < n {
		return 0
	}
	sum := 0.0
	for _, v := range vals[len(vals)-n:] {
		sum += safe(v)
	}
	return safe(sum / float64(n))
}

func stdLast(vals []float64, n int) float64 {
	if n <= 0 || len(vals) < n {
		return 0
	}
	return safe(stddev(vals[len(vals)-n:]))
}

func priceToMA(closes []float64, n int) float64 {
	if len(closes) < n || n <= 0 {
		return 0
	}
	ma := sma(closes, n)
	if ma == 0 {
		return 0
	}
	return safe((closes[len(closes)-1] - ma) / ma)
}

func itoa(i int) string {
	switch i {
	case 1:
		return "1"
	case 2:
		return "2"
	case 3:
		return "3"
	case 5:
		return "5"
	case 10:
		return "10"
	case 20:
		return "20"
	case 50:
		return "50"
	default:
		return "0"
	}
}

func stochD3(highs, lows, closes []float64, lookback int) float64 {
	if len(closes) < lookback+2 {
		return 0
	}
	k1 := stochK(highs[:len(highs)-2], lows[:len(lows)-2], closes[:len(closes)-2], lookback)
	k2 := stochK(highs[:len(highs)-1], lows[:len(lows)-1], closes[:len(closes)-1], lookback)
	k3 := stochK(highs, lows, closes, lookback)
	return safe((k1 + k2 + k3) / 3.0)
}

func mapBaseAndCore(raw map[string]float64, closes, highs, lows, vols []float64, open, high, low, close, volume float64, ts time.Time, flow TraderFlow) {
	m, sig, hist := macd(closes, 12, 26, 9)

	raw["sma_7"] = safe(sma(closes, 7))
	raw["sma_25"] = safe(sma(closes, 25))
	raw["sma_99"] = safe(sma(closes, 99))
	raw["sma_200"] = safe(sma(closes, 200))
	raw["ema_12"] = safe(ema(closes, 12))
	raw["ema_26"] = safe(ema(closes, 26))
	raw["ema_50"] = safe(ema(closes, 50))
	raw["ema_200"] = safe(ema(closes, 200))
	raw["macd"] = safe(m)
	raw["macd_signal"] = safe(sig)
	raw["macd_hist"] = safe(hist)

	raw["rsi"] = safe(rsi(closes, 14))
	raw["atr"] = safe(atr(highs, lows, closes, 14))

	raw["returns"] = safe(lagReturn(closes, 1))
	if len(closes) >= 2 && closes[len(closes)-2] > 0 && closes[len(closes)-1] > 0 {
		raw["log_returns"] = safe(math.Log(closes[len(closes)-1] / closes[len(closes)-2]))
	} else {
		raw["log_returns"] = 0
	}

	raw["price_range"] = safe((high - low) / math.Max(close, 1e-12))
	raw["body_size"] = safe((close - open) / math.Max(open, 1e-12))
	raw["upper_shadow"] = safe((high - math.Max(open, close)) / math.Max(close, 1e-12))
	raw["lower_shadow"] = safe((math.Min(open, close) - low) / math.Max(close, 1e-12))

	raw["volume_sma_20"] = safe(sma(vols, 20))
	if raw["volume_sma_20"] != 0 {
		raw["volume_ratio"] = safe(volume / raw["volume_sma_20"])
	} else {
		raw["volume_ratio"] = 0
	}

	n := 20
	if len(closes) < n {
		n = len(closes)
	}
	if n > 0 {
		num, den := 0.0, 0.0
		for i := len(closes) - n; i < len(closes); i++ {
			typ := (highs[i] + lows[i] + closes[i]) / 3.0
			v := vols[i]
			num += typ * v
			den += v
		}
		if den != 0 {
			raw["vwap"] = safe(num / den)
		}
	}

	obv := 0.0
	for i := 1; i < len(closes); i++ {
		if closes[i] > closes[i-1] {
			obv += vols[i]
		} else if closes[i] < closes[i-1] {
			obv -= vols[i]
		}
	}
	raw["obv"] = safe(obv)

	for _, l := range []int{1, 2, 3, 5, 10, 20} {
		raw["return_lag_"+itoa(l)] = safe(lagReturn(closes, l))
		raw["volume_lag_"+itoa(l)] = safe(lagVolume(vols, l))
	}
	for _, w := range []int{5, 10, 20, 50} {
		raw["rolling_mean_"+itoa(w)] = safe(meanLast(closes, w))
		raw["rolling_std_"+itoa(w)] = safe(stdLast(closes, w))
		raw["rolling_vol_mean_"+itoa(w)] = safe(meanLast(vols, w))
		raw["price_to_ma_"+itoa(w)] = safe(priceToMA(closes, w))
	}
	raw["volatility_5"] = safe(rollingVolatility(closes, 5))
	raw["volatility_20"] = safe(rollingVolatility(closes, 20))
	if raw["volatility_20"] != 0 {
		raw["volatility_ratio"] = safe(raw["volatility_5"] / raw["volatility_20"])
	}

	raw["hour"] = float64(ts.UTC().Hour())
	raw["day_of_week"] = float64(ts.UTC().Weekday())
	if ts.UTC().Weekday() == time.Saturday || ts.UTC().Weekday() == time.Sunday {
		raw["is_weekend"] = 1
	} else {
		raw["is_weekend"] = 0
	}

	raw["roc_12"] = safe(roc(closes, 12))
	raw["roc_24"] = safe(roc(closes, 24))
	raw["stoch_k"] = safe(stochK(highs, lows, closes, 14))
	raw["stoch_d"] = safe(stochD3(highs, lows, closes, 14))
	raw["williams_r"] = safe(williamsR(highs, lows, closes, 14))
	raw["cci"] = safe(cci(highs, lows, closes, 20))

	bu, bm, bl, bw, bp := bollinger(closes, 20, 2.0)
	raw["bb_upper"] = bu
	raw["bb_middle"] = bm
	raw["bb_lower"] = bl
	raw["bb_width"] = bw
	raw["bb_pct"] = bp

	km := safe(ema(closes, 20))
	av := safe(atr(highs, lows, closes, 14))
	raw["keltner_middle"] = km
	raw["keltner_upper"] = safe(km + 2*av)
	raw["keltner_lower"] = safe(km - 2*av)

	adxV, pdi, mdi := adxDI(highs, lows, closes, 14)
	raw["adx"] = adxV
	raw["plus_di"] = pdi
	raw["minus_di"] = mdi
	raw["mfi"] = safe(mfi(highs, lows, closes, vols, 14))

	t, kjn, sa, sb := ichimoku(highs, lows)
	raw["ichimoku_tenkan"] = t
	raw["ichimoku_kijun"] = kjn
	raw["ichimoku_senkou_a"] = sa
	raw["ichimoku_senkou_b"] = sb
	raw["parabolic_sar"] = safe(parabolicSAR(highs, lows, 0.02, 0.2))

	raw["trader_buy_ratio"] = safe(flow.BuyRatio)
	raw["trader_sell_ratio"] = safe(flow.SellRatio)
	raw["trader_net_flow"] = safe(flow.NetFlow)
}

func mapTF(prefix string, raw map[string]float64, series []models.OHLCV, ts time.Time, flow TraderFlow) {
	if len(series) < 2 {
		return
	}
	closes, highs, lows, vols := closesHighsLowsVolumes(series)
	last := series[len(series)-1]

	raw[prefix+"close"] = safe(last.Close)
	raw[prefix+"open"] = safe(last.Open)
	raw[prefix+"high"] = safe(last.High)
	raw[prefix+"low"] = safe(last.Low)
	raw[prefix+"volume"] = safe(last.Volume)

	raw[prefix+"sma_7"] = safe(sma(closes, 7))
	raw[prefix+"sma_25"] = safe(sma(closes, 25))
	raw[prefix+"sma_99"] = safe(sma(closes, 99))
	raw[prefix+"sma_200"] = safe(sma(closes, 200))
	raw[prefix+"ema_12"] = safe(ema(closes, 12))
	raw[prefix+"ema_26"] = safe(ema(closes, 26))
	raw[prefix+"ema_50"] = safe(ema(closes, 50))
	raw[prefix+"ema_200"] = safe(ema(closes, 200))

	m, sig, hist := macd(closes, 12, 26, 9)
	raw[prefix+"macd"] = safe(m)
	raw[prefix+"macd_signal"] = safe(sig)
	raw[prefix+"macd_hist"] = safe(hist)
	raw[prefix+"rsi"] = safe(rsi(closes, 14))
	raw[prefix+"atr"] = safe(atr(highs, lows, closes, 14))

	raw[prefix+"returns"] = safe(lagReturn(closes, 1))
	if len(closes) >= 2 && closes[len(closes)-2] > 0 && closes[len(closes)-1] > 0 {
		raw[prefix+"log_returns"] = safe(math.Log(closes[len(closes)-1] / closes[len(closes)-2]))
	} else {
		raw[prefix+"log_returns"] = 0
	}

	raw[prefix+"price_range"] = safe((last.High - last.Low) / math.Max(last.Close, 1e-12))
	raw[prefix+"body_size"] = safe((last.Close - last.Open) / math.Max(last.Open, 1e-12))
	raw[prefix+"upper_shadow"] = safe((last.High - math.Max(last.Open, last.Close)) / math.Max(last.Close, 1e-12))
	raw[prefix+"lower_shadow"] = safe((math.Min(last.Open, last.Close) - last.Low) / math.Max(last.Close, 1e-12))

	raw[prefix+"volume_sma_20"] = safe(sma(vols, 20))
	if raw[prefix+"volume_sma_20"] != 0 {
		raw[prefix+"volume_ratio"] = safe(last.Volume / raw[prefix+"volume_sma_20"])
	} else {
		raw[prefix+"volume_ratio"] = 0
	}

	obv := 0.0
	for i := 1; i < len(closes); i++ {
		if closes[i] > closes[i-1] {
			obv += vols[i]
		} else if closes[i] < closes[i-1] {
			obv -= vols[i]
		}
	}
	raw[prefix+"obv"] = safe(obv)

	n := 20
	if len(closes) < n {
		n = len(closes)
	}
	if n > 0 {
		num, den := 0.0, 0.0
		for i := len(closes) - n; i < len(closes); i++ {
			typ := (highs[i] + lows[i] + closes[i]) / 3.0
			num += typ * vols[i]
			den += vols[i]
		}
		if den != 0 {
			raw[prefix+"vwap"] = safe(num / den)
		}
	}

	for _, l := range []int{1, 2, 3, 5, 10, 20} {
		raw[prefix+"return_lag_"+itoa(l)] = safe(lagReturn(closes, l))
		raw[prefix+"volume_lag_"+itoa(l)] = safe(lagVolume(vols, l))
	}
	for _, w := range []int{5, 10, 20, 50} {
		raw[prefix+"rolling_mean_"+itoa(w)] = safe(meanLast(closes, w))
		raw[prefix+"rolling_std_"+itoa(w)] = safe(stdLast(closes, w))
		raw[prefix+"rolling_vol_mean_"+itoa(w)] = safe(meanLast(vols, w))
		raw[prefix+"price_to_ma_"+itoa(w)] = safe(priceToMA(closes, w))
	}
	raw[prefix+"volatility_5"] = safe(rollingVolatility(closes, 5))
	raw[prefix+"volatility_20"] = safe(rollingVolatility(closes, 20))
	if raw[prefix+"volatility_20"] != 0 {
		raw[prefix+"volatility_ratio"] = safe(raw[prefix+"volatility_5"] / raw[prefix+"volatility_20"])
	}

	raw[prefix+"hour"] = float64(ts.UTC().Hour())
	raw[prefix+"day_of_week"] = float64(ts.UTC().Weekday())
	if ts.UTC().Weekday() == time.Saturday || ts.UTC().Weekday() == time.Sunday {
		raw[prefix+"is_weekend"] = 1
	} else {
		raw[prefix+"is_weekend"] = 0
	}

	raw[prefix+"roc_12"] = safe(roc(closes, 12))
	raw[prefix+"roc_24"] = safe(roc(closes, 24))
	raw[prefix+"stoch_k"] = safe(stochK(highs, lows, closes, 14))
	raw[prefix+"stoch_d"] = safe(stochD3(highs, lows, closes, 14))
	raw[prefix+"williams_r"] = safe(williamsR(highs, lows, closes, 14))
	raw[prefix+"cci"] = safe(cci(highs, lows, closes, 20))

	bu, bm, bl, bw, bp := bollinger(closes, 20, 2.0)
	raw[prefix+"bb_upper"] = bu
	raw[prefix+"bb_middle"] = bm
	raw[prefix+"bb_lower"] = bl
	raw[prefix+"bb_width"] = bw
	raw[prefix+"bb_pct"] = bp

	km := safe(ema(closes, 20))
	av := safe(atr(highs, lows, closes, 14))
	raw[prefix+"keltner_middle"] = km
	raw[prefix+"keltner_upper"] = safe(km + 2*av)
	raw[prefix+"keltner_lower"] = safe(km - 2*av)

	adxV, pdi, mdi := adxDI(highs, lows, closes, 14)
	raw[prefix+"adx"] = adxV
	raw[prefix+"plus_di"] = pdi
	raw[prefix+"minus_di"] = mdi
	raw[prefix+"mfi"] = safe(mfi(highs, lows, closes, vols, 14))

	t, kjn, sa, sb := ichimoku(highs, lows)
	raw[prefix+"ichimoku_tenkan"] = t
	raw[prefix+"ichimoku_kijun"] = kjn
	raw[prefix+"ichimoku_senkou_a"] = sa
	raw[prefix+"ichimoku_senkou_b"] = sb
	raw[prefix+"parabolic_sar"] = safe(parabolicSAR(highs, lows, 0.02, 0.2))

	// tf signal score true value (simple rule-based)
	score := 0.0
	if raw[prefix+"ema_12"] > raw[prefix+"ema_26"] {
		score += 0.4
	}
	if raw[prefix+"rsi"] > 50 {
		score += 0.3
	}
	if raw[prefix+"macd"] > raw[prefix+"macd_signal"] {
		score += 0.3
	}
	raw[prefix+"signal_score"] = safe(score)

	raw[prefix+"trader_buy_ratio"] = safe(flow.BuyRatio)
	raw[prefix+"trader_sell_ratio"] = safe(flow.SellRatio)
	raw[prefix+"trader_net_flow"] = safe(flow.NetFlow)
}

func ComputeSnapshot(
	symbol string,
	interval string,
	klines1h []models.OHLCV,
	klines4h []models.OHLCV,
	klines1d []models.OHLCV,
	featureCols []string,
	flow1h TraderFlow,
	flow4h TraderFlow,
	flow1d TraderFlow,
	now time.Time,
) (*FeatureSnapshot, error) {
	closed1h, series1h, err := PickClosedCandle(klines1h, time.Hour, now)
	if err != nil {
		return nil, err
	}

	closes1h, highs1h, lows1h, vols1h := closesHighsLowsVolumes(series1h)
	raw := make(map[string]float64, 512)
	mapBaseAndCore(raw, closes1h, highs1h, lows1h, vols1h, closed1h.Open, closed1h.High, closed1h.Low, closed1h.Close, closed1h.Volume, closed1h.Timestamp.UTC(), flow1h)

	if len(klines4h) >= 2 {
		if closed4h, series4h, e := PickClosedCandle(klines4h, 4*time.Hour, now); e == nil {
			mapTF("tf4h_", raw, series4h, closed4h.Timestamp.UTC(), flow4h)
		}
	}
	if len(klines1d) >= 2 {
		if closed1d, series1d, e := PickClosedCandle(klines1d, 24*time.Hour, now); e == nil {
			mapTF("tf1d_", raw, series1d, closed1d.Timestamp.UTC(), flow1d)
		}
	}

	aligned, computed, missing := AlignToSchema(raw, featureCols)

	m := aligned["macd"]
	ms := aligned["macd_signal"]
	mh := aligned["macd_hist"]
	rsiVal := aligned["rsi"]
	atrVal := aligned["atr"]
	vol20 := aligned["volatility_20"]

	snap := &FeatureSnapshot{
		Symbol:    symbol,
		Interval:  interval,
		FeatureTS: closed1h.Timestamp.UTC(),

		Open:   safe(closed1h.Open),
		High:   safe(closed1h.High),
		Low:    safe(closed1h.Low),
		Close:  safe(closed1h.Close),
		Volume: safe(closed1h.Volume),

		Ret1H: safe(lagReturn(closes1h, 1)),
		Ret4H: safe(lagReturn(closes1h, 4)),

		SMA7:   aligned["sma_7"],
		SMA25:  aligned["sma_25"],
		SMA99:  aligned["sma_99"],
		SMA200: aligned["sma_200"],

		EMA12:  aligned["ema_12"],
		EMA26:  aligned["ema_26"],
		EMA50:  aligned["ema_50"],
		EMA200: aligned["ema_200"],

		MACD:       m,
		MACDSignal: ms,
		MACDHist:   mh,

		RSI14:        rsiVal,
		ATR14:        atrVal,
		Volatility20: vol20,

		Features:      aligned,
		SchemaColumns: len(featureCols),
		ComputedCols:  computed,
		MissingCols:   missing,
	}

	if len(klines4h) >= 2 {
		closed4h, series4h, e := PickClosedCandle(klines4h, 4*time.Hour, now)
		if e == nil {
			cl4, _, _, _ := closesHighsLowsVolumes(series4h)
			snap.Filter4H.FeatureTS = closed4h.Timestamp.UTC()
			snap.Filter4H.Close = safe(closed4h.Close)
			snap.Filter4H.EMA200 = safe(ema(cl4, 200))
			snap.Filter4H.RSI14 = safe(rsi(cl4, 14))
		}
	}

	return snap, nil
}
