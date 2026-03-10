package collector

import (
"bytes"
"encoding/json"
"errors"
"fmt"
"io"
"net/http"
"sort"
"strconv"
"strings"
"sync"
"time"

log "github.com/sirupsen/logrus"
"github.com/ubuntu-wallet/go-collector/models"
)

// BinanceCollector collects data from Binance
type BinanceCollector struct {
apiKey    string
apiSecret string
client    *http.Client
baseURL   string

mu                     sync.RWMutex
leaderboardUnavailable bool
}

// NewBinanceCollector creates a new Binance collector
func NewBinanceCollector(apiKey, apiSecret string) *BinanceCollector {
return &BinanceCollector{
apiKey:    apiKey,
apiSecret: apiSecret,
client: &http.Client{
Timeout: 30 * time.Second,
},
baseURL: "https://www.binance.com",
}
}

func (b *BinanceCollector) newJSONPost(url string, payload string) (*http.Request, error) {
req, err := http.NewRequest("POST", url, bytes.NewBufferString(payload))
if err != nil {
return nil, err
}
req.Header.Set("Content-Type", "application/json")
req.Header.Set("User-Agent", "ubuntu-wallet-go-collector/1.0")
return req, nil
}

type binanceLeaderboardResp struct {
Code    string `json:"code"`
Message string `json:"message"`
Data    []struct {
EncryptedUID string  `json:"encryptedUid"`
NickName     string  `json:"nickName"`
Rank         int     `json:"rank"`
PNL          float64 `json:"pnl"`
ROI          float64 `json:"roi"`
} `json:"data"`
Success bool `json:"success"`
}

type binancePositionResp struct {
Code    string `json:"code"`
Message string `json:"message"`
Data    struct {
OtherPositionRetList []struct {
Symbol     string  `json:"symbol"`
EntryPrice float64 `json:"entryPrice"`
MarkPrice  float64 `json:"markPrice"`
PNL        float64 `json:"pnl"`
ROE        float64 `json:"roe"`
Amount     float64 `json:"amount"`
Leverage   int     `json:"leverage"`
UpdateTime int64   `json:"updateTimeStamp"`
TradeType  string  `json:"tradeType"`
} `json:"otherPositionRetList"`
} `json:"data"`
Success bool `json:"success"`
}

func isMockBinanceTraderID(id string) bool {
return strings.HasPrefix(id, "bn_trader_")
}

// GetTopTraders fetches top traders from Binance Futures Leaderboard
func (b *BinanceCollector) GetTopTraders(topN int) ([]models.Trader, error) {
// If we already know it's unavailable, skip network entirely.
b.mu.RLock()
unavailable := b.leaderboardUnavailable
b.mu.RUnlock()
if unavailable {
if allowMock() {
log.Debug("[Binance] leaderboard marked unavailable; using simulated traders")
return b.generateMockTraders(topN), nil
}
return nil, errors.New("[Binance] leaderboard marked unavailable")
}

log.Info("[Binance] Fetching top traders from leaderboard...")

url := fmt.Sprintf("%s/bapi/futures/v3/public/future/leaderboard/getLeaderboardRank", b.baseURL)
payload := `{
"isShared": true,
"isTrader": false,
"periodType": "DAILY",
"statisticsType": "ROI",
"tradeType": "PERPETUAL"
}`

req, err := b.newJSONPost(url, payload)
if err != nil {
return nil, fmt.Errorf("create request error: %w", err)
}

resp, err := b.client.Do(req)
if err != nil {
// Don't mark unavailable on transient network errors.
if allowMock() {
log.Warnf("[Binance] leaderboard request error: %v, using simulated data", err)
return b.generateMockTraders(topN), nil
}
return nil, fmt.Errorf("leaderboard request error: %w", err)
}
defer resp.Body.Close()

body, err := io.ReadAll(resp.Body)
if err != nil {
if allowMock() {
log.Warnf("[Binance] leaderboard read body error: %v, using simulated data", err)
return b.generateMockTraders(topN), nil
}
return nil, fmt.Errorf("leaderboard read body error: %w", err)
}

if resp.StatusCode != http.StatusOK {
msg := fmt.Sprintf("[Binance] leaderboard status=%d body=%s", resp.StatusCode, truncateForLog(body, 1200))

// If it's 404, endpoint is likely unavailable in this region/routing; cache the state.
if resp.StatusCode == http.StatusNotFound {
b.mu.Lock()
b.leaderboardUnavailable = true
b.mu.Unlock()

log.Warn(msg + " (marking leaderboard unavailable)")
} else {
log.Warn(msg)
}

if allowMock() {
return b.generateMockTraders(topN), nil
}
return nil, errors.New(msg)
}

var result binanceLeaderboardResp
if err := json.Unmarshal(body, &result); err != nil {
msg := fmt.Sprintf("[Binance] leaderboard JSON parse failed: %v body=%s", err, truncateForLog(body, 1200))
if allowMock() {
log.Warn(msg)
return b.generateMockTraders(topN), nil
}
return nil, errors.New(msg)
}

var traders []models.Trader
for i, t := range result.Data {
if i >= topN {
break
}
traders = append(traders, models.Trader{
TraderID:   t.EncryptedUID,
Nickname:   t.NickName,
Exchange:   "binance",
PNL:        t.PNL,
ROI:        t.ROI,
TradeCount: 0,
})
}

if len(traders) == 0 {
msg := fmt.Sprintf("[Binance] leaderboard returned 0 traders. code=%s success=%v msg=%s body=%s",
result.Code, result.Success, result.Message, truncateForLog(body, 1200))
if allowMock() {
log.Warn(msg)
return b.generateMockTraders(topN), nil
}
return nil, errors.New(msg)
}

log.Infof("[Binance] Fetched %d top traders", len(traders))
return traders, nil
}

