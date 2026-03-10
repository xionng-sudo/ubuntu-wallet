package market

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

type Kline struct {
	Timestamp int64   `json:"timestamp"` // ms since epoch
	Open      float64 `json:"open"`
	High      float64 `json:"high"`
	Low       float64 `json:"low"`
	Close     float64 `json:"close"`
	Volume    float64 `json:"volume"`
}

// Fetcher is the minimal interface we need (so you can wire your existing exchange client).
type Fetcher interface {
	FetchOHLCV(ctx context.Context, symbol string, interval string, limit int) ([]Kline, error)
}

type WriterConfig struct {
	Symbol    string
	Interval  string // "1h"
	Limit     int
	DataDir   string
	Filename  string // "klines_1h.json"
	Every     time.Duration
}

func DefaultWriterConfig(dataDir string) WriterConfig {
	return WriterConfig{
		Symbol:   "ETHUSDT",
		Interval: "1h",
		Limit:    500,
		DataDir:  dataDir,
		Filename: "klines_1h.json",
		Every:    60 * time.Second,
	}
}

func RunKlinesWriter(ctx context.Context, f Fetcher, cfg WriterConfig) error {
	if cfg.Every <= 0 {
		cfg.Every = 60 * time.Second
	}
	if cfg.Limit <= 0 {
		cfg.Limit = 500
	}
	if cfg.DataDir == "" {
		return fmt.Errorf("DataDir is empty")
	}
	if cfg.Filename == "" {
		cfg.Filename = fmt.Sprintf("klines_%s.json", cfg.Interval)
	}

	_ = os.MkdirAll(cfg.DataDir, 0o775)

	// initial write immediately, then every tick
	if err := writeOnce(ctx, f, cfg); err != nil {
		// don't exit; keep retrying
	}

	ticker := time.NewTicker(cfg.Every)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
			_ = writeOnce(ctx, f, cfg)
		}
	}
}

func writeOnce(ctx context.Context, f Fetcher, cfg WriterConfig) error {
	klines, err := f.FetchOHLCV(ctx, cfg.Symbol, cfg.Interval, cfg.Limit)
	if err != nil {
		return err
	}
	if len(klines) == 0 {
		return fmt.Errorf("empty klines")
	}

	outPath := filepath.Join(cfg.DataDir, cfg.Filename)
	tmpPath := outPath + ".tmp"

	b, err := json.Marshal(klines)
	if err != nil {
		return err
	}

	// write tmp then rename (atomic on same filesystem)
	if err := os.WriteFile(tmpPath, b, 0o664); err != nil {
		return err
	}
	return os.Rename(tmpPath, outPath)
}
