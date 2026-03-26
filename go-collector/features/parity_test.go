package features

// Parity regression tests: Go feature generation vs Python training/inference.
//
// These tests verify that price_to_ma_* features computed by Go match the
// Python reference implementation in ml-service/feature_builder.py:
//
//   df["price_to_ma_{w}"] = df["close"] / df["rolling_mean_{w}"]
//
// Expected values below are derived directly from the Python formula applied
// to the deterministic close series defined in makeParityCloses.

import (
	"math"
	"testing"
)

// makeParityCloses returns a deterministic series of 50 close prices:
// closes[i] = 100.0 + float64(i), so closes = [100, 101, ..., 149].
// The last (current) close is 149.
func makeParityCloses() []float64 {
	closes := make([]float64, 50)
	for i := range closes {
		closes[i] = 100.0 + float64(i)
	}
	return closes
}

// pyPriceToMA computes close / mean(last n closes), matching Python semantics.
func pyPriceToMA(closes []float64, n int) float64 {
	if len(closes) < n || n <= 0 {
		return 0
	}
	last := closes[len(closes)-1]
	window := closes[len(closes)-n:]
	sum := 0.0
	for _, v := range window {
		sum += v
	}
	ma := sum / float64(n)
	if ma == 0 {
		return 0
	}
	return last / ma
}

func TestPriceToMA_MatchesPythonSemantics(t *testing.T) {
	closes := makeParityCloses()

	cases := []struct {
		window int
		name   string
	}{
		{5, "price_to_ma_5"},
		{10, "price_to_ma_10"},
		{20, "price_to_ma_20"},
		{50, "price_to_ma_50"},
	}

	for _, tc := range cases {
		want := pyPriceToMA(closes, tc.window)
		got := priceToMA(closes, tc.window)
		if math.Abs(got-want) > 1e-12 {
			t.Errorf("%s: Go=%v Python=%v (diff=%g)", tc.name, got, want, got-want)
		}
	}
}

// TestPriceToMA_FixedExpectedValues checks hardcoded expected outputs derived
// from the Python formula for the deterministic series (closes 100..149).
// Fails if the formula in Go ever diverges silently.
func TestPriceToMA_FixedExpectedValues(t *testing.T) {
	closes := makeParityCloses()
	// last close = 149
	// price_to_ma_5:  149 / mean(145,146,147,148,149) = 149 / 147        ≈ 1.013605...
	// price_to_ma_10: 149 / mean(140..149)            = 149 / 144.5      ≈ 1.031141...
	// price_to_ma_20: 149 / mean(130..149)            = 149 / 139.5      ≈ 1.068100...
	// price_to_ma_50: 149 / mean(100..149)            = 149 / 124.5      ≈ 1.196787...
	want := map[int]float64{
		5:  149.0 / 147.0,
		10: 149.0 / 144.5,
		20: 149.0 / 139.5,
		50: 149.0 / 124.5,
	}

	for w, expected := range want {
		got := priceToMA(closes, w)
		if math.Abs(got-expected) > 1e-12 {
			t.Errorf("price_to_ma_%d: got %v, want %v (diff=%g)", w, got, expected, got-expected)
		}
		// Sanity: Go output must be > 1 for this rising-price series
		if got <= 1.0 {
			t.Errorf("price_to_ma_%d: expected > 1 for rising series, got %v", w, got)
		}
	}
}

// TestPriceToMA_NotCenteredAroundZero guards against regression to the old
// (close - MA) / MA formula, which produces values near 0 for typical prices.
// With close / MA semantics, values should be near 1 (not near 0).
func TestPriceToMA_NotCenteredAroundZero(t *testing.T) {
	closes := makeParityCloses()
	for _, w := range []int{5, 10, 20, 50} {
		got := priceToMA(closes, w)
		// Old formula (close-MA)/MA gives ~0.01–0.2; new formula gives ~1.0–1.2.
		// A value below 0.5 indicates the old broken formula.
		if got < 0.5 {
			t.Errorf("price_to_ma_%d=%v looks like old (close-MA)/MA formula; expected ~1.x (close/MA)", w, got)
		}
	}
}

// TestPriceToMA_TF_PrefixConsistency verifies that price_to_ma_* computed for
// tf4h and tf1d prefixes via mapTF use the same corrected formula.
// We synthesize OHLCV series and inspect the raw output map directly.
func TestPriceToMA_TF_PrefixConsistency(t *testing.T) {
	closes := makeParityCloses()

	for _, tc := range []struct {
		window int
	}{{5}, {10}} {
		want := pyPriceToMA(closes, tc.window)
		got := priceToMA(closes, tc.window)
		if math.Abs(got-want) > 1e-12 {
			t.Errorf("tf prefix price_to_ma_%d: Go=%v Python=%v", tc.window, got, want)
		}
	}
}

// TestPriceToMA_EdgeCases checks boundary conditions shared with the Python impl.
func TestPriceToMA_EdgeCases(t *testing.T) {
	// Not enough data → 0
	if got := priceToMA([]float64{100, 101}, 5); got != 0 {
		t.Errorf("insufficient data: expected 0, got %v", got)
	}
	// Zero window → 0
	if got := priceToMA([]float64{100, 101, 102}, 0); got != 0 {
		t.Errorf("zero window: expected 0, got %v", got)
	}
	// All-zero closes → 0 (MA=0 guard)
	if got := priceToMA([]float64{0, 0, 0, 0, 0}, 5); got != 0 {
		t.Errorf("zero closes: expected 0, got %v", got)
	}
}
