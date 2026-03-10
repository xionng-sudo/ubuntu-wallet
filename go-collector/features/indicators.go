package features

import "math"

func sma(values []float64, period int) float64 {
	if period <= 0 || len(values) < period {
		return 0
	}
	start := len(values) - period
	var sum float64
	for i := start; i < len(values); i++ {
		sum += values[i]
	}
	return sum / float64(period)
}

func ema(values []float64, period int) float64 {
	if period <= 0 || len(values) == 0 {
		return 0
	}
	alpha := 2.0 / float64(period+1)
	e := values[0]
	for i := 1; i < len(values); i++ {
		e = alpha*values[i] + (1-alpha)*e
	}
	return e
}

func rsi(closes []float64, period int) float64 {
	if period <= 0 || len(closes) < period+1 {
		return 0
	}
	var gain, loss float64
	start := len(closes) - (period + 1)
	for i := start + 1; i < len(closes); i++ {
		d := closes[i] - closes[i-1]
		if d > 0 {
			gain += d
		} else {
			loss += -d
		}
	}
	avgGain := gain / float64(period)
	avgLoss := loss / float64(period)
	if avgLoss == 0 {
		return 100
	}
	rs := avgGain / avgLoss
	return 100 - (100 / (1 + rs))
}

func stddev(vals []float64) float64 {
	if len(vals) == 0 {
		return 0
	}
	var sum float64
	for _, v := range vals {
		sum += v
	}
	mean := sum / float64(len(vals))
	var ss float64
	for _, v := range vals {
		d := v - mean
		ss += d * d
	}
	return math.Sqrt(ss / float64(len(vals)))
}

func macd(closes []float64, fast, slow, signal int) (m, s, h float64) {
	if len(closes) == 0 {
		return 0, 0, 0
	}
	emaFast := ema(closes, fast)
	emaSlow := ema(closes, slow)
	m = emaFast - emaSlow

	// Build a short series of MACD values for signal EMA
	n := len(closes)
	start := 0
	if n > slow*3 {
		start = n - slow*3
	}
	macdSeries := make([]float64, 0, n-start)
	for i := start; i < n; i++ {
		sub := closes[:i+1]
		macdSeries = append(macdSeries, ema(sub, fast)-ema(sub, slow))
	}
	s = ema(macdSeries, signal)
	h = m - s
	return
}

func atr(highs, lows, closes []float64, period int) float64 {
	if period <= 0 || len(highs) == 0 || len(lows) == 0 || len(closes) == 0 {
		return 0
	}
	n := len(closes)
	if n < period+1 {
		return 0
	}
	trs := make([]float64, 0, period)
	start := n - period
	if start < 1 {
		start = 1
	}
	for i := start; i < n; i++ {
		hl := highs[i] - lows[i]
		hc := math.Abs(highs[i] - closes[i-1])
		lc := math.Abs(lows[i] - closes[i-1])
		tr := hl
		if hc > tr {
			tr = hc
		}
		if lc > tr {
			tr = lc
		}
		trs = append(trs, tr)
	}
	var sum float64
	for _, v := range trs {
		sum += v
	}
	return sum / float64(len(trs))
}
