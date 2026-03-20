package features

import "time"

// FeatureSnapshot is a single, time-aligned feature vector.
// feature_ts must be the timestamp of the CLOSED candle close time (UTC).
type FeatureSnapshot struct {
	Symbol    string    `json:"symbol"`
	Interval  string    `json:"interval"` // "1h"
	FeatureTS time.Time `json:"feature_ts"`

	// Backward-compatible candle fields
	Close  float64 `json:"close"`
	Open   float64 `json:"open"`
	High   float64 `json:"high"`
	Low    float64 `json:"low"`
	Volume float64 `json:"volume"`

	// Legacy fields kept for signal package compatibility
	Ret1H float64 `json:"ret_1h"`
	Ret4H float64 `json:"ret_4h"`

	SMA7   float64 `json:"sma_7"`
	SMA25  float64 `json:"sma_25"`
	SMA99  float64 `json:"sma_99"`
	SMA200 float64 `json:"sma_200"`

	EMA12  float64 `json:"ema_12"`
	EMA26  float64 `json:"ema_26"`
	EMA50  float64 `json:"ema_50"`
	EMA200 float64 `json:"ema_200"`

	MACD       float64 `json:"macd"`
	MACDSignal float64 `json:"macd_signal"`
	MACDHist   float64 `json:"macd_hist"`

	RSI14        float64 `json:"rsi_14"`
	Volatility20 float64 `json:"volatility_20"`
	ATR14        float64 `json:"atr_14"`

	// Full model input vector aligned to feature_columns_event_v3.json
	Features map[string]float64 `json:"features"`

	// Optional debug info
	SchemaColumns int `json:"schema_columns"`
	ComputedCols  int `json:"computed_cols"`
	MissingCols   int `json:"missing_cols"`

	// Legacy 4h block
	Filter4H struct {
		FeatureTS time.Time `json:"feature_ts"`
		Close     float64   `json:"close"`
		EMA200    float64   `json:"ema_200"`
		RSI14     float64   `json:"rsi_14"`
	} `json:"filter_4h"`
}
