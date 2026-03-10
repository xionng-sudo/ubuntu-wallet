package signal

import (
	"time"

	"github.com/ubuntu-wallet/go-collector/features"
)

type Signal string

const (
	SignalLong  Signal = "LONG"
	SignalShort Signal = "SHORT"
	SignalFlat  Signal = "FLAT"
)

type Engine string

const (
	EngineRules Engine = "rules"
	EngineML    Engine = "ml"
)

// SignalResult is the output consumed by Python executor / dashboard.
type SignalResult struct {
	Symbol    string `json:"symbol"`
	Interval  string `json:"interval"`   // "1h"
	Engine    Engine `json:"engine"`     // rules/ml
	EngineUsed Engine `json:"engine_used"` // rules/ml (ml may fallback to rules)

	FeatureTS time.Time `json:"feature_ts"` // aligns with features.feature_ts

	Signal     Signal  `json:"signal"`
	Confidence float64 `json:"confidence"`

	Fallback bool   `json:"fallback"`
	Reason   string `json:"reason,omitempty"`

	ModelVersion string `json:"model_version,omitempty"`

	Reasons []string               `json:"reasons,omitempty"`
	Features *features.FeatureSnapshot `json:"features,omitempty"`
}