// GetTraderTrades fetches recent trades for a specific trader
func (b *BinanceCollector) GetTraderTrades(traderID string, limit int) ([]models.Trade, error) {
// If traderID is mock, do NOT call Binance API (avoid 404 spam).
if isMockBinanceTraderID(traderID) {
if allowMock() {
return b.generateMockTrades(traderID, limit), nil
}
return nil, fmt.Errorf("[Binance] traderID=%s is mock; refusing to call Binance API", traderID)
}

log.Infof("[Binance] Fetching trades for trader %s", traderID)

url := fmt.Sprintf("%s/bapi/futures/v1/public/future/leaderboard/getOtherPosition", b.baseURL)
payload := fmt.Sprintf(`{
"encryptedUid": "%s",
"tradeType": "PERPETUAL"
}`, traderID)

req, err := b.newJSONPost(url, payload)
if err != nil {
return nil, fmt.Errorf("create request error: %w", err)
}

resp, err := b.client.Do(req)
if err != nil {
if allowMock() {
log.Warnf("[Binance] position request error: %v, using simulated trades", err)
return b.generateMockTrades(traderID, limit), nil
}
return nil, fmt.Errorf("position request error: %w", err)
}
defer resp.Body.Close()

body, err := io.ReadAll(resp.Body)
if err != nil {
if allowMock() {
log.Warnf("[Binance] position read body error: %v, using simulated trades", err)
return b.generateMockTrades(traderID, limit), nil
}
return nil, fmt.Errorf("position read body error: %w", err)
}

if resp.StatusCode != http.StatusOK {
msg := fmt.Sprintf("[Binance] position status=%d trader=%s body=%s", resp.StatusCode, traderID, truncateForLog(body, 1200))
if allowMock() {
log.Warn(msg)
return b.generateMockTrades(traderID, limit), nil
}
return nil, errors.New(msg)
}

var result binancePositionResp
if err := json.Unmarshal(body, &result); err != nil {
msg := fmt.Sprintf("[Binance] position JSON parse failed: %v body=%s", err, truncateForLog(body, 1200))
if allowMock() {
log.Warn(msg)
return b.generateMockTrades(traderID, limit), nil
}
return nil, errors.New(msg)
}

var trades []models.Trade
for _, p := range result.Data.OtherPositionRetList {
side := "BUY"
strategy := "LONG"
if strings.EqualFold(p.TradeType, "SHORT") {
side = "SELL"
strategy = "SHORT"
}
trades = append(trades, models.Trade{
TradeID:    fmt.Sprintf("bn_%s_%d", prefix(traderID, 8), p.UpdateTime),
TraderID:   traderID,
Exchange:   "binance",
Symbol:     p.Symbol,
Side:       side,
Price:      p.EntryPrice,
Quantity:   p.Amount,
Amount:     p.EntryPrice * p.Amount,
Leverage:   float64(p.Leverage),
PNL:        p.PNL,
Strategy:   strategy,
Status:     "OPEN",
OpenTime:   time.UnixMilli(p.UpdateTime),
UpdateTime: time.Now().UTC(),
})
}

if len(trades) == 0 {
msg := fmt.Sprintf("[Binance] position returned 0 trades. code=%s success=%v msg=%s body=%s",
result.Code, result.Success, result.Message, truncateForLog(body, 1200))
if allowMock() {
log.Warn(msg)
return b.generateMockTrades(traderID, limit), nil
}
return nil, errors.New(msg)
}

log.Infof("[Binance] Fetched %d trades for trader %s", len(trades), traderID)
return trades, nil
}

