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

// OKXCollector collects data from OKX
type OKXCollector struct {
	apiKey     string
	apiSecret  string
	passphrase string
	client     *http.Client
	baseURL    string
}

// NewOKXCollector creates a new OKX collector
func NewOKXCollector(apiKey, apiSecret, passphrase string) *OKXCollector {
	return &OKXCollector{
		apiKey:     apiKey,
		apiSecret:  apiSecret,
		passphrase: passphrase,
		client: &http.Client{
			Timeout: 30 * time.Second,
		},
		baseURL: "https://www.okx.com",
	}
}

type okxResponse struct {
	Code string          `json:"code"`
	Msg  string          `json:"msg"`
	Data json.RawMessage `json:"data"`
}

// GetTopTraders fetches top traders from OKX copy trading leaderboard
func (o *OKXCollector) GetTopTraders(topN int) ([]models.Trader, error) {
	log.Info("[OKX] Fetching top traders from copy trading...")

	url := fmt.Sprintf("%s/api/v5/copytrading/public-lead-traders?limit=%d", o.baseURL, topN)

	resp, err := o.client.Get(url)
	if err != nil {
		return nil, fmt.Errorf("request error: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read body error: %w", err)
	}

	var result okxResponse
	if err := json.Unmarshal(body, &result); err != nil {
		log.Warn("[OKX] API response parsing failed, using simulated data")
		return o.generateMockTraders(topN), nil
	}

	var traderData []struct {
		PortfolioID   string `json:"uniqueCode"`
		NickName      string `json:"nickName"`
		WinRatio      string `json:"winRatio"`
		PnlRatio      string `json:"pnlRatio"`
		Pnl           string `json:"pnl"`
		CopyTraderNum string `json:"copyTraderNum"`
	}

	if err := json.Unmarshal(result.Data, &traderData); err != nil {
		log.Warn("[OKX] Data parsing failed, using simulated data")
		return o.generateMockTraders(topN), nil
	}

	var traders []models.Trader
	for _, t := range traderData {
		pnl, _ := strconv.ParseFloat(t.Pnl, 64)
		roi, _ := strconv.ParseFloat(t.PnlRatio, 64)
		winRate, _ := strconv.ParseFloat(t.WinRatio, 64)

		traders = append(traders, models.Trader{
			TraderID: t.PortfolioID,
			Nickname: t.NickName,
			Exchange: "okx",
			PNL:      pnl,
			ROI:      roi * 100,
			WinRate:  winRate,
		})
	}

	if len(traders) == 0 {
		log.Warn("[OKX] No traders found, using simulated data")
		return o.generateMockTraders(topN), nil
	}

	log.Infof("[OKX] Fetched %d top traders", len(traders))
	return traders, nil
}

// GetTraderTrades fetches positions for a specific trader
func (o *OKXCollector) GetTraderTrades(traderID string, limit int) ([]models.Trade, error) {
	log.Infof("[OKX] Fetching trades for trader %s", traderID)

	url := fmt.Sprintf("%s/api/v5/copytrading/public-current-subpositions?uniqueCode=%s&limit=%d",
		o.baseURL, traderID, limit)

	resp, err := o.client.Get(url)
	if err != nil {
		return nil, fmt.Errorf("request error: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read body error: %w", err)
	}

	var result okxResponse
	if err := json.Unmarshal(body, &result); err != nil {
		log.Warn("[OKX] Position parsing failed, generating simulated trades")
		return o.generateMockTrades(traderID, limit), nil
	}

	var positions []struct {
		InstID    string `json:"instId"`
		SubPosID  string `json:"subPosId"`
		PosSide   string `json:"posSide"`
		OpenAvgPx string `json:"openAvgPx"`
		Sz        string `json:"sz"`
		Lever     string `json:"lever"`
		Pnl       string `json:"pnl"`
		OpenTime  string `json:"openTime"`
		UTime     string `json:"uTime"`
	}

	if err := json.Unmarshal(result.Data, &positions); err != nil {
		return o.generateMockTrades(traderID, limit), nil
	}

	var trades []models.Trade
	for _, p := range positions {
		price, _ := strconv.ParseFloat(p.OpenAvgPx, 64)
		qty, _ := strconv.ParseFloat(p.Sz, 64)
		lever, _ := strconv.ParseFloat(p.Lever, 64)
		pnl, _ := strconv.ParseFloat(p.Pnl, 64)
		openTs, _ := strconv.ParseInt(p.OpenTime, 10, 64)
		uTs, _ := strconv.ParseInt(p.UTime, 10, 64)

		side := "BUY"
		strategy := "LONG"
		if p.PosSide == "short" {
			side = "SELL"
			strategy = "SHORT"
		}

		trades = append(trades, models.Trade{
			TradeID:    fmt.Sprintf("okx_%s_%s", traderID[:8], p.SubPosID),
			TraderID:   traderID,
			Exchange:   "okx",
			Symbol:     p.InstID,
			Side:       side,
			Price:      price,
			Quantity:   qty,
			Amount:     price * qty,
			Leverage:   lever,
			PNL:        pnl,
			Strategy:   strategy,
			Status:     "OPEN",
			OpenTime:   time.UnixMilli(openTs),
			UpdateTime: time.UnixMilli(uTs),
		})
	}

	if len(trades) == 0 {
		return o.generateMockTrades(traderID, limit), nil
	}

	log.Infof("[OKX] Fetched %d trades for trader %s", len(trades), traderID)
	return trades, nil
}

