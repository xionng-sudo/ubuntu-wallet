package main

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	log "github.com/sirupsen/logrus"
	"github.com/ubuntu-wallet/go-collector/collector"
	"github.com/ubuntu-wallet/go-collector/exog"
	"github.com/ubuntu-wallet/go-collector/features"
	"github.com/ubuntu-wallet/go-collector/models"
	"github.com/ubuntu-wallet/go-collector/signal"
)

const (
	defaultDataDir = "../data"

	topN       = 50
	tradeLimit = 100

	okxMaxWorkers = 6
	okxRateSleep  = 80 * time.Millisecond
)

// Klines lookback mode controls whether we call Binance GetKlinesLookback for
// 15m/1h/4h/1d intervals.
// - on_startup (default): run lookback once during initial startup collection only
// - always: run lookback on every FAST tick (can be heavy/noisy)
// - off: never run lookback (always use latest-window-only)
const (
	klinesLookbackModeOnStartup = "on_startup"
	klinesLookbackModeAlways    = "always"
	klinesLookbackModeOff       = "off"
)

// ensures on_startup lookback happens at most once per process lifetime
var lookbackOnce sync.Once

type DataStore struct {
	mu          sync.RWMutex
	Traders     map[string][]models.Trader `json:"traders"`
	Trades      map[string][]models.Trade  `json:"trades"`
	PriceLevels []models.PriceLevel        `json:"price_levels"`
	MarketData  *models.MarketData         `json:"market_data"`
	Klines      map[string][]models.OHLCV  `json:"klines"`
	LastUpdate  time.Time                  `json:"last_update"`

	StartedAt time.Time `json:"-"`

	// New: computed artifacts (in-memory latest)
	LatestFeatures1H  *features.FeatureSnapshot `json:"-"`
	LatestSignalRules *signal.SignalResult      `json:"-"`
	LatestSignalML    *signal.SignalResult      `json:"-"`
}

var store = &DataStore{
	Traders:   make(map[string][]models.Trader),
	Trades:    make(map[string][]models.Trade),
	Klines:    make(map[string][]models.OHLCV),
	StartedAt: time.Now().UTC(),
}

// runtime config (for health/status observability)
var (
	runtimeDataDir   string
	runtimeFastEvery time.Duration
	runtimeSlowEvery time.Duration
	runtimeFeatureColumns []string
)

func envOrDefault(key, def string) string {
	v := strings.TrimSpace(os.Getenv(key))
	if v == "" {
		return def
	}
	return v
}

func envIntOrDefault(key string, def int) int {
	v := strings.TrimSpace(os.Getenv(key))
	if v == "" {
		return def
	}
	i, err := strconv.Atoi(v)
	if err != nil {
		return def
	}
	return i
}

func envDurationOrDefault(key string, def time.Duration) time.Duration {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return def
	}
	d, err := time.ParseDuration(raw)
	if err != nil || d <= 0 {
		log.Warnf("Invalid %s=%q, fallback to %s", key, raw, def)
		return def
	}
	return d
}

func envBoolOrDefault(key string, def bool) bool {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return def
	}
	switch strings.ToLower(raw) {
	case "1", "true", "yes", "y", "on":
		return true
	case "0", "false", "no", "n", "off":
		return false
	default:
		return def
	}
}

func fileStatJSON(path string) map[string]interface{} {
	st, err := os.Stat(path)
	if err != nil {
		return map[string]interface{}{
			"path":  path,
			"ok":    false,
			"error": err.Error(),
		}
	}
	return map[string]interface{}{
		"path":  path,
		"ok":    true,
		"mtime": st.ModTime().UTC().Format(time.RFC3339Nano),
		"size":  st.Size(),
	}
}

// fileFreshOK returns whether:
// - file exists (ok=true), and
// - now - mtime <= maxAge
// It also returns a JSON-ish map for embedding in /api/healthz response.
func fileFreshOK(path string, now time.Time, maxAge time.Duration) (bool, map[string]interface{}) {
	st, err := os.Stat(path)
	if err != nil {
		return false, map[string]interface{}{
			"path":  path,
			"ok":    false,
			"error": err.Error(),
		}
	}

	age := now.Sub(st.ModTime())
	fresh := age <= maxAge

	return fresh, map[string]interface{}{
		"path":          path,
		"ok":            true,
		"mtime":         st.ModTime().UTC().Format(time.RFC3339Nano),
		"size":          st.Size(),
		"age_sec":       int64(age.Seconds()),
		"fresh":         fresh,
		"max_age_sec":   int64(maxAge.Seconds()),
		"max_age_human": maxAge.String(),
	}
}

