package market

import (
	"fmt"
	"os"
	"path/filepath"
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
	return filepath.Join(dataDir, symbol)
}

// ResolvePrimarySymbol returns the primary trading symbol.
//
// Resolution order:
//  1. PRIMARY_SYMBOL env var – if set, it is validated against enabledSymbols;
//     returns an error if it is not present in the enabled list.
//  2. "ETHUSDT" if present in enabledSymbols (backward-compatible default,
//     preserves existing ETHUSDT-oriented consumers).
//  3. enabledSymbols[0] as a safe last-resort fallback (with a note to
//     operators to set PRIMARY_SYMBOL explicitly).
//
// The caller (main) should log.Fatalf on a non-nil error so that
// misconfiguration is caught at startup rather than silently at runtime.
func ResolvePrimarySymbol(enabledSymbols []string) (string, error) {
	if raw := strings.ToUpper(strings.TrimSpace(os.Getenv("PRIMARY_SYMBOL"))); raw != "" {
		for _, s := range enabledSymbols {
			if s == raw {
				return raw, nil
			}
		}
		return "", fmt.Errorf("PRIMARY_SYMBOL=%q is not in enabled symbols %v; add it to SYMBOLS or unset PRIMARY_SYMBOL", raw, enabledSymbols)
	}

	// Backward-compatible default: prefer ETHUSDT to preserve existing consumers.
	for _, s := range enabledSymbols {
		if s == "ETHUSDT" {
			return "ETHUSDT", nil
		}
	}

	// Last resort: fall back to the first enabled symbol and warn operators.
	if len(enabledSymbols) > 0 {
		return enabledSymbols[0], nil
	}

	return "", fmt.Errorf("cannot resolve primary symbol: enabled symbol list is empty")
}

func envBoolOrFalse(key string) bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv(key))) {
	case "1", "true", "yes", "y", "on":
		return true
	}
	return false
}
