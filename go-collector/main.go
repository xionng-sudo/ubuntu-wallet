package main

import (
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
	"github.com/ubuntu-wallet/go-collector/models"
)

const (
	// ✅ 默认值保持不变：仍然是 ../data
	defaultDataDir = "../data"

	topN       = 50
	tradeLimit = 100

	okxMaxWorkers = 6
	okxRateSleep  = 80 * time.Millisecond
)

type DataStore struct {
	mu          sync.RWMutex
	Traders     map[string][]models.Trader `json:"traders"`
	Trades      map[string][]models.Trade  `json:"trades"`
	PriceLevels []models.PriceLevel        `json:"price_levels"`
	MarketData  *models.MarketData         `json:"market_data"`
	Klines      map[string][]models.OHLCV  `json:"klines"`
	LastUpdate  time.Time                  `json:"last_update"`
}

var store = &DataStore{
	Traders: make(map[string][]models.Trader),
	Trades:  make(map[string][]models.Trade),
	Klines:  make(map[string][]models.OHLCV),
}

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

func main() {
	log.SetFormatter(&log.TextFormatter{FullTimestamp: true})
	log.SetLevel(log.InfoLevel)

	log.Info("========================================")
	log.Info("  ETH Crypto Trader Data Collector")
	log.Info("========================================")
	log.Infof("PID: %d", os.Getpid())
	if v := os.Getenv("ALLOW_MOCK"); v == "" {
		log.Info("ALLOW_MOCK is not set (default: true)")
	} else {
		log.Infof("ALLOW_MOCK=%s", v)
	}

	// ✅ 从环境变量读取数据目录（与 Python 统一）
	// 仍然兼容旧行为：没配置则用 ../data
	dataDir := envOrDefault("DATA_DIR", defaultDataDir)
	log.Infof("DATA_DIR=%s", dataDir)

	if err := os.MkdirAll(dataDir, 0755); err != nil {
		log.Fatalf("Failed to create data directory: %v", err)
	}

	binance := collector.NewBinanceCollector(os.Getenv("BINANCE_API_KEY"), os.Getenv("BINANCE_API_SECRET"))
	okx := collector.NewOKXCollector(os.Getenv("OKX_API_KEY"), os.Getenv("OKX_API_SECRET"), os.Getenv("OKX_PASSPHRASE"))
	coinbase := collector.NewCoinbaseCollector(os.Getenv("COINBASE_API_KEY"), os.Getenv("COINBASE_API_SECRET"))

	log.Info("Starting initial data collection...")
	collectAllData(dataDir, binance, okx, coinbase)

	go func() {
		ticker := time.NewTicker(5 * time.Minute)
		defer ticker.Stop()
		for range ticker.C {
			log.Info("Running periodic data collection...")
			collectAllData(dataDir, binance, okx, coinbase)
		}
	}()

	startAPIServer()
}

func collectAllData(dataDir string, binance *collector.BinanceCollector, okx *collector.OKXCollector, coinbase *collector.CoinbaseCollector) {
	var wg sync.WaitGroup

	wg.Add(3)
	go func() { defer wg.Done(); collectBinanceData(binance) }()
	go func() { defer wg.Done(); collectOKXData(okx) }()
	go func() { defer wg.Done(); collectCoinbaseData(coinbase) }()
	wg.Wait()

	collectMarketData(binance)
	analyzePriceLevels()
	saveDataToFiles(dataDir)

	store.mu.Lock()
	store.LastUpdate = time.Now()
	store.mu.Unlock()

	log.Info("Data collection completed successfully!")
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

	// When Binance leaderboard is unavailable, bn returns simulated traders and we skip fetching trades.
	// (Market data still comes from Binance via the public api.binance.com endpoints.)
	if len(traders) > 0 && len(traders[0].TraderID) >= 9 && traders[0].TraderID[:9] == "bn_trader" {
		log.Warn("[Binance] Traders look like simulated data; skipping Binance trade fetch")
		return
	}

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

			// soft rate limiting
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

func collectMarketData(bn *collector.BinanceCollector) {
	market, err := bn.GetCurrentPrice("ETHUSDT")
	if err != nil {
		log.Warnf("Failed to get current price: %v", err)
	} else {
		store.mu.Lock()
		store.MarketData = market
		store.mu.Unlock()
	}

	intervals := []string{"1m", "5m", "15m", "1h", "4h", "1d"}
	for _, interval := range intervals {
		klines, err := bn.GetKlines("ETHUSDT", interval, 500)
		if err != nil {
			log.Warnf("Failed to get %s klines: %v", interval, err)
			continue
		}
		store.mu.Lock()
		store.Klines[interval] = klines
		store.mu.Unlock()
		time.Sleep(100 * time.Millisecond)
	}
}

func analyzePriceLevels() {
	store.mu.Lock()
	defer store.mu.Unlock()

	if store.MarketData == nil {
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

	for price := minPrice; price <= maxPrice; price += priceStep {
		levelMap[price] = &models.PriceLevel{
			PriceMin:  price,
			PriceMax:  price + priceStep,
			Buyers:    []string{},
			Sellers:   []string{},
			Timestamp: time.Now(),
		}
	}

	for _, trades := range store.Trades {
		for _, trade := range trades {
			if trade.Symbol != "ETHUSDT" && trade.Symbol != "ETH-USDT-SWAP" && trade.Symbol != "ETH-USD" {
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

	var levels []models.PriceLevel
	for _, level := range levelMap {
		if len(level.Buyers) > 0 || len(level.Sellers) > 0 {
			levels = append(levels, *level)
		}
	}
	store.PriceLevels = levels
}

func saveDataToFiles(dataDir string) {
	store.mu.RLock()
	defer store.mu.RUnlock()

	saveJSON(filepath.Join(dataDir, "traders.json"), store.Traders)
	saveJSON(filepath.Join(dataDir, "trades.json"), store.Trades)
	saveJSON(filepath.Join(dataDir, "price_levels.json"), store.PriceLevels)

	if store.MarketData != nil {
		saveJSON(filepath.Join(dataDir, "market_data.json"), store.MarketData)
	}

	for interval, klines := range store.Klines {
		filename := fmt.Sprintf("klines_%s.json", interval)
		saveJSON(filepath.Join(dataDir, filename), klines)
	}

	log.Info("Data saved to files successfully")
}

func saveJSON(filename string, data interface{}) {
	file, err := os.Create(filename)
	if err != nil {
		log.Errorf("Failed to create file %s: %v", filename, err)
		return
	}
	defer file.Close()

	encoder := json.NewEncoder(file)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(data); err != nil {
		log.Errorf("Failed to encode JSON to %s: %v", filename, err)
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

	handler := corsMiddleware(mux)

	port := os.Getenv("COLLECTOR_PORT")
	if port == "" {
		port = "8080"
	}

	log.Infof("API server starting on port %s", port)
	log.Infof("Endpoints: /api/traders, /api/trades, /api/price-levels, /api/market, /api/klines, /api/status")

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

func handleTrades(w http.ResponseWriter, r *http.Request) {
	store.mu.RLock()
	defer store.mu.RUnlock()
	writeJSON(w, store.Trades)
}

func handlePriceLevels(w http.ResponseWriter, r *http.Request) {
	store.mu.RLock()
	defer store.mu.RUnlock()
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

	status := map[string]interface{}{
		"status":      "running",
		"last_update": store.LastUpdate,
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

func writeJSON(w http.ResponseWriter, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	encoder := json.NewEncoder(w)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(data); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}