func main() {
	log.SetFormatter(&log.TextFormatter{FullTimestamp: true})
	log.SetLevel(log.InfoLevel)
	schemaPath := envOrDefault("FEATURE_SCHEMA_PATH", "../models/current/feature_columns_event_v3.json")
	cols, err := features.LoadFeatureColumns(schemaPath)
	if err != nil {
		log.Fatalf("Failed to load feature schema columns from %s: %v", schemaPath, err)
	}
	runtimeFeatureColumns = cols
	log.Infof("Loaded feature schema columns: %d from %s", len(runtimeFeatureColumns), schemaPath)

	mode := strings.TrimSpace(strings.ToLower(os.Getenv("KLINES_LOOKBACK_MODE")))
	if mode == "" {
		mode = klinesLookbackModeOnStartup
	}
	log.Infof("KLINES_LOOKBACK_MODE=%s", mode)

	log.Info("========================================")
	log.Info("  ETH Crypto Trader Data Collector")
	log.Info("========================================")
	log.Infof("PID: %d", os.Getpid())
	if v := os.Getenv("ALLOW_MOCK"); v == "" {
		log.Info("ALLOW_MOCK is not set (default: true)")
	} else {
		log.Infof("ALLOW_MOCK=%s", v)
	}

	dataDir := envOrDefault("DATA_DIR", defaultDataDir)
	runtimeDataDir = dataDir
	log.Infof("DATA_DIR=%s", dataDir)

	if err := os.MkdirAll(dataDir, 0755); err != nil {
		log.Fatalf("Failed to create data directory: %v", err)
	}

	binance := collector.NewBinanceCollector(os.Getenv("BINANCE_API_KEY"), os.Getenv("BINANCE_API_SECRET"))
	okx := collector.NewOKXCollector(os.Getenv("OKX_API_KEY"), os.Getenv("OKX_API_SECRET"), os.Getenv("OKX_PASSPHRASE"))
	coinbase := collector.NewCoinbaseCollector(os.Getenv("COINBASE_API_KEY"), os.Getenv("COINBASE_API_SECRET"))

	// Backward compatible: if COLLECT_FAST_INTERVAL not set, fall back to COLLECT_INTERVAL; otherwise default 60s.
	fastRaw := strings.TrimSpace(os.Getenv("COLLECT_FAST_INTERVAL"))
	if fastRaw == "" {
		fastRaw = strings.TrimSpace(os.Getenv("COLLECT_INTERVAL"))
	}
	if fastRaw == "" {
		fastRaw = "60s"
	}
	_ = os.Setenv("COLLECT_FAST_INTERVAL", fastRaw)

	fastEvery := envDurationOrDefault("COLLECT_FAST_INTERVAL", 60*time.Second)
	slowEvery := envDurationOrDefault("COLLECT_SLOW_INTERVAL", 5*time.Minute)
	runtimeFastEvery = fastEvery
	runtimeSlowEvery = slowEvery

	log.Infof("Fast collection every %s (market+klines+features+signal)", fastEvery)
	log.Infof("Slow collection every %s (traders+trades+price-levels)", slowEvery)

	// Initial: do slow first (so you have traders/trades), then fast (so you have features/signal quickly).
	log.Info("Starting initial slow data collection...")
	collectSlowAll(dataDir, binance, okx, coinbase)

	log.Info("Starting initial fast data collection...")
	collectFastAll(dataDir, binance, true)

	go func() {
		ticker := time.NewTicker(fastEvery)
		defer ticker.Stop()
		for range ticker.C {
			log.Info("Running periodic FAST data collection...")
			collectFastAll(dataDir, binance, false)
		}
	}()

	go func() {
		ticker := time.NewTicker(slowEvery)
		defer ticker.Stop()
		for range ticker.C {
			log.Info("Running periodic SLOW data collection...")
			collectSlowAll(dataDir, binance, okx, coinbase)
		}
	}()

	startAPIServer()
}

// FAST: only what ML really needs (stable + low rate-limit risk)
func collectFastAll(dataDir string, binance *collector.BinanceCollector, isStartup bool) {
	collectMarketData(binance, isStartup)
	saveFastDataToFiles(dataDir)

	// === compute features + signals (1h primary + 4h filter) ===
	computeAndPersistFeaturesAndSignals(dataDir)

	store.mu.Lock()
	store.LastUpdate = time.Now().UTC()
	store.mu.Unlock()

	log.Info("FAST data collection completed successfully!")
}