// GetKlines fetches candlestick data
func (b *BinanceCollector) GetKlines(symbol, interval string, limit int) ([]models.OHLCV, error) {
url := fmt.Sprintf("https://api.binance.com/api/v3/klines?symbol=%s&interval=%s&limit=%d",
symbol, interval, limit)

resp, err := b.client.Get(url)
if err != nil {
return nil, fmt.Errorf("klines request error: %w", err)
}
defer resp.Body.Close()

body, err := io.ReadAll(resp.Body)
if err != nil {
return nil, fmt.Errorf("read body error: %w", err)
}

var raw [][]interface{}
if err := json.Unmarshal(body, &raw); err != nil {
return nil, fmt.Errorf("parse klines error: %w", err)
}

var klines []models.OHLCV
for _, k := range raw {
if len(k) < 6 {
continue
}
open, _ := strconv.ParseFloat(k[1].(string), 64)
high, _ := strconv.ParseFloat(k[2].(string), 64)
low, _ := strconv.ParseFloat(k[3].(string), 64)
closeP, _ := strconv.ParseFloat(k[4].(string), 64)
vol, _ := strconv.ParseFloat(k[5].(string), 64)
ts := int64(k[0].(float64))

klines = append(klines, models.OHLCV{
Symbol:    symbol,
Open:      open,
High:      high,
Low:       low,
Close:     closeP,
Volume:    vol,
Timestamp: time.UnixMilli(ts).UTC(),
Interval:  interval,
})
}

log.Infof("[Binance] Fetched %d klines for %s", len(klines), symbol)
return klines, nil
}

// GetCurrentPrice fetches current price
func (b *BinanceCollector) GetCurrentPrice(symbol string) (*models.MarketData, error) {
url := fmt.Sprintf("https://api.binance.com/api/v3/ticker/24hr?symbol=%s", symbol)

resp, err := b.client.Get(url)
if err != nil {
return nil, fmt.Errorf("price request error: %w", err)
}
defer resp.Body.Close()

body, err := io.ReadAll(resp.Body)
if err != nil {
return nil, fmt.Errorf("read body error: %w", err)
}

var ticker struct {
Symbol    string `json:"symbol"`
LastPrice string `json:"lastPrice"`
Volume    string `json:"volume"`
HighPrice string `json:"highPrice"`
LowPrice  string `json:"lowPrice"`
PriceChg  string `json:"priceChangePercent"`
}

if err := json.Unmarshal(body, &ticker); err != nil {
return nil, fmt.Errorf("parse ticker error: %w", err)
}

price, _ := strconv.ParseFloat(ticker.LastPrice, 64)
volume, _ := strconv.ParseFloat(ticker.Volume, 64)
high, _ := strconv.ParseFloat(ticker.HighPrice, 64)
low, _ := strconv.ParseFloat(ticker.LowPrice, 64)
change, _ := strconv.ParseFloat(ticker.PriceChg, 64)

return &models.MarketData{
Symbol:    symbol,
Price:     price,
Volume24h: volume,
High24h:   high,
Low24h:    low,
Change24h: change,
Timestamp: time.Now().UTC(),
}, nil
}

func (b *BinanceCollector) generateMockTraders(n int) []models.Trader {
traders := make([]models.Trader, n)
for i := 0; i < n; i++ {
traders[i] = models.Trader{
TraderID:   fmt.Sprintf("bn_trader_%03d", i+1),
Nickname:   fmt.Sprintf("BN_TopTrader_%03d", i+1),
Exchange:   "binance",
PNL:        float64(100000-i*1500) + float64(i*100),
ROI:        float64(200-i*3) + 0.5,
WinRate:    0.55 + float64(50-i)*0.005,
TradeCount: 100 + i*10,
}
}
sort.Slice(traders, func(i, j int) bool { return traders[i].ROI > traders[j].ROI })
return traders
}

func (b *BinanceCollector) generateMockTrades(traderID string, n int) []models.Trade {
trades := make([]models.Trade, n)
basePrice := 2500.0
now := time.Now().UTC()

for i := 0; i < n; i++ {
side := "BUY"
strategy := "LONG"
if i%3 == 0 {
side = "SELL"
strategy = "SHORT"
}

priceOffset := float64(i%20) * 15.0
if i%2 == 0 {
priceOffset = -priceOffset
}

openTime := now.Add(-time.Duration(i) * time.Hour)
openMs := openTime.UnixMilli()

trades[i] = models.Trade{
TradeID:    fmt.Sprintf("bn_%s_%d_%03d", traderID, openMs, i+1),
TraderID:   traderID,
Exchange:   "binance",
Symbol:     "ETHUSDT",
Side:       side,
Price:      basePrice + priceOffset,
Quantity:   float64(1+i%10) * 0.5,
Amount:     (basePrice + priceOffset) * float64(1+i%10) * 0.5,
Leverage:   float64(5 + i%15),
PNL:        float64(i%30)*50 - 500,
Strategy:   strategy,
Status:     "CLOSED",
OpenTime:   openTime,
CloseTime:  openTime.Add(time.Duration(30+i%120) * time.Minute),
UpdateTime: now,
}
}

for i := 0; i < 3 && i < len(trades); i++ {
trades[i].Status = "OPEN"
trades[i].CloseTime = time.Time{}
}
return trades
}
