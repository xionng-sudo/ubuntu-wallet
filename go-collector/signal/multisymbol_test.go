package signal

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/ubuntu-wallet/go-collector/features"
)

// makeTestSnap builds a minimal FeatureSnapshot for testing.
func makeTestSnap(symbol string) *features.FeatureSnapshot {
	return &features.FeatureSnapshot{
		Symbol:    symbol,
		Interval:  "1h",
		FeatureTS: time.Now().UTC(),
	}
}

// capturedRequests accumulates symbols received by a mock ML server.
type capturedRequests struct {
	mu      sync.Mutex
	symbols []string
}

func (c *capturedRequests) add(sym string) {
	c.mu.Lock()
	c.symbols = append(c.symbols, sym)
	c.mu.Unlock()
}

func (c *capturedRequests) all() []string {
	c.mu.Lock()
	defer c.mu.Unlock()
	out := make([]string, len(c.symbols))
	copy(out, c.symbols)
	return out
}

// mockMLServer starts a test HTTP server that records received symbols and
// always returns a valid FLAT response.
func mockMLServer(t *testing.T) (*httptest.Server, *capturedRequests) {
	t.Helper()
	cap := &capturedRequests{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req mlPredictRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		cap.add(req.Symbol)
		resp := mlPredictResponse{Signal: SignalFlat, Confidence: 0.5, ModelVersion: "test-v1"}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(resp)
	}))
	return srv, cap
}

// TestMLOrFallback_SymbolPropagation verifies that the symbol in the
// FeatureSnapshot is forwarded correctly in the POST /predict request.
func TestMLOrFallback_SymbolPropagation(t *testing.T) {
	srv, cap := mockMLServer(t)
	defer srv.Close()

	t.Setenv("ML_SERVICE_URL", srv.URL)

	snap := makeTestSnap("BTCUSDT")
	result := MLOrFallback(context.Background(), snap)

	got := cap.all()
	if len(got) == 0 {
		t.Fatal("expected at least one request to ml-service, got none")
	}
	if got[0] != "BTCUSDT" {
		t.Errorf("expected symbol BTCUSDT in request, got %q", got[0])
	}
	if result.Fallback {
		t.Errorf("expected no fallback, got fallback=true reason=%q", result.Reason)
	}
	if result.Symbol != "BTCUSDT" {
		t.Errorf("expected result.Symbol=BTCUSDT, got %q", result.Symbol)
	}
}

// TestMLOrFallback_MultiSymbolEachGetsRequest verifies that calling MLOrFallback
// for multiple symbols produces a separate /predict request per symbol, each with
// the correct symbol value (simulating the per-symbol loop in
// computeAndPersistFeaturesAndSignals).
func TestMLOrFallback_MultiSymbolEachGetsRequest(t *testing.T) {
	srv, cap := mockMLServer(t)
	defer srv.Close()

	t.Setenv("ML_SERVICE_URL", srv.URL)

	symbols := []string{"ETHUSDT", "BTCUSDT", "SOLUSDT", "BNBUSDT"}
	for _, sym := range symbols {
		snap := makeTestSnap(sym)
		MLOrFallback(context.Background(), snap)
	}

	got := cap.all()
	if len(got) != len(symbols) {
		t.Fatalf("expected %d /predict requests (one per symbol), got %d", len(symbols), len(got))
	}
	for i, sym := range symbols {
		if got[i] != sym {
			t.Errorf("request[%d]: expected symbol %q, got %q", i, sym, got[i])
		}
	}
}

// TestMLOrFallback_NonPrimarySymbolPredicted verifies that a non-primary symbol
// (BTCUSDT) gets its own prediction request when primary is ETHUSDT.
func TestMLOrFallback_NonPrimarySymbolPredicted(t *testing.T) {
	srv, cap := mockMLServer(t)
	defer srv.Close()

	t.Setenv("ML_SERVICE_URL", srv.URL)

	// Simulate: primary = ETHUSDT, non-primary = BTCUSDT
	for _, sym := range []string{"ETHUSDT", "BTCUSDT"} {
		MLOrFallback(context.Background(), makeTestSnap(sym))
	}

	sawBTC := false
	sawETH := false
	for _, sym := range cap.all() {
		switch sym {
		case "BTCUSDT":
			sawBTC = true
		case "ETHUSDT":
			sawETH = true
		}
	}
	if !sawETH {
		t.Error("expected ETHUSDT (primary) prediction request")
	}
	if !sawBTC {
		t.Error("expected BTCUSDT (non-primary) prediction request — non-primary symbols must also get predictions")
	}
}

// TestMLOrFallback_FallbackOnMLUnavailable verifies that when ml-service is
// unreachable, the result uses the rules engine with Fallback=true, so that a
// failed ml-service does not silently skip signal generation for that symbol.
func TestMLOrFallback_FallbackOnMLUnavailable(t *testing.T) {
	t.Setenv("ML_SERVICE_URL", "http://127.0.0.1:19997/predict") // unreachable port

	result := MLOrFallback(context.Background(), makeTestSnap("SOLUSDT"))

	if !result.Fallback {
		t.Error("expected Fallback=true when ml-service is unreachable")
	}
	if result.EngineUsed != EngineRules {
		t.Errorf("expected EngineUsed=%q on fallback, got %q", EngineRules, result.EngineUsed)
	}
	if result.Symbol != "SOLUSDT" {
		t.Errorf("expected result.Symbol=SOLUSDT, got %q", result.Symbol)
	}
}

// TestMLOrFallback_OneFailureDoesNotBlockOthers verifies that a failure for one
// symbol does not prevent predictions from being issued for other symbols
// (failure isolation).
func TestMLOrFallback_OneFailureDoesNotBlockOthers(t *testing.T) {
	srv, cap := mockMLServer(t)
	defer srv.Close()

	// ETHUSDT: ml-service is unreachable → fallback expected.
	t.Setenv("ML_SERVICE_URL", "http://127.0.0.1:19997/predict")
	ethResult := MLOrFallback(context.Background(), makeTestSnap("ETHUSDT"))

	// BTCUSDT: ml-service is up → no fallback expected.
	t.Setenv("ML_SERVICE_URL", srv.URL)
	btcResult := MLOrFallback(context.Background(), makeTestSnap("BTCUSDT"))

	if !ethResult.Fallback {
		t.Error("expected ETHUSDT to fall back when ml-service is unavailable")
	}
	got := cap.all()
	if btcResult.Fallback {
		t.Errorf("expected BTCUSDT to succeed (ml-service is up), got fallback=true: %s", btcResult.Reason)
	}
	if len(got) != 1 || got[0] != "BTCUSDT" {
		t.Errorf("expected exactly one successful request for BTCUSDT, got %v", got)
	}
}
