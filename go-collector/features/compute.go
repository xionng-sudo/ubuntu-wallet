package features

import (
	"errors"
	"math"
	"time"

	"github.com/ubuntu-wallet/go-collector/models"
)

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

func ComputeSnapshot(symbol string, interval string, klines1h []models.OHLCV, klines4h []models.OHLCV, now time.Time) (*FeatureSnapshot, error) {
	closed1h, series1h, err := PickClosedCandle(klines1h, time.Hour, now)
	if err != nil {
		return nil, err
	}

	closes := make([]float64, 0, len(series1h))
	highs := make([]float64, 0, len(series1h))
	lows := make([]float64, 0, len(series1h))
	for _, k := range series1h {
		closes = append(closes, k.Close)
		highs = append(highs, k.High)
		lows = append(lows, k.Low)
	}

	ret1h := 0.0
	if len(closes) >= 2 {
		ret1h = pctChange(closes[len(closes)-1], closes[len(closes)-2])
	}

	ret4h := 0.0
	if len(closes) >= 5 {
		ret4h = pctChange(closes[len(closes)-1], closes[len(closes)-5])
	}

	vol20 := 0.0
	if len(closes) >= 21 {
		rets := make([]float64, 0, 20)
		for i := len(closes) - 20; i < len(closes); i++ {
			r := pctChange(closes[i], closes[i-1])
			if math.IsNaN(r) || math.IsInf(r, 0) {
				r = 0
			}
			rets = append(rets, r)
		}
		vol20 = stddev(rets)
	}

	m, sig, hist := macd(closes, 12, 26, 9)

	snap := &FeatureSnapshot{
		Symbol:    symbol,
		Interval:  interval,
		FeatureTS: closed1h.Timestamp.UTC(),

		Open:   closed1h.Open,
		High:   closed1h.High,
		Low:    closed1h.Low,
		Close:  closed1h.Close,
		Volume: closed1h.Volume,

		Ret1H: ret1h,
		Ret4H: ret4h,

		// meta-compatible
		SMA7:   sma(closes, 7),
		SMA25:  sma(closes, 25),
		SMA99:  sma(closes, 99),
		SMA200: sma(closes, 200),

		EMA12:  ema(closes, 12),
		EMA26:  ema(closes, 26),
		EMA50:  ema(closes, 50),
		EMA200: ema(closes, 200),

		MACD:       m,
		MACDSignal: sig,

		// extras
		RSI14:        rsi(closes, 14),
		MACDHist:     hist,
		Volatility20: vol20,
		ATR14:        atr(highs, lows, closes, 14),
	}

	// 4h filter
	if len(klines4h) >= 2 {
		closed4h, series4h, e := PickClosedCandle(klines4h, 4*time.Hour, now)
		if e == nil {
			cl4 := make([]float64, 0, len(series4h))
			for _, k := range series4h {
				cl4 = append(cl4, k.Close)
			}
			snap.Filter4H.FeatureTS = closed4h.Timestamp.UTC()
			snap.Filter4H.Close = closed4h.Close
			snap.Filter4H.EMA200 = ema(cl4, 200)
			snap.Filter4H.RSI14 = rsi(cl4, 14)
		}
	}

	return snap, nil
}