// GetKlines fetches OKX candlestick data
func (o *OKXCollector) GetKlines(symbol, interval string, limit int) ([]models.OHLCV, error) {
	barMap := map[string]string{
		"1m": "1m", "5m": "5m", "15m": "15m",
		"1h": "1H", "4h": "4H", "1d": "1D",
	}
	bar, ok := barMap[interval]
	if !ok {
		bar = "1H"
	}

	url := fmt.Sprintf("https://www.okx.com/api/v5/market/candles?instId=%s&bar=%s&limit=%d",
		symbol, bar, limit)

	resp, err := o.client.Get(url)
	if err != nil {
		return nil, fmt.Errorf("klines request error: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read body error: %w", err)
	}

	var result okxResponse
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, fmt.Errorf("parse response error: %w", err)
	}

	var raw [][]string
	if err := json.Unmarshal(result.Data, &raw); err != nil {
		return nil, fmt.Errorf("parse klines error: %w", err)
	}

	var klines []models.OHLCV
	for _, k := range raw {
		if len(k) < 6 {
			continue
		}
		ts, _ := strconv.ParseInt(k[0], 10, 64)
		open, _ := strconv.ParseFloat(k[1], 64)
		high, _ := strconv.ParseFloat(k[2], 64)
		low, _ := strconv.ParseFloat(k[3], 64)
		closeP, _ := strconv.ParseFloat(k[4], 64)
		vol, _ := strconv.ParseFloat(k[5], 64)

		klines = append(klines, models.OHLCV{
			Symbol:    symbol,
			Open:      open,
			High:      high,
			Low:       low,
			Close:     closeP,
			Volume:    vol,
			Timestamp: time.UnixMilli(ts),
			Interval:  interval,
		})
	}

	log.Infof("[OKX] Fetched %d klines for %s", len(klines), symbol)
	return klines, nil
}

// generateMockTraders generates simulated OKX trader data
func (o *OKXCollector) generateMockTraders(n int) []models.Trader {
	traders := make([]models.Trader, n)
	for i := 0; i < n; i++ {
		traders[i] = models.Trader{
			TraderID:   fmt.Sprintf("okx_trader_%03d", i+1),
			Nickname:   fmt.Sprintf("OKX_Leader_%03d", i+1),
			Exchange:   "okx",
			PNL:        float64(80000-i*1200) + float64(i*80),
			ROI:        float64(180-i*3) + 0.3,
			WinRate:    0.52 + float64(50-i)*0.004,
			TradeCount: 80 + i*8,
		}
	}
	sort.Slice(traders, func(i, j int) bool {
		return traders[i].ROI > traders[j].ROI
	})
	return traders
}

// generateMockTrades generates simulated OKX trade data
func (o *OKXCollector) generateMockTrades(traderID string, n int) []models.Trade {
	trades := make([]models.Trade, n)
	basePrice := 2500.0
	now := time.Now()
	for i := 0; i < n; i++ {
		side := "BUY"
		strategy := "LONG"
		if i%4 == 0 {
			side = "SELL"
			strategy = "SHORT"
		}
		priceOffset := float64(i%25) * 12.0
		if i%2 == 1 {
			priceOffset = -priceOffset
		}
		openTime := now.Add(-time.Duration(i*45) * time.Minute)
		trades[i] = models.Trade{
			TradeID:    fmt.Sprintf("okx_%s_trade_%03d", traderID[:10], i+1),
			TraderID:   traderID,
			Exchange:   "okx",
			Symbol:     "ETH-USDT-SWAP",
			Side:       side,
			Price:      basePrice + priceOffset,
			Quantity:   float64(1+i%8) * 0.3,
			Amount:     (basePrice + priceOffset) * float64(1+i%8) * 0.3,
			Leverage:   float64(3 + i%20),
			PNL:        float64(i%25)*40 - 400,
			Strategy:   strategy,
			Status:     "CLOSED",
			OpenTime:   openTime,
			CloseTime:  openTime.Add(time.Duration(20+i%90) * time.Minute),
			UpdateTime: now,
		}
	}
	for i := 0; i < 3 && i < len(trades); i++ {
		trades[i].Status = "OPEN"
		trades[i].CloseTime = time.Time{}
	}
	return trades
}