func aggregateTraderFlow(trades map[string][]models.Trade, now time.Time, window time.Duration) features.TraderFlow {
	var buyVol, sellVol float64
	cutoff := now.Add(-window)

	for _, ts := range trades {
		for _, t := range ts {
			tt := t.UpdateTime
			if tt.IsZero() {
				tt = t.CloseTime
			}
			if tt.IsZero() {
				tt = t.OpenTime
			}
			// 没时间戳就跳过（避免脏数据）
			if tt.IsZero() {
				continue
			}
			if tt.Before(cutoff) || tt.After(now) {
				continue
			}

			amt := t.Amount
			if amt == 0 && t.Price != 0 && t.Quantity != 0 {
				amt = t.Price * t.Quantity
			}
			if amt < 0 {
				amt = -amt
			}
			if amt == 0 {
				continue
			}

			switch strings.ToUpper(strings.TrimSpace(t.Side)) {
			case "BUY":
				buyVol += amt
			case "SELL":
				sellVol += amt
			}
		}
	}

	total := buyVol + sellVol
	if total == 0 {
		return features.TraderFlow{}
	}
	return features.TraderFlow{
		BuyRatio:  buyVol / total,
		SellRatio: sellVol / total,
		NetFlow:   (buyVol - sellVol) / total,
	}
}

// SLOW: heavy endpoints, do less frequently
func collectSlowAll(dataDir string, binance *collector.BinanceCollector, okx *collector.OKXCollector, coinbase *collector.CoinbaseCollector) {
	var wg sync.WaitGroup

	wg.Add(3)
	go func() { defer wg.Done(); collectBinanceData(binance) }()
	go func() { defer wg.Done(); collectOKXData(okx) }()
	go func() { defer wg.Done(); collectCoinbaseData(coinbase) }()
	wg.Wait()

	analyzePriceLevels()
	saveSlowDataToFiles(dataDir)

	// Exogenous features (ENABLE_EXOG_FEATURES=true to activate)
	if envBoolOrDefault("ENABLE_EXOG_FEATURES", false) {
		ec := exog.NewExogCollector()
		snap, err := ec.Collect("ETHUSDT")
		if err != nil {
			log.Warnf("exog: collect failed (non-fatal): %v", err)
		} else {
			if saveErr := exog.SaveExogSnapshot(dataDir, snap); saveErr != nil {
				log.Warnf("exog: save failed (non-fatal): %v", saveErr)
			} else {
				log.Infof("exog: saved snapshot for %s funding=%.6f oi=%.2f taker_buy=%.4f",
					snap.Symbol, snap.FundingRate, snap.OpenInterest, snap.TakerBuyRatio)
			}
		}
	}

	log.Info("SLOW data collection completed successfully!")
}

func computeAndPersistFeaturesAndSignals(dataDir string) {
	// copy needed data under lock, compute outside lock
	store.mu.RLock()
	kl1h := append([]models.OHLCV(nil), store.Klines["1h"]...)
	kl4h := append([]models.OHLCV(nil), store.Klines["4h"]...)
	kl1d := append([]models.OHLCV(nil), store.Klines["1d"]...)
	// deep-copy trades
	tradesCopy := make(map[string][]models.Trade, len(store.Trades))
	for k, v := range store.Trades {
		vc := append([]models.Trade(nil), v...)
		tradesCopy[k] = vc
	}
	store.mu.RUnlock()

	if len(kl1h) < 2 {
		log.Warn("Not enough 1h klines to compute features")
		return
	}

	now := time.Now().UTC()
	flow1h := aggregateTraderFlow(tradesCopy, now, 1*time.Hour)
	flow4h := aggregateTraderFlow(tradesCopy, now, 4*time.Hour)
	flow1d := aggregateTraderFlow(tradesCopy, now, 24*time.Hour)
	if len(runtimeFeatureColumns) == 0 {
		log.Warn("Feature schema columns not loaded; skip feature compute")
		return
	}
	snap, err := features.ComputeSnapshot(
		"ETHUSDT", "1h",
		kl1h, kl4h, kl1d,
		runtimeFeatureColumns,
		flow1h, flow4h, flow1d,
		now,
	)
	if err != nil {
		log.Warnf("ComputeSnapshot failed: %v", err)
		return
	}

	if _, err := features.WriteLatest(dataDir, snap); err != nil {
		log.Warnf("features.WriteLatest failed: %v", err)
	}
	if _, err := features.AppendHistory(dataDir, snap); err != nil {
		log.Warnf("features.AppendHistory failed: %v", err)
	}
	log.Infof("Feature snapshot aligned: schema=%d computed=%d missing=%d",
		snap.SchemaColumns, snap.ComputedCols, snap.MissingCols)

	// === RULES ===
	rulesRes := signal.RulesEngine(snap)

	// New: split latest
	if _, err := signal.WriteLatestRules(dataDir, &rulesRes); err != nil {
		log.Warnf("signal.WriteLatestRules failed: %v", err)
	}

	// history (rules)
	if _, err := signal.AppendHistory(dataDir, &rulesRes); err != nil {
		log.Warnf("signal.AppendHistory(rules) failed: %v", err)
	}

	// === ML (or fallback) ===
	mlRes := signal.MLOrFallback(context.Background(), snap)

	// New: split latest
	if _, err := signal.WriteLatestML(dataDir, &mlRes); err != nil {
		log.Warnf("signal.WriteLatestML failed: %v", err)
	}

	// Backward compatible latest: point it to "final" signal (ML or fallback)
	if _, err := signal.WriteLatest(dataDir, &mlRes); err != nil {
		log.Warnf("signal.WriteLatest(ml->compat latest) failed: %v", err)
	}

	// history (ml)
	if _, err := signal.AppendHistory(dataDir, &mlRes); err != nil {
		log.Warnf("signal.AppendHistory(ml) failed: %v", err)
	}

	// NEW: collector health/debug prediction log (optional)
	// If set, write ML result into the JSONL file (one line per run).
	if hp := strings.TrimSpace(os.Getenv("COLLECTOR_PREDICT_HEALTH_LOG_PATH")); hp != "" {
		if _, err := signal.AppendJSONL(hp, &mlRes); err != nil {
			log.Warnf("signal.AppendJSONL(health) failed: %v", err)
		}
	}

	// update in-memory latest
	store.mu.Lock()
	store.LatestFeatures1H = snap
	store.LatestSignalRules = &rulesRes
	store.LatestSignalML = &mlRes
	store.mu.Unlock()
}

