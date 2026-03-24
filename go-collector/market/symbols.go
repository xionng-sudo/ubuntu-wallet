package market

import (
	"os"
	"strings"
)

// Phase 1 symbols – enabled by default.
var phase1Symbols = []string{"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"}

// Phase 2 symbols – off by default; enabled via SYMBOLS env or ENABLE_PHASE2_SYMBOLS=true.
var phase2Symbols = []string{"XRPUSDT", "DOGEUSDT", "ADAUSDT"}

// ParseSymbols returns the ordered list of trading symbols to collect.
//
// Resolution order:
//  1. SYMBOLS env var (comma-separated, e.g. "BTCUSDT,ETHUSDT,SOLUSDT")
//  2. If ENABLE_PHASE2_SYMBOLS=true – all 7 symbols (Phase 1 + Phase 2)
//  3. Default – Phase 1 only (BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT)
func ParseSymbols() []string {
	if raw := strings.TrimSpace(os.Getenv("SYMBOLS")); raw != "" {
		var out []string
		for _, s := range strings.Split(raw, ",") {
			s = strings.ToUpper(strings.TrimSpace(s))
			if s != "" {
				out = append(out, s)
			}
		}
		if len(out) > 0 {
			return out
		}
	}

	if envBoolOrFalse("ENABLE_PHASE2_SYMBOLS") {
		return append(append([]string{}, phase1Symbols...), phase2Symbols...)
	}

	return append([]string{}, phase1Symbols...)
}

// SymbolDataDir returns the per-symbol subdirectory path within dataDir.
// e.g. SymbolDataDir("/data", "ETHUSDT") → "/data/ETHUSDT"
func SymbolDataDir(dataDir, symbol string) string {
	return dataDir + "/" + symbol
}

func envBoolOrFalse(key string) bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv(key))) {
	case "1", "true", "yes", "y", "on":
		return true
	}
	return false
}
