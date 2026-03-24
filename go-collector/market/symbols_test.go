package market

import (
	"os"
	"reflect"
	"testing"
)

func TestParseSymbols_DefaultPhase1(t *testing.T) {
	os.Unsetenv("SYMBOLS")
	os.Unsetenv("ENABLE_PHASE2_SYMBOLS")

	got := ParseSymbols()
	want := []string{"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("ParseSymbols() = %v, want %v", got, want)
	}
}

func TestParseSymbols_Phase2Enabled(t *testing.T) {
	os.Unsetenv("SYMBOLS")
	os.Setenv("ENABLE_PHASE2_SYMBOLS", "true")
	defer os.Unsetenv("ENABLE_PHASE2_SYMBOLS")

	got := ParseSymbols()
	want := []string{"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("ParseSymbols() = %v, want %v", got, want)
	}
}

func TestParseSymbols_ExplicitSYMBOLS(t *testing.T) {
	os.Setenv("SYMBOLS", "BTCUSDT, ETHUSDT , SOLUSDT")
	os.Unsetenv("ENABLE_PHASE2_SYMBOLS")
	defer os.Unsetenv("SYMBOLS")

	got := ParseSymbols()
	want := []string{"BTCUSDT", "ETHUSDT", "SOLUSDT"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("ParseSymbols() = %v, want %v", got, want)
	}
}

func TestParseSymbols_AllSevenViaEnv(t *testing.T) {
	os.Setenv("SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT")
	defer os.Unsetenv("SYMBOLS")

	got := ParseSymbols()
	want := []string{"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("ParseSymbols() = %v, want %v", got, want)
	}
}

func TestParseSymbols_SYMBOLSTakesPrecedenceOverPhase2(t *testing.T) {
	os.Setenv("SYMBOLS", "BTCUSDT,ETHUSDT")
	os.Setenv("ENABLE_PHASE2_SYMBOLS", "true")
	defer os.Unsetenv("SYMBOLS")
	defer os.Unsetenv("ENABLE_PHASE2_SYMBOLS")

	got := ParseSymbols()
	want := []string{"BTCUSDT", "ETHUSDT"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("ParseSymbols() = %v, want %v", got, want)
	}
}

func TestParseSymbols_EmptySYMBOLSFallsThrough(t *testing.T) {
	os.Setenv("SYMBOLS", "   ")
	os.Unsetenv("ENABLE_PHASE2_SYMBOLS")
	defer os.Unsetenv("SYMBOLS")

	got := ParseSymbols()
	want := []string{"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("ParseSymbols() = %v, want %v", got, want)
	}
}

func TestSymbolDataDir(t *testing.T) {
	cases := []struct {
		dataDir string
		symbol  string
		want    string
	}{
		{"/data", "ETHUSDT", "/data/ETHUSDT"},
		{"/data", "BTCUSDT", "/data/BTCUSDT"},
		{"../data", "SOLUSDT", "../data/SOLUSDT"},
	}
	for _, c := range cases {
		got := SymbolDataDir(c.dataDir, c.symbol)
		if got != c.want {
			t.Errorf("SymbolDataDir(%q, %q) = %q, want %q", c.dataDir, c.symbol, got, c.want)
		}
	}
}

func TestDefaultWriterConfigNoHardcodedSymbol(t *testing.T) {
	cfg := DefaultWriterConfig("/data")
	// Symbol field should be empty so the caller must set it explicitly.
	if cfg.Symbol == "ETHUSDT" {
		t.Error("DefaultWriterConfig should not hardcode ETHUSDT as Symbol; caller must set it")
	}
}
