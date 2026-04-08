package signal

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	log "github.com/sirupsen/logrus"
	"github.com/ubuntu-wallet/go-collector/features"
)

type mlPredictRequest struct {
	Symbol    string                    `json:"symbol"`
	Interval  string                    `json:"interval"`
	FeatureTS time.Time                 `json:"feature_ts"`
	Features  *features.FeatureSnapshot `json:"features"`
}

type mlPredictResponse struct {
	Signal       Signal   `json:"signal"`
	Confidence   float64  `json:"confidence"`
	ModelVersion string   `json:"model_version"`
	Reasons      []string `json:"reasons,omitempty"`
}

// Default: http://127.0.0.1:9000/predict
func mlServiceURL() string {
	v := strings.TrimSpace(os.Getenv("ML_SERVICE_URL"))
	if v == "" {
		return "http://127.0.0.1:9000/predict"
	}
	return v
}

// sharedMLClient is reused across calls to avoid per-request connection
// allocation overhead. The Timeout field is intentionally left at zero
// here — callers pass a context with deadline instead.
var sharedMLClient = &http.Client{}

func PredictWithML(ctx context.Context, snap *features.FeatureSnapshot, timeout time.Duration) (SignalResult, error) {
	res := SignalResult{
		Symbol:     snap.Symbol,
		Interval:   snap.Interval,
		Engine:     EngineML,
		EngineUsed: EngineML,
		FeatureTS:  snap.FeatureTS,
		Signal:     SignalFlat,
		Confidence: 0,
		Fallback:   false,
		Features:   snap,
	}

	if timeout <= 0 {
		timeout = 800 * time.Millisecond
	}

	reqBody := mlPredictRequest{
		Symbol:    snap.Symbol,
		Interval:  snap.Interval,
		FeatureTS: snap.FeatureTS,
		Features:  snap,
	}
	b, err := json.Marshal(reqBody)
	if err != nil {
		return res, err
	}

	url := mlServiceURL()

	// Use a deadline-scoped context so each call respects the timeout while
	// still honouring the caller's cancellation.
	callCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	httpReq, err := http.NewRequestWithContext(callCtx, http.MethodPost, url, bytes.NewReader(b))
	if err != nil {
		return res, err
	}
	httpReq.Header.Set("Content-Type", "application/json")

	httpResp, err := sharedMLClient.Do(httpReq)
	if err != nil {
		return res, fmt.Errorf("ml request failed: url=%s err=%w", url, err)
	}
	defer httpResp.Body.Close()

	// Read body regardless of status (helps debug 4xx/5xx HTML/JSON)
	bodyBytes, readErr := io.ReadAll(httpResp.Body)
	if readErr != nil {
		log.Warnf("ml_client: failed to read response body from %s: %v", url, readErr)
	}

	if httpResp.StatusCode < 200 || httpResp.StatusCode >= 300 {
		snippet := string(bodyBytes)
		if len(snippet) > 300 {
			snippet = snippet[:300]
		}
		return res, fmt.Errorf("ml service non-2xx: url=%s status=%d body=%q", url, httpResp.StatusCode, snippet)
	}

	var out mlPredictResponse
	if err := json.Unmarshal(bodyBytes, &out); err != nil {
		snippet := string(bodyBytes)
		if len(snippet) > 300 {
			snippet = snippet[:300]
		}
		return res, fmt.Errorf("ml service decode failed: url=%s body=%q err=%w", url, snippet, err)
	}

	// basic validation
	if out.Signal != SignalLong && out.Signal != SignalShort && out.Signal != SignalFlat {
		return res, fmt.Errorf("ml service returned invalid signal: url=%s signal=%q", url, string(out.Signal))
	}

	res.Signal = out.Signal
	res.Confidence = out.Confidence
	res.ModelVersion = out.ModelVersion
	if len(out.Reasons) > 0 {
		res.Reasons = out.Reasons
	}
	return res, nil
}

// MLOrFallback returns ML prediction; on failure, returns rules result (EngineUsed=rules, Fallback=true).
func MLOrFallback(ctx context.Context, snap *features.FeatureSnapshot) SignalResult {
	mlRes, err := PredictWithML(ctx, snap, 800*time.Millisecond)
	if err == nil {
		return mlRes
	}

	rules := RulesEngine(snap)
	rules.Engine = EngineML
	rules.EngineUsed = EngineRules
	rules.Fallback = true
	rules.Reason = "ml_unavailable: " + err.Error()
	return rules
}
