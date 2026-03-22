package features

import "math"

// ADX / +DI / -DI
func adxDI(highs, lows, closes []float64, period int) (adx, plusDI, minusDI float64) {
	n := len(closes)
	if n < period+2 || len(highs) != n || len(lows) != n || period <= 1 {
		return 0, 0, 0
	}

	tr := make([]float64, n)
	pdm := make([]float64, n)
	mdm := make([]float64, n)

	for i := 1; i < n; i++ {
		upMove := highs[i] - highs[i-1]
		downMove := lows[i-1] - lows[i]

		if upMove > downMove && upMove > 0 {
			pdm[i] = upMove
		}
		if downMove > upMove && downMove > 0 {
			mdm[i] = downMove
		}

		a := highs[i] - lows[i]
		b := math.Abs(highs[i] - closes[i-1])
		c := math.Abs(lows[i] - closes[i-1])
		tr[i] = math.Max(a, math.Max(b, c))
	}

	atrSm := wilderSmooth(tr, period)
	pdmSm := wilderSmooth(pdm, period)
	mdmSm := wilderSmooth(mdm, period)

	dx := make([]float64, n)
	for i := 0; i < n; i++ {
		if atrSm[i] == 0 {
			continue
		}
		p := 100.0 * (pdmSm[i] / atrSm[i])
		m := 100.0 * (mdmSm[i] / atrSm[i])
		den := p + m
		if den != 0 {
			dx[i] = 100.0 * math.Abs(p-m) / den
		}
		if i == n-1 {
			plusDI = safe(p)
			minusDI = safe(m)
		}
	}

	adxArr := wilderSmooth(dx, period)
	adx = safe(adxArr[n-1])
	return
}

func wilderSmooth(vals []float64, period int) []float64 {
	n := len(vals)
	out := make([]float64, n)
	if n == 0 || period <= 0 || n < period {
		return out
	}

	sum := 0.0
	for i := 0; i < period; i++ {
		sum += vals[i]
	}
	out[period-1] = sum

	for i := period; i < n; i++ {
		out[i] = out[i-1] - out[i-1]/float64(period) + vals[i]
	}
	return out
}

// MFI
func mfi(highs, lows, closes, vols []float64, period int) float64 {
	n := len(closes)
	if n < period+1 || len(highs) != n || len(lows) != n || len(vols) != n {
		return 0
	}

	typ := make([]float64, n)
	for i := range closes {
		typ[i] = (highs[i] + lows[i] + closes[i]) / 3.0
	}

	pos, neg := 0.0, 0.0
	for i := n - period; i < n; i++ {
		if i <= 0 {
			continue
		}
		rm := typ[i] * vols[i]
		if typ[i] > typ[i-1] {
			pos += rm
		} else if typ[i] < typ[i-1] {
			neg += rm
		}
	}
	if neg == 0 {
		if pos == 0 {
			return 50
		}
		return 100
	}
	mr := pos / neg
	return safe(100.0 - (100.0 / (1.0 + mr)))
}

// Ichimoku
func ichimoku(highs, lows []float64) (tenkan, kijun, senkouA, senkouB float64) {
	n := len(highs)
	if n == 0 || len(lows) != n {
		return
	}
	tenkan = midHL(highs, lows, 9)
	kijun = midHL(highs, lows, 26)
	senkouA = safe((tenkan + kijun) / 2.0)
	senkouB = midHL(highs, lows, 52)
	return
}

func midHL(highs, lows []float64, period int) float64 {
	if len(highs) < period || len(lows) < period {
		return 0
	}
	h := highest(highs, period)
	l := lowest(lows, period)
	return safe((h + l) / 2.0)
}

// Parabolic SAR (simplified)
func parabolicSAR(highs, lows []float64, afStep, afMax float64) float64 {
	n := len(highs)
	if n < 2 || len(lows) != n {
		return 0
	}

	uptrend := true
	sar := lows[0]
	ep := highs[0]
	af := afStep

	for i := 1; i < n; i++ {
		sar = sar + af*(ep-sar)

		if uptrend {
			if lows[i] < sar {
				uptrend = false
				sar = ep
				ep = lows[i]
				af = afStep
			} else if highs[i] > ep {
				ep = highs[i]
				af = math.Min(af+afStep, afMax)
			}
		} else {
			if highs[i] > sar {
				uptrend = true
				sar = ep
				ep = highs[i]
				af = afStep
			} else if lows[i] < ep {
				ep = lows[i]
				af = math.Min(af+afStep, afMax)
			}
		}
	}
	return safe(sar)
}
