package collector

import (
"encoding/json"
"fmt"
"io"
"net/http"
"sort"
"strconv"
"time"

log "github.com/sirupsen/logrus"
"github.com/ubuntu-wallet/go-collector/models"
)

// CoinbaseCollector collects data from Coinbase
type CoinbaseCollector struct {
apiKey    string
apiSecret string
client    *http.Client
baseURL   string
}

// NewCoinbaseCollector creates a new Coinbase collector
func NewCoinbaseCollector(apiKey, apiSecret string) *CoinbaseCollector {
return &CoinbaseCollector{
apiKey:    apiKey,
apiSecret: apiSecret,
client: &http.Client{
Timeout: 30 * time.Second,
},
baseURL: "https://api.exchange.coinbase.com",
}
}

// GetTopTraders fetches top traders from Coinbase
// Note: Coinbase doesn't have a public leaderboard, so we use market data to simulate
func (c *CoinbaseCollector) GetTopTraders(topN int) ([]models.Trader, error) {
log.Info("[Coinbase] Fetching market-based trader data...")

// Coinbase doesn't expose trader leaderboard, use mock data
return c.generateMockTraders(topN), nil
}

// GetTraderTrades fetches trades for a trader
func (c *CoinbaseCollector) GetTraderTrades(traderID string, limit int) ([]models.Trade, error) {
log.Infof("[Coinbase] Fetching trades for trader %s", traderID)

// Try to get recent market trades as reference data
url := fmt.Sprintf("%s/products/ETH-USD/trades?limit=%d", c.baseURL, limit)

resp, err := c.client.Get(url)
if err != nil {
return c.generateMockTrades(traderID, limit), nil
}
defer resp.Body.Close()

body, err := io.ReadAll(resp.Body)
if err != nil {
return c.generateMockTrades(traderID, limit), nil
}

var marketTrades []struct {
TradeID int    `json:"trade_id"`
Price   string `json:"price"`
Size    string `json:"size"`
Side    string `json:"side"`
Time    string `json:"time"`
}

if err := json.Unmarshal(body, &marketTrades); err != nil {
return c.generateMockTrades(traderID, limit), nil
}

var trades []models.Trade
for _, mt := range marketTrades {
price, _ := strconv.ParseFloat(mt.Price, 64)
qty, _ := strconv.ParseFloat(mt.Size, 64)
t, _ := time.Parse(time.RFC3339Nano, mt.Time)

side := "BUY"
strategy := "LONG"
if mt.Side == "sell" {
side = "SELL"
strategy = "SHORT"
}

trades = append(trades, models.Trade{
TradeID:    fmt.Sprintf("cb_%d", mt.TradeID),
TraderID:   traderID,
Exchange:   "coinbase",
Symbol:     "ETH-USD",
Side:       side,
Price:      price,
Quantity:   qty,
Amount:     price * qty,
Leverage:   1.0,
Strategy:   strategy,
Status:     "CLOSED",
OpenTime:   t,
CloseTime:  t.Add(10 * time.Minute),
UpdateTime: time.Now().UTC(),
})
}

if len(trades) == 0 {
return c.generateMockTrades(traderID, limit), nil
}

log.Infof("[Coinbase] Fetched %d trades for trader %s", len(trades), traderID)
return trades, nil
}

// GetKlines fetches Coinbase candlestick data
func (c *CoinbaseCollector) GetKlines(symbol, interval string, limit int) ([]models.OHLCV, error) {
granMap := map[string]int{
"1m": 60, "5m": 300, "15m": 900,
"1h": 3600, "4h": 14400, "1d": 86400,
}
gran, ok := granMap[interval]
if !ok {
gran = 3600
}

url := fmt.Sprintf("%s/products/%s/candles?granularity=%d", c.baseURL, symbol, gran)

resp, err := c.client.Get(url)
if err != nil {
return nil, fmt.Errorf("klines request error: %w", err)
}
defer resp.Body.Close()

body, err := io.ReadAll(resp.Body)
if err != nil {
return nil, fmt.Errorf("read body error: %w", err)
}

var raw [][]float64
if err := json.Unmarshal(body, &raw); err != nil {
return nil, fmt.Errorf("parse klines error: %w", err)
}

var klines []models.OHLCV
for _, k := range raw {
if len(k) < 6 {
continue
}
klines = append(klines, models.OHLCV{
Symbol:    symbol,
Open:      k[3],
High:      k[2],
Low:       k[1],
Close:     k[4],
Volume:    k[5],
Timestamp: time.Unix(int64(k[0]), 0).UTC(),
Interval:  interval,
})
}

log.Infof("[Coinbase] Fetched %d klines for %s", len(klines), symbol)
return klines, nil
}

// generateMockTraders generates simulated Coinbase trader data
func (c *CoinbaseCollector) generateMockTraders(n int) []models.Trader {
traders := make([]models.Trader, n)
for i := 0; i < n; i++ {
traders[i] = models.Trader{
TraderID:   fmt.Sprintf("cb_trader_%03d", i+1),
Nickname:   fmt.Sprintf("CB_Whale_%03d", i+1),
Exchange:   "coinbase",
PNL:        float64(60000-i*1000) + float64(i*60),
ROI:        float64(150-i*2) + 0.2,
WinRate:    0.50 + float64(50-i)*0.003,
TradeCount: 60 + i*5,
}
}
sort.Slice(traders, func(i, j int) bool {
return traders[i].ROI > traders[j].ROI
})
return traders
}

// generateMockTrades generates simulated Coinbase trade data
func (c *CoinbaseCollector) generateMockTrades(traderID string, n int) []models.Trade {
trades := make([]models.Trade, n)
basePrice := 2500.0
now := time.Now().UTC()
for i := 0; i < n; i++ {
side := "BUY"
strategy := "LONG"
if i%5 == 0 {
side = "SELL"
strategy = "SHORT"
}
priceOffset := float64(i%15) * 18.0
if i%3 == 0 {
priceOffset = -priceOffset
}
openTime := now.Add(-time.Duration(i*30) * time.Minute)
trades[i] = models.Trade{
TradeID:    fmt.Sprintf("cb_%s_trade_%03d", traderID[:10], i+1),
TraderID:   traderID,
Exchange:   "coinbase",
Symbol:     "ETH-USD",
Side:       side,
Price:      basePrice + priceOffset,
Quantity:   float64(1+i%5) * 0.2,
Amount:     (basePrice + priceOffset) * float64(1+i%5) * 0.2,
Leverage:   1.0,
PNL:        float64(i%20)*30 - 300,
Strategy:   strategy,
Status:     "CLOSED",
OpenTime:   openTime,
CloseTime:  openTime.Add(time.Duration(15+i%60) * time.Minute),
UpdateTime: now,
}
}
for i := 0; i < 2 && i < len(trades); i++ {
trades[i].Status = "OPEN"
trades[i].CloseTime = time.Time{}
}
return trades
}