func collectBinanceData(bn *collector.BinanceCollector) {
	traders, err := bn.GetTopTraders(topN)
	if err != nil {
		log.Errorf("[Binance] Failed to get top traders: %v", err)
		return
	}

	store.mu.Lock()
	store.Traders["binance"] = traders
	store.mu.Unlock()

	// Do NOT skip trade fetch when using simulated traders.
	// bn.GetTraderTrades() will generate mock trades for mock traderIDs (bn_trader_###).
	for _, trader := range traders {
		trades, err := bn.GetTraderTrades(trader.TraderID, tradeLimit)
		if err != nil {
			log.Warnf("[Binance] Failed to get trades for %s: %v", trader.TraderID, err)
			continue
		}
		store.mu.Lock()
		store.Trades[trader.TraderID] = trades
		store.mu.Unlock()
		time.Sleep(200 * time.Millisecond)
	}
}

func collectOKXData(okx *collector.OKXCollector) {
	traders, err := okx.GetTopTraders(topN)
	if err != nil {
		log.Errorf("[OKX] Failed to get top traders: %v", err)
		return
	}

	store.mu.Lock()
	store.Traders["okx"] = traders
	store.mu.Unlock()

	type job struct {
		traderID string
	}

	jobs := make(chan job)
	var wg sync.WaitGroup

	worker := func() {
		defer wg.Done()
		for j := range jobs {
			trades, err := okx.GetTraderTrades(j.traderID, tradeLimit)
			if err != nil {
				log.Warnf("[OKX] Failed to get trades for %s: %v", j.traderID, err)
				continue
			}
			store.mu.Lock()
			store.Trades[j.traderID] = trades
			store.mu.Unlock()
			time.Sleep(okxRateSleep)
		}
	}

	workers := okxMaxWorkers
	if workers < 1 {
		workers = 1
	}
	if workers > len(traders) {
		workers = len(traders)
	}
	wg.Add(workers)
	for i := 0; i < workers; i++ {
		go worker()
	}

	for _, t := range traders {
		jobs <- job{traderID: t.TraderID}
	}
	close(jobs)

	wg.Wait()
}

func collectCoinbaseData(cb *collector.CoinbaseCollector) {
	traders, err := cb.GetTopTraders(topN)
	if err != nil {
		log.Errorf("[Coinbase] Failed to get top traders: %v", err)
		return
	}

	store.mu.Lock()
	store.Traders["coinbase"] = traders
	store.mu.Unlock()

	for _, trader := range traders {
		trades, err := cb.GetTraderTrades(trader.TraderID, tradeLimit)
		if err != nil {
			log.Warnf("[Coinbase] Failed to get trades for %s: %v", trader.TraderID, err)
			continue
		}
		store.mu.Lock()
		store.Trades[trader.TraderID] = trades
		store.mu.Unlock()

		time.Sleep(100 * time.Millisecond)
	}
}

func shouldUseLookbackThisRun(isStartup bool) bool {
	mode := strings.TrimSpace(strings.ToLower(os.Getenv("KLINES_LOOKBACK_MODE")))
	if mode == "" {
		mode = klinesLookbackModeOnStartup
	}

	switch mode {
	case klinesLookbackModeAlways:
		return true
	case klinesLookbackModeOff:
		return false
	case klinesLookbackModeOnStartup:
		return isStartup
	default:
		return true
	}
}

