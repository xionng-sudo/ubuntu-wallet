package features

import "math"

func highest(vals []float64, n int) float64 {
	if len(vals) == 0 || n <= 0 {
		return 0
	}
	if len(vals) < n {
		n = len(vals)
	}
	h := vals[len(vals)-n]
	for _, v := range vals[len(vals)-n:] {
		if v > h {
			h = v
		}
	}
	return h
}

func lowest(vals []float64, n int) float64 {
	if len(vals) == 0 || n <= 0 {
		return 0
	}
	if len(vals) < n {
		n = len(vals)
	}
	l := vals[len(vals)-n]
	for _, v := range vals[len(vals)-n:] {
		if v < l {
			l = v
		}
	}
	return l
}

func roc(closes []float64, n int) float64 {
	if n <= 0 || len(closes) < n+1 {
		return 0
	}
	prev := closes[len(closes)-1-n]
	if prev == 0 {
		return 0
	}
	return safe((closes[len(closes)-1] - prev) / prev)
}

func bollinger(closes []float64, n int, k float64) (upper, middle, lower, width, pct float64) {
	if len(closes) < n || n <= 1 {
		return
	}
	w := closes[len(closes)-n:]
	middle = safe(mean(w))
	sd := safe(stddev(w))
	upper = safe(middle + k*sd)
	lower = safe(middle - k*sd)
	if middle != 0 {
		width = safe((upper - lower) / middle)
	}
	if upper != lower {
		pct = safe((closes[len(closes)-1] - lower) / (upper - lower))
	}
	return
}

func mean(vals []float64) float64 {
	if len(vals) == 0 {
		return 0
	}
	s := 0.0
	for _, v := range vals {
		s += v
	}
	return s / float64(len(vals))
}

func cci(highs, lows, closes []float64, n int) float64 {
	if len(closes) < n || len(highs) < n || len(lows) < n || n <= 1 {
		return 0
	}
	tp := make([]float64, len(closes))
	for i := range closes {
		tp[i] = (highs[i] + lows[i] + closes[i]) / 3.0
	}
	w := tp[len(tp)-n:]
	smaTP := mean(w)
	md := 0.0
	for _, v := range w {
		md += math.Abs(v - smaTP)
	}
	md /= float64(n)
	if md == 0 {
		return 0
	}
	return safe((tp[len(tp)-1] - smaTP) / (0.015 * md))
}

func williamsR(highs, lows, closes []float64, n int) float64 {
	if len(closes) < n || len(highs) < n || len(lows) < n {
		return 0
	}
	h := highest(highs, n)
	l := lowest(lows, n)
	if h == l {
		return 0
	}
	return safe(-100.0 * (h - closes[len(closes)-1]) / (h - l))
}

func stochK(highs, lows, closes []float64, n int) float64 {
	if len(closes) < n || len(highs) < n || len(lows) < n {
		return 0
	}
	h := highest(highs, n)
	l := lowest(lows, n)
	if h == l {
		return 0
	}
	return safe(100.0 * (closes[len(closes)-1] - l) / (h - l))
}

func stochDFromKSeries(kSeries []float64, n int) float64 {
	if len(kSeries) < n || n <= 0 {
		return 0
	}
	return safe(mean(kSeries[len(kSeries)-n:]))
}
