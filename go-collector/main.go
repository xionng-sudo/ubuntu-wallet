package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"time"

	log "github.com/sirupsen/logrus"
	"github.com/ubuntu-wallet/go-collector/collector"
	"github.com/ubuntu-wallet/go-collector/models"
)

const (
	dataDir    = "../data"
	topN       = 50
	tradeLimit = 100
)

// DataStore holds all collected data in memory
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

func main() {
	log.SetFormatter(&log.TextFormatter{
		FullTimestamp: true,
	})
	log.SetLevel(log.InfoLevel)

	log.Info("========================================")
	log.Info("  ETH Crypto Trader Data Collector")
	log.Info("========================================")

	// Ensure data directory exists
	if err := os.MkdirAll(dataDir, 0755); err != nil {
		log.Fatalf("Failed to create data directory: %v", err)
	}

	// Initialize collectors
	binance := collector.NewBinanceCollector(
		os.Getenv("BINANCE_API_KEY"),
		os.Getenv("BINANCE_API_SECRET"),
	)
	okx := collector.NewOKXCollector(
		os.Getenv("OKX_API_KEY"),
		os.Getenv("OKX_API_SECRET"),
		os.Getenv("OKX_PASSPHRASE"),
	)
	coinbase := collector.NewCoinbaseCollector(
		os.Getenv("COINBASE_API_KEY"),
		os.Getenv("COINBASE_API_SECRET"),
	)

	// Initial data collection
	log.Info("Starting initial data collection...")
	collectAllData(binance, okx, coinbase)

	// Start periodic collection in background
	go func() {
		ticker := time.NewTicker(5 * time.Minute)
		defer ticker.Stop()
		for range ticker.C {
			log.Info("Running periodic data collection...")
			collectAllData(binance, okx, coinbase)
		}
	}()

	// Start HTTP API server
	startAPIServer()
}

func collectAllData(binance *collector.BinanceCollector, okx *collector.OKXCollector, coinbase *collector.CoinbaseCollector) {
	var wg sync.WaitGroup

	// Collect from all exchanges in parallel
	wg.Add(3)
	go func() {
		defer wg.Done()
		collectExchangeData("binance", binance)
	}()
	go func() {
		defer wg.Done()
		collectOKXData(okx)
	}()
	go func() {
		defer wg.Done()
		collectCoinbaseData(coinbase)
	}()
	wg.Wait()

	// Collect market data (Binance as primary source)
	collectMarketData(binance)

	// Analyze price levels
	analyzePriceLevels()

	// Save data to files
	saveDataToFiles()

	store.mu.Lock()
	store.LastUpdate = time.Now()
	store.mu.Unlock()

	log.Info("Data collection completed successfully!")
}

func collectExchangeData(exchange string, bn *collector.BinanceCollector) {
	traders, err := bn.GetTopTraders(topN)
	if err != nil {
		log.Errorf("[%s] Failed to get top traders: %v", exchange, err)
		return
	}

	store.mu.Lock()
	store.Traders[exchange] = traders
	store.mu.Unlock()

	// Fetch trades for each trader
	for _, trader := range traders {
		trades, err := bn.GetTraderTrades(trader.TraderID, tradeLimit)
		if err != nil {
			log.Warnf("[%s] Failed to get trades for %s: %v", exchange, trader.TraderID, err)
			continue
		}
		store.mu.Lock()
		store.Trades[trader.TraderID] = trades
		store.mu.Unlock()

		// Rate limiting
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

	for _, trader := range traders {
		trades, err := okx.GetTraderTrades(trader.TraderID, tradeLimit)
		if err != nil {
			log.Warnf("[OKX] Failed to get trades for %s: %v", trader.TraderID, err)
			continue
		}
		store.mu.Lock()
		store.Trades[trader.TraderID] = trades
		store.mu.Unlock()

		time.Sleep(200 * time.Millisecond)
	}
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
	// Get current price
	market, err := bn.GetCurrentPrice("ETHUSDT")
	if err != nil {
		log.Warnf("Failed to get current price: %v", err)
	} else {
		store.mu.Lock()
		store.MarketData = market
		store.mu.Unlock()
	}

	// Get klines for different intervals
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

	// Define price ranges (every $50)
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

	// Categorize trades into price levels
	for _, trades := range store.Trades {
		for _, trade := range trades {
			if trade.Symbol != "ETHUSDT" && trade.Symbol != "ETH-USDT-SWAP" && trade.Symbol != "ETH-USD" {
				continue
			}
			// Find the matching price level
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

func saveDataToFiles() {
	store.mu.RLock()
	defer store.mu.RUnlock()

	// Save traders
	saveJSON(filepath.Join(dataDir, "traders.json"), store.Traders)

	// Save trades
	saveJSON(filepath.Join(dataDir, "trades.json"), store.Trades)

	// Save price levels
	saveJSON(filepath.Join(dataDir, "price_levels.json"), store.PriceLevels)

	// Save market data
	if store.MarketData != nil {
		saveJSON(filepath.Join(dataDir, "market_data.json"), store.MarketData)
	}

	// Save klines
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

// HTTP API handlers
func startAPIServer() {
	mux := http.NewServeMux()
	mux.HandleFunc("/api/traders", handleTraders)
	mux.HandleFunc("/api/trades", handleTrades)
	mux.HandleFunc("/api/price-levels", handlePriceLevels)
	mux.HandleFunc("/api/market", handleMarket)
	mux.HandleFunc("/api/klines", handleKlines)
	mux.HandleFunc("/api/status", handleStatus)
	mux.HandleFunc("/api/all-data", handleAllData)

	// CORS middleware
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

	exchange := r.URL.Query().Get("exchange")
	if exchange != "" {
		// Filter trades by exchange
		filtered := make(map[string][]models.Trade)
		for id, trades := range store.Trades {
			var exchangeTrades []models.Trade
			for _, t := range trades {
				if t.Exchange == exchange {
					exchangeTrades = append(exchangeTrades, t)
				}
			}
			if len(exchangeTrades) > 0 {
				filtered[id] = exchangeTrades
			}
		}
		writeJSON(w, filtered)
		return
	}
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

	interval := r.URL.Query().Get("interval")
	if interval != "" {
		if klines, ok := store.Klines[interval]; ok {
			writeJSON(w, klines)
			return
		}
	}
	writeJSON(w, store.Klines)
}

func handleStatus(w http.ResponseWriter, r *http.Request) {
	store.mu.RLock()
	defer store.mu.RUnlock()

	status := map[string]interface{}{
		"status":      "running",
		"last_update": store.LastUpdate,
		"traders_count": map[string]int{
			"binance":  len(store.Traders["binance"]),
			"okx":      len(store.Traders["okx"]),
			"coinbase": len(store.Traders["coinbase"]),
		},
		"total_trades":    len(store.Trades),
		"price_levels":    len(store.PriceLevels),
		"kline_intervals": len(store.Klines),
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
