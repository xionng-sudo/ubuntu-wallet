package signal

import (
	"math"

	"github.com/ubuntu-wallet/go-collector/features"
)

// RulesEngine returns a baseline trend-following signal for 1h with 4h filter.
// Uses EMA12/EMA26 cross (common) to align with model_meta.json features.
func RulesEngine(snap *features.FeatureSnapshot) SignalResult {
	res := SignalResult{
		Symbol:     snap.Symbol,
		Interval:   snap.Interval,
		Engine:     EngineRules,
		EngineUsed: EngineRules,
		FeatureTS:  snap.FeatureTS,
		Signal:     SignalFlat,
		Confidence: 0,
		Fallback:   false,
		Reasons:    []string{},
		Features:   snap,
	}

	// Basic sanity
	if snap.Close <= 0 || snap.EMA12 == 0 || snap.EMA26 == 0 {
		res.Signal = SignalFlat
		res.Confidence = 0
		res.Reason = "insufficient_features"
		return res
	}

	// Direction by EMA12/EMA26 cross
	dirLong := snap.EMA12 > snap.EMA26
	dirShort := snap.EMA12 < snap.EMA26

	// Regime filter: low-vol chop -> flat
	lowVol := snap.Volatility20 > 0 && snap.Volatility20 < 0.002 // ~0.2% stddev
	if lowVol {
		res.Signal = SignalFlat
		res.Confidence = 0.2
		res.Reasons = append(res.Reasons, "low_volatility")
		return res
	}

	score := 0.0
	if dirLong {
		score += 0.40
		res.Reasons = append(res.Reasons, "ema12>ema26")
	} else if dirShort {
		score += 0.40
		res.Reasons = append(res.Reasons, "ema12<ema26")
	}

	// RSI confirmation (extra feature, helps avoid chop)
	if dirLong {
		if snap.RSI14 >= 52 {
			score += 0.20
			res.Reasons = append(res.Reasons, "rsi_confirm_long")
		} else {
			res.Reasons = append(res.Reasons, "rsi_weak_long")
		}
	}
	if dirShort {
		if snap.RSI14 <= 48 {
			score += 0.20
			res.Reasons = append(res.Reasons, "rsi_confirm_short")
		} else {
			res.Reasons = append(res.Reasons, "rsi_weak_short")
		}
	}

	// MACD confirmation (meta feature includes macd/macd_signal; we also have hist)
	if dirLong && snap.MACD > snap.MACDSignal {
		score += 0.15
		res.Reasons = append(res.Reasons, "macd>signal")
	}
	if dirShort && snap.MACD < snap.MACDSignal {
		score += 0.15
		res.Reasons = append(res.Reasons, "macd<signal")
	}

	// 4h filter: only allow trades aligned with 4h EMA200 trend
	if !snap.Filter4H.FeatureTS.IsZero() && snap.Filter4H.EMA200 != 0 {
		if dirLong && snap.Filter4H.Close >= snap.Filter4H.EMA200 {
			score += 0.20
			res.Reasons = append(res.Reasons, "4h_above_ema200")
		} else if dirShort && snap.Filter4H.Close <= snap.Filter4H.EMA200 {
			score += 0.20
			res.Reasons = append(res.Reasons, "4h_below_ema200")
		} else {
			res.Reasons = append(res.Reasons, "4h_trend_filter_block")
			score -= 0.25
		}
	}

	// clamp
	if score < 0 {
		score = 0
	}
	if score > 1 {
		score = 1
	}

	res.Confidence = math.Round(score*100) / 100

	// Decide signal by direction + minimum confidence
	if res.Confidence < 0.55 {
		res.Signal = SignalFlat
		res.Reasons = append(res.Reasons, "confidence_too_low")
		return res
	}
	if dirLong {
		res.Signal = SignalLong
	} else if dirShort {
		res.Signal = SignalShort
	} else {
		res.Signal = SignalFlat
	}
	return res
}
