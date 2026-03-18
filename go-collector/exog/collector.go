package exog

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"time"
)

const binanceFuturesBase = "https://fapi.binance.com"

// ExogSnapshot holds a single point-in-time snapshot of exogenous market features
// for one futures symbol (e.g. ETHUSDT).
type ExogSnapshot struct {
	Symbol         string    `json:"symbol"`
	FundingRate    float64   `json:"funding_rate"`
	OpenInterest   float64   `json:"open_interest"`
	TakerBuyRatio  float64   `json:"taker_buy_ratio"`
	Timestamp      time.Time `json:"timestamp"`
}

// ExogCollector fetches exogenous features from Binance Futures REST API.
type ExogCollector struct {
	client *http.Client
}

// NewExogCollector creates an ExogCollector with a default timeout.
func NewExogCollector() *ExogCollector {
	return &ExogCollector{
		client: &http.Client{Timeout: 10 * time.Second},
	}
}

// Collect fetches funding rate, open interest, and taker buy ratio for symbol.
// On partial API failures it logs a warning and returns the best-effort snapshot.
func (c *ExogCollector) Collect(symbol string) (*ExogSnapshot, error) {
	snap := &ExogSnapshot{
		Symbol:    symbol,
		Timestamp: time.Now().UTC(),
	}

	fr, err := c.fetchFundingRate(symbol)
	if err != nil {
		return snap, fmt.Errorf("exog: funding rate for %s: %w", symbol, err)
	}
	snap.FundingRate = fr

	oi, err := c.fetchOpenInterest(symbol)
	if err != nil {
		return snap, fmt.Errorf("exog: open interest for %s: %w", symbol, err)
	}
	snap.OpenInterest = oi

	tbr, err := c.fetchTakerBuyRatio(symbol)
	if err != nil {
		return snap, fmt.Errorf("exog: taker buy ratio for %s: %w", symbol, err)
	}
	snap.TakerBuyRatio = tbr

	return snap, nil
}

func (c *ExogCollector) getJSON(url string, dest interface{}) error {
	resp, err := c.client.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(body))
	}

	return json.Unmarshal(body, dest)
}

func (c *ExogCollector) fetchFundingRate(symbol string) (float64, error) {
	url := fmt.Sprintf("%s/fapi/v1/fundingRate?symbol=%s&limit=1", binanceFuturesBase, symbol)

	var rows []struct {
		FundingRate string `json:"fundingRate"`
	}
	if err := c.getJSON(url, &rows); err != nil {
		return 0, err
	}
	if len(rows) == 0 {
		return 0, fmt.Errorf("empty fundingRate response")
	}

	var v float64
	if _, err := fmt.Sscanf(rows[0].FundingRate, "%f", &v); err != nil {
		return 0, fmt.Errorf("parse fundingRate %q: %w", rows[0].FundingRate, err)
	}
	return v, nil
}

func (c *ExogCollector) fetchOpenInterest(symbol string) (float64, error) {
	url := fmt.Sprintf("%s/fapi/v1/openInterest?symbol=%s", binanceFuturesBase, symbol)

	var row struct {
		OpenInterest string `json:"openInterest"`
	}
	if err := c.getJSON(url, &row); err != nil {
		return 0, err
	}

	var v float64
	if _, err := fmt.Sscanf(row.OpenInterest, "%f", &v); err != nil {
		return 0, fmt.Errorf("parse openInterest %q: %w", row.OpenInterest, err)
	}
	return v, nil
}

func (c *ExogCollector) fetchTakerBuyRatio(symbol string) (float64, error) {
	url := fmt.Sprintf("%s/fapi/v1/takerLongShortRatio?symbol=%s&period=1h&limit=1", binanceFuturesBase, symbol)

	var rows []struct {
		BuySellRatio string `json:"buySellRatio"`
	}
	if err := c.getJSON(url, &rows); err != nil {
		return 0, err
	}
	if len(rows) == 0 {
		return 0, fmt.Errorf("empty takerLongShortRatio response")
	}

	var v float64
	if _, err := fmt.Sscanf(rows[0].BuySellRatio, "%f", &v); err != nil {
		return 0, fmt.Errorf("parse buySellRatio %q: %w", rows[0].BuySellRatio, err)
	}
	return v, nil
}

// SaveExogSnapshot appends snap as a JSONL line to <dataDir>/raw/exog_<symbol>.jsonl.
// The raw/ subdirectory is created if it does not exist.
func SaveExogSnapshot(dataDir string, snap *ExogSnapshot) error {
	rawDir := filepath.Join(dataDir, "raw")
	if err := os.MkdirAll(rawDir, 0755); err != nil {
		return fmt.Errorf("exog: mkdir %s: %w", rawDir, err)
	}

	path := filepath.Join(rawDir, fmt.Sprintf("exog_%s.jsonl", snap.Symbol))
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		return fmt.Errorf("exog: open %s: %w", path, err)
	}
	defer f.Close()

	line, err := json.Marshal(snap)
	if err != nil {
		return fmt.Errorf("exog: marshal snapshot: %w", err)
	}

	_, err = fmt.Fprintf(f, "%s\n", line)
	return err
}