func collectMarketData(bn *collector.BinanceCollector, isStartup bool) {
	market, err := bn.GetCurrentPrice("ETHUSDT")
	if err != nil {
		log.Warnf("Failed to get current price: %v", err)
	} else {
		store.mu.Lock()
		store.MarketData = market
		store.mu.Unlock()
	}

	useLookback := shouldUseLookbackThisRun(isStartup)
	log.Infof("Klines lookback enabled for this run: %v (isStartup=%v)", useLookback, isStartup)

	// Lookback defaults (can be overridden by env). If <=0 => fallback to latest-window only.
	look15m := envIntOrDefault("KLINES_15M_LOOKBACK_DAYS", 90)
	look1h := envIntOrDefault("KLINES_1H_LOOKBACK_DAYS", 180)
	look4h := envIntOrDefault("KLINES_4H_LOOKBACK_DAYS", 365)
	look1d := envIntOrDefault("KLINES_1D_LOOKBACK_DAYS", 730)

	intervals := []string{"1m", "5m", "15m", "1h", "4h", "1d"}

	for _, interval := range intervals {
		var (
			klines []models.OHLCV
			err    error
		)

		switch interval {
		case "15m":
			if useLookback && look15m > 0 {
				klines, err = bn.GetKlinesLookback("ETHUSDT", "15m", look15m)
			} else {
				klines, err = bn.GetKlines("ETHUSDT", "15m", 500)
			}
		case "1h":
			if useLookback && look1h > 0 {
				klines, err = bn.GetKlinesLookback("ETHUSDT", "1h", look1h)
			} else {
				klines, err = bn.GetKlines("ETHUSDT", "1h", 500)
			}
		case "4h":
			if useLookback && look4h > 0 {
				klines, err = bn.GetKlinesLookback("ETHUSDT", "4h", look4h)
			} else {
				klines, err = bn.GetKlines("ETHUSDT", "4h", 500)
			}
		case "1d":
			if useLookback && look1d > 0 {
				klines, err = bn.GetKlinesLookback("ETHUSDT", "1d", look1d)
			} else {
				klines, err = bn.GetKlines("ETHUSDT", "1d", 500)
			}
		default:
			// 1m/5m: keep light
			klines, err = bn.GetKlines("ETHUSDT", interval, 500)
		}

		if err != nil {
			log.Warnf("Failed to get %s klines: %v", interval, err)
			continue
		}
		store.mu.Lock()
		store.Klines[interval] = klines
		store.mu.Unlock()

		// Spread requests a bit
		time.Sleep(120 * time.Millisecond)
	}
}

func analyzePriceLevels() {
	store.mu.Lock()
	defer store.mu.Unlock()

	if store.MarketData == nil {
		// Ensure JSON encodes as [] rather than null
		store.PriceLevels = make([]models.PriceLevel, 0)
		return
	}

	currentPrice := store.MarketData.Price
	if currentPrice == 0 {
		currentPrice = 2500.0
	}
	priceStep := 50.0
	minPrice := currentPrice - 500.0
	maxPrice := currentPrice + 500.0
	levelMap := make(map[float64]*models.PriceLevel)

	now := time.Now().UTC()
	for price := minPrice; price <= maxPrice; price += priceStep {
		levelMap[price] = &models.PriceLevel{
			PriceMin:  price,
			PriceMax:  price + priceStep,
			Buyers:    []string{},
			Sellers:   []string{},
			Timestamp: now,
		}
	}

	for _, trades := range store.Trades {
		for _, trade := range trades {
			if trade.Symbol != "ETHUSDT" && trade.Symbol != "ETH-USDT-SWAP" && trade.Symbol != "ETH-USD" {
				continue
			}
			if trade.Price <= 0 || trade.Amount == 0 {
				continue
			}
			levelKey := float64(int(trade.Price/priceStep)) * priceStep
			level, exists := levelMap[levelKey]
			if !exists {
				continue
			}
			if trade.Side == "BUY" {
				level.Buyers = append(level.Buyers, trade.TraderID)
				level.BuyVolume += trade.Amount
			} else {
				level.Sellers = append(level.Sellers, trade.TraderID)
				level.SellVolume += trade.Amount
			}
		}
	}

	// IMPORTANT: initialize as empty slice so JSON encodes as [] not null
	levels := make([]models.PriceLevel, 0)
	for _, level := range levelMap {
		if len(level.Buyers) > 0 || len(level.Sellers) > 0 {
			levels = append(levels, *level)
		}
	}
	store.PriceLevels = levels
}

func saveFastDataToFiles(dataDir string) {
	store.mu.RLock()
	defer store.mu.RUnlock()

	// Only fast-changing things needed by ML pipeline
	if store.MarketData != nil {
		saveJSON(filepath.Join(dataDir, "market_data.json"), store.MarketData)
	}

	for interval, klines := range store.Klines {
		filename := fmt.Sprintf("klines_%s.json", interval)
		saveJSON(filepath.Join(dataDir, filename), klines)
	}

	log.Info("FAST data saved to files successfully")
}

func saveSlowDataToFiles(dataDir string) {
	store.mu.RLock()
	defer store.mu.RUnlock()

	saveJSON(filepath.Join(dataDir, "traders.json"), store.Traders)
	saveJSON(filepath.Join(dataDir, "trades.json"), store.Trades)
	saveJSON(filepath.Join(dataDir, "price_levels.json"), store.PriceLevels)

	log.Info("SLOW data saved to files successfully")
}

func saveJSON(filename string, data interface{}) {
	_ = os.MkdirAll(filepath.Dir(filename), 0o775)

	tmp := filename + ".tmp"
	file, err := os.Create(tmp)
	if err != nil {
		log.Errorf("Failed to create tmp file %s: %v", tmp, err)
		return
	}

	encoder := json.NewEncoder(file)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(data); err != nil {
		_ = file.Close()
		_ = os.Remove(tmp)
		log.Errorf("Failed to encode JSON to %s (target=%s): %v", tmp, filename, err)
		return
	}

	if err := file.Close(); err != nil {
		_ = os.Remove(tmp)
		log.Errorf("Failed to close tmp file %s: %v", tmp, err)
		return
	}

	if err := os.Rename(tmp, filename); err != nil {
		_ = os.Remove(tmp)
		log.Errorf("Failed to rename tmp file %s -> %s: %v", tmp, filename, err)
		return
	}
}

func startAPIServer() {
	mux := http.NewServeMux()
	mux.HandleFunc("/api/traders", handleTraders)
	mux.HandleFunc("/api/trades", handleTrades)
	mux.HandleFunc("/api/price-levels", handlePriceLevels)
	mux.HandleFunc("/api/market", handleMarket)
	mux.HandleFunc("/api/klines", handleKlines)
	mux.HandleFunc("/api/status", handleStatus)
	mux.HandleFunc("/api/all-data", handleAllData)
	mux.HandleFunc("/api/healthz", handleHealthz)

	// New: features & signal APIs
	mux.HandleFunc("/api/features/latest", handleFeaturesLatest)
	mux.HandleFunc("/api/features/history", handleFeaturesHistory)
	mux.HandleFunc("/api/signal", handleSignal)

	handler := corsMiddleware(mux)

	port := os.Getenv("COLLECTOR_PORT")
	if port == "" {
		port = "8080"
	}

	log.Infof("API server starting on port %s", port)
	log.Infof("Endpoints: /api/traders, /api/trades, /api/price-levels, /api/market, /api/klines, /api/status, /api/healthz, /api/features/*, /api/signal")

	if err := http.ListenAndServe(":"+port, handler); err != nil {
		log.Fatalf("API server failed: %v", err)
	}
}

func corsMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		if r.Method == "OPTIONS" {
			w.WriteHeader(http.StatusOK)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func handleTraders(w http.ResponseWriter, r *http.Request) {
	store.mu.RLock()
	defer store.mu.RUnlock()
	writeJSON(w, store.Traders)
}

// build a traderID set for an exchange (requires store.mu already RLocked)
func traderIDsForExchangeLocked(exchange string) map[string]struct{} {
	out := make(map[string]struct{})
	trs := store.Traders[exchange]
	for _, t := range trs {
		if t.TraderID == "" {
			continue
		}
		out[t.TraderID] = struct{}{}
	}
	return out
}

func handleTrades(w http.ResponseWriter, r *http.Request) {
	store.mu.RLock()
	defer store.mu.RUnlock()

	exchange := strings.TrimSpace(strings.ToLower(r.URL.Query().Get("exchange")))
	symbol := strings.TrimSpace(r.URL.Query().Get("symbol"))

	if exchange == "" {
		// Backward compatible: return all trades
		if symbol == "" {
			writeJSON(w, store.Trades)
			return
		}
		// If symbol provided without exchange, filter all
		filtered := make(map[string][]models.Trade)
		for traderID, trades := range store.Trades {
			out := make([]models.Trade, 0, len(trades))
			for _, t := range trades {
				if symbol == "" || t.Symbol == symbol {
					out = append(out, t)
				}
			}
			if len(out) > 0 {
				filtered[traderID] = out
			}
		}
		writeJSON(w, filtered)
		return
	}

	ids := traderIDsForExchangeLocked(exchange)
	if len(ids) == 0 {
		// Unknown exchange or no traders yet -> empty map
		writeJSON(w, map[string][]models.Trade{})
		return
	}

	filtered := make(map[string][]models.Trade)
	for traderID, trades := range store.Trades {
		if _, ok := ids[traderID]; !ok {
			continue
		}
		if symbol == "" {
			filtered[traderID] = trades
			continue
		}
		out := make([]models.Trade, 0, len(trades))
		for _, t := range trades {
			if t.Symbol == symbol {
				out = append(out, t)
			}
		}
		if len(out) > 0 {
			filtered[traderID] = out
		}
	}

	writeJSON(w, filtered)
}

func handlePriceLevels(w http.ResponseWriter, r *http.Request) {
	store.mu.RLock()
	defer store.mu.RUnlock()
	if store.PriceLevels == nil {
		// defensive: always return [] not null
		writeJSON(w, make([]models.PriceLevel, 0))
		return
	}
	writeJSON(w, store.PriceLevels)
}

func handleMarket(w http.ResponseWriter, r *http.Request) {
	store.mu.RLock()
	defer store.mu.RUnlock()
	writeJSON(w, store.MarketData)
}

func handleKlines(w http.ResponseWriter, r *http.Request) {
	store.mu.RLock()
	defer store.mu.RUnlock()
	writeJSON(w, store.Klines)
}

func handleStatus(w http.ResponseWriter, r *http.Request) {
	store.mu.RLock()
	defer store.mu.RUnlock()

	traderCounts := map[string]int{
		"binance":  len(store.Traders["binance"]),
		"okx":      len(store.Traders["okx"]),
		"coinbase": len(store.Traders["coinbase"]),
	}

	// Trade counts by exchange
	tradeCounts := map[string]int{
		"binance":  0,
		"okx":      0,
		"coinbase": 0,
	}
	for _, t := range store.Traders["binance"] {
		tradeCounts["binance"] += len(store.Trades[t.TraderID])
	}
	for _, t := range store.Traders["okx"] {
		tradeCounts["okx"] += len(store.Trades[t.TraderID])
	}
	for _, t := range store.Traders["coinbase"] {
		tradeCounts["coinbase"] += len(store.Trades[t.TraderID])
	}

	priceLevelsCount := 0
	if store.PriceLevels != nil {
		priceLevelsCount = len(store.PriceLevels)
	}

	klinesCounts := make(map[string]int, len(store.Klines))
	for k, v := range store.Klines {
		klinesCounts[k] = len(v)
	}

	status := map[string]interface{}{
		"status": "running",

		"last_update": store.LastUpdate,
		"started_at":  store.StartedAt,
		"uptime_sec":  int64(time.Since(store.StartedAt).Seconds()),

		"has_market_data": store.MarketData != nil,

		"trader_counts": traderCounts,
		"trade_counts":  tradeCounts,

		"price_levels_count": priceLevelsCount,

		"klines_counts": klinesCounts,

		"has_features_1h":  store.LatestFeatures1H != nil,
		"has_signal_rules": store.LatestSignalRules != nil,
		"has_signal_ml":    store.LatestSignalML != nil,
	}
	writeJSON(w, status)
}

func handleAllData(w http.ResponseWriter, r *http.Request) {
	store.mu.RLock()
	defer store.mu.RUnlock()

	allData := map[string]interface{}{
		"traders":      store.Traders,
		"trades":       store.Trades,
		"price_levels": store.PriceLevels,
		"market_data":  store.MarketData,
		"klines":       store.Klines,
		"last_update":  store.LastUpdate,
	}
	writeJSON(w, allData)
}

// Configurable STRICT health:
// - ok=false if staleness exceeds HEALTH_STALENESS_MAX (default 180s)
// - ok=false if required files are missing
// - ok=false if required files are older than HEALTH_STALENESS_MAX
// - ok=false if signals_1h_latest.json missing AND HEALTH_REQUIRE_SIGNALS=true (default true)
// - ok=false if split signals missing AND HEALTH_REQUIRE_SIGNALS_SPLIT=true (default false)
func handleHealthz(w http.ResponseWriter, r *http.Request) {
	store.mu.RLock()
	last := store.LastUpdate
	started := store.StartedAt
	store.mu.RUnlock()

	stalenessSec := int64(0)
	if !last.IsZero() {
		stalenessSec = int64(time.Since(last).Seconds())
	}

	dataDir := runtimeDataDir
	if dataDir == "" {
		dataDir = envOrDefault("DATA_DIR", defaultDataDir)
	}

	now := time.Now().UTC()
	staleMax := envDurationOrDefault("HEALTH_STALENESS_MAX", 180*time.Second)
	requireSignals := envBoolOrDefault("HEALTH_REQUIRE_SIGNALS", true)
	requireSignalsSplit := envBoolOrDefault("HEALTH_REQUIRE_SIGNALS_SPLIT", false)

	kl1hFresh, kl1h := fileFreshOK(filepath.Join(dataDir, "klines_1h.json"), now, staleMax)
	featFresh, featLatest := fileFreshOK(filepath.Join(dataDir, "features", "features_1h_latest.json"), now, staleMax)

	// Backward compatible
	sigLatestFresh, sigLatest := fileFreshOK(filepath.Join(dataDir, "signals", "signals_1h_latest.json"), now, staleMax)

	// New split files
	sigLatestRulesFresh, sigLatestRules := fileFreshOK(filepath.Join(dataDir, "signals", "signals_1h_latest_rules.json"), now, staleMax)
	sigLatestMLFresh, sigLatestML := fileFreshOK(filepath.Join(dataDir, "signals", "signals_1h_latest_ml.json"), now, staleMax)

	ok := true

	// in-memory staleness
	if time.Duration(stalenessSec)*time.Second > staleMax {
		ok = false
	}

	// file freshness
	if !kl1hFresh {
		ok = false
	}
	if !featFresh {
		ok = false
	}
	if requireSignals && !sigLatestFresh {
		ok = false
	}
	if requireSignalsSplit {
		if !sigLatestRulesFresh {
			ok = false
		}
		if !sigLatestMLFresh {
			ok = false
		}
	}

	files := map[string]interface{}{
		"klines_1h":            kl1h,
		"features_latest":      featLatest,
		"signals_latest":       sigLatest,
		"signals_latest_rules": sigLatestRules,
		"signals_latest_ml":    sigLatestML,
	}

	resp := map[string]interface{}{
		"ok":            ok,
		"last_update":   last,
		"started_at":    started,
		"staleness_sec": stalenessSec,

		"data_dir": dataDir,

		"fast_interval": func() string {
			if runtimeFastEvery > 0 {
				return runtimeFastEvery.String()
			}
			return ""
		}(),
		"slow_interval": func() string {
			if runtimeSlowEvery > 0 {
				return runtimeSlowEvery.String()
			}
			return ""
		}(),

		"health_now":                   now.Format(time.RFC3339Nano),
		"health_staleness_max":         staleMax.String(),
		"health_require_signals":       requireSignals,
		"health_require_signals_split": requireSignalsSplit,

		"files": files,
	}
	writeJSON(w, resp)
}

func handleFeaturesLatest(w http.ResponseWriter, r *http.Request) {
	store.mu.RLock()
	defer store.mu.RUnlock()

	if store.LatestFeatures1H == nil {
		http.Error(w, "no features computed yet", http.StatusNotFound)
		return
	}
	writeJSON(w, store.LatestFeatures1H)
}

// Lightweight history API: reads JSONL and returns last N rows.
func handleFeaturesHistory(w http.ResponseWriter, r *http.Request) {
	dataDir := envOrDefault("DATA_DIR", defaultDataDir)
	limit := envIntOrDefault("FEATURES_HISTORY_LIMIT_DEFAULT", 500)
	if v := strings.TrimSpace(r.URL.Query().Get("limit")); v != "" {
		if i, err := strconv.Atoi(v); err == nil && i > 0 && i <= 5000 {
			limit = i
		}
	}

	p := filepath.Join(dataDir, "features", "features_1h_history.jsonl")
	f, err := os.Open(p)
	if err != nil {
		http.Error(w, "history file not found", http.StatusNotFound)
		return
	}
	defer f.Close()

	all := make([]features.FeatureSnapshot, 0, limit)
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		var snap features.FeatureSnapshot
		if err := json.Unmarshal(sc.Bytes(), &snap); err != nil {
			continue
		}
		all = append(all, snap)
	}
	if err := sc.Err(); err != nil {
		http.Error(w, "failed to read history file", http.StatusInternalServerError)
		return
	}

	if len(all) > limit {
		all = all[len(all)-limit:]
	}
	writeJSON(w, all)
}

func handleSignal(w http.ResponseWriter, r *http.Request) {
	engine := strings.TrimSpace(strings.ToLower(r.URL.Query().Get("engine")))
	if engine == "" {
		engine = "rules"
	}

	store.mu.RLock()
	snap := store.LatestFeatures1H
	rules := store.LatestSignalRules
	ml := store.LatestSignalML
	store.mu.RUnlock()

	if snap == nil {
		http.Error(w, "no features computed yet", http.StatusNotFound)
		return
	}

	switch engine {
	case "rules":
		if rules != nil {
			writeJSON(w, rules)
			return
		}
		res := signal.RulesEngine(snap)
		writeJSON(w, res)
		return
	case "ml":
		if ml != nil {
			writeJSON(w, ml)
			return
		}
		res := signal.MLOrFallback(context.Background(), snap)
		writeJSON(w, res)
		return
	default:
		http.Error(w, "invalid engine (use rules|ml)", http.StatusBadRequest)
		return
	}
}

func writeJSON(w http.ResponseWriter, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	encoder := json.NewEncoder(w)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(data); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}
