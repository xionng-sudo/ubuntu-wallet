package collector

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"sort"
	"strconv"
	"strings"
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

func (o *OKXCollector) newGET(url string) (*http.Request, error) {
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "ubuntu-wallet-go-collector/1.0")
	return req, nil
}

func isMockOKXUniqueCode(s string) bool {
	return strings.HasPrefix(s, "okx_trader_")
}

func pickFirstNonEmpty(vals ...string) string {
	for _, v := range vals {
		v = strings.TrimSpace(v)
		if v != "" {
			return v
		}
	}
	return ""
}

func (o *OKXCollector) fetchTopTradersOnce(limit int) ([]models.Trader, []byte, error) {
	url := fmt.Sprintf("%s/api/v5/copytrading/public-lead-traders?limit=%d", o.baseURL, limit)

	req, err := o.newGET(url)
	if err != nil {
		return nil, nil, fmt.Errorf("create request error: %w", err)
	}

	resp, err := o.client.Do(req)
	if err != nil {
		return nil, nil, fmt.Errorf("request error: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, body, fmt.Errorf("read body error: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, body, fmt.Errorf("non-200 status=%d body=%s", resp.StatusCode, truncateForLog(body, 1200))
	}

	var result okxResponse
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, body, fmt.Errorf("response parse failed: %w body=%s", err, truncateForLog(body, 1200))
	}
	if result.Code != "" && result.Code != "0" {
		return nil, body, fmt.Errorf("api error code=%s msg=%s body=%s", result.Code, result.Msg, truncateForLog(body, 1200))
	}

	// Real schema (based on your log):
	// data: [ { dataVer: "...", ranks: [ {...}, {...} ] } ]
	type okxLeadTradersDataItem struct {
		DataVer string `json:"dataVer"`
		Ranks   []struct {
			// ID fields (OKX might use any of these; we accept all)
			UniqueCode  string `json:"uniqueCode"`
			PortfolioID string `json:"portfolioId"`
			LeadCode    string `json:"leadCode"`
			UniqueID    string `json:"uniqueId"`
			UID         string `json:"uid"`

			NickName string `json:"nickName"`
			WinRatio string `json:"winRatio"`
			PnlRatio string `json:"pnlRatio"`
			Pnl      string `json:"pnl"`

			CopyTraderNum    string `json:"copyTraderNum"`
			AccCopyTraderNum string `json:"accCopyTraderNum"`
			Aum              string `json:"aum"`
			Ccy              string `json:"ccy"`
			LeadDays         string `json:"leadDays"`
			MaxCopyTraderNum string `json:"maxCopyTraderNum"`
			CopyState        string `json:"copyState"`
		} `json:"ranks"`
	}

	var dataArr []okxLeadTradersDataItem
	if err := json.Unmarshal(result.Data, &dataArr); err != nil {
		return nil, body, fmt.Errorf("lead-traders data parse failed: %w data=%s", err, truncateForLog([]byte(result.Data), 1200))
	}
	if len(dataArr) == 0 || len(dataArr[0].Ranks) == 0 {
		return nil, body, errors.New("lead-traders returned empty ranks")
	}

	var traders []models.Trader
	for _, r := range dataArr[0].Ranks {
		id := pickFirstNonEmpty(r.UniqueCode, r.PortfolioID, r.LeadCode, r.UniqueID, r.UID)
		if id == "" {
			continue
		}

		pnl, _ := strconv.ParseFloat(r.Pnl, 64)
		roi, _ := strconv.ParseFloat(r.PnlRatio, 64)
		winRate, _ := strconv.ParseFloat(r.WinRatio, 64)

		traders = append(traders, models.Trader{
			TraderID: id,
			Nickname: r.NickName,
			Exchange: "okx",
			PNL:      pnl,
			ROI:      roi * 100,
			WinRate:  winRate,
		})
	}

	if len(traders) == 0 {
		return nil, body, errors.New("lead-traders returned 0 usable traders (missing ids)")
	}

	return traders, body, nil
}

// GetTopTraders fetches top traders from OKX copy trading leaderboard
func (o *OKXCollector) GetTopTraders(topN int) ([]models.Trader, error) {
	log.Info("[OKX] Fetching top traders from copy trading...")

	// OKX lead-traders endpoint has strict limit constraints.
	// We auto-downgrade until OKX accepts the parameter.
	tryLimits := []int{20, 10, 5, 1}

	var lastErr error
	var lastBody []byte

	for _, lim := range tryLimits {
		traders, body, err := o.fetchTopTradersOnce(lim)
		if err != nil {
			lastErr = err
			lastBody = body
			log.Warnf("[OKX] lead-traders failed (limit=%d): %v", lim, err)
			continue
		}

		if topN > 0 && len(traders) > topN {
			traders = traders[:topN]
		}

		log.Infof("[OKX] lead-traders success with limit=%d, returned=%d", lim, len(traders))
		return traders, nil
	}

	msg := fmt.Sprintf("[OKX] lead-traders failed for all limits. lastErr=%v lastBody=%s",
		lastErr, truncateForLog(lastBody, 1200))

	if allowMock() {
		log.Warn(msg + " (using simulated data)")
		return o.generateMockTraders(topN), nil
	}
	return nil, errors.New(msg)
}

// GetTraderTrades fetches positions for a specific trader
func (o *OKXCollector) GetTraderTrades(traderID string, limit int) ([]models.Trade, error) {
	// If trader is mock, don't hammer OKX.
	if isMockOKXUniqueCode(traderID) {
		if allowMock() {
			log.Warnf("[OKX] traderID=%s looks like mock uniqueCode; generating simulated trades", traderID)
			return o.generateMockTrades(traderID, limit), nil
		}
		return nil, fmt.Errorf("[OKX] traderID=%s looks like mock uniqueCode; refusing to call OKX API", traderID)
	}

	traderID = strings.TrimSpace(traderID)
	if traderID == "" {
		if allowMock() {
			log.Warn("[OKX] traderID is empty; generating simulated trades")
			return o.generateMockTrades(traderID, limit), nil
		}
		return nil, errors.New("[OKX] traderID is empty")
	}

	log.Infof("[OKX] Fetching trades for trader %s", traderID)

	url := fmt.Sprintf("%s/api/v5/copytrading/public-current-subpositions?uniqueCode=%s&limit=%d",
		o.baseURL, traderID, limit)

	req, err := o.newGET(url)
	if err != nil {
		return nil, fmt.Errorf("create request error: %w", err)
	}

	resp, err := o.client.Do(req)
	if err != nil {
		if allowMock() {
			log.Warnf("[OKX] request error: %v, generating simulated trades", err)
			return o.generateMockTrades(traderID, limit), nil
		}
		return nil, fmt.Errorf("request error: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		if allowMock() {
			log.Warnf("[OKX] read body error: %v, generating simulated trades", err)
			return o.generateMockTrades(traderID, limit), nil
		}
		return nil, fmt.Errorf("read body error: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		msg := fmt.Sprintf("[OKX] subpositions status=%d trader=%s body=%s",
			resp.StatusCode, traderID, truncateForLog(body, 1200))
		if allowMock() {
			log.Warn(msg)
			return o.generateMockTrades(traderID, limit), nil
		}
		return nil, errors.New(msg)
	}

	var result okxResponse
	if err := json.Unmarshal(body, &result); err != nil {
		msg := fmt.Sprintf("[OKX] subpositions response parse failed: %v body=%s", err, truncateForLog(body, 1200))
		if allowMock() {
			log.Warn(msg)
			return o.generateMockTrades(traderID, limit), nil
		}
		return nil, errors.New(msg)
	}

	if result.Code != "" && result.Code != "0" {
		msg := fmt.Sprintf("[OKX] subpositions api error code=%s msg=%s body=%s", result.Code, result.Msg, truncateForLog(body, 1200))
		if allowMock() {
			log.Warn(msg)
			return o.generateMockTrades(traderID, limit), nil
		}
		return nil, errors.New(msg)
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
		msg := fmt.Sprintf("[OKX] subpositions data parse failed: %v data=%s", err, truncateForLog([]byte(result.Data), 1200))
		if allowMock() {
			log.Warn(msg)
			return o.generateMockTrades(traderID, limit), nil
		}
		return nil, errors.New(msg)
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
		if strings.EqualFold(p.PosSide, "short") {
			side = "SELL"
			strategy = "SHORT"
		}

		trades = append(trades, models.Trade{
			TradeID:    fmt.Sprintf("okx_%s_%s", prefix(traderID, 8), p.SubPosID),
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

	// CHANGE: 0 trades is not an error; treat as normal.
	if len(trades) == 0 {
		log.Infof("[OKX] subpositions returned 0 trades (trader=%s) body=%s", traderID, truncateForLog(body, 1200))
		return []models.Trade{}, nil
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

	req, err := o.newGET(url)
	if err != nil {
		return nil, fmt.Errorf("create request error: %w", err)
	}

	resp, err := o.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("klines request error: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read body error: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("klines non-200 status=%d body=%s", resp.StatusCode, truncateForLog(body, 1200))
	}

	var result okxResponse
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, fmt.Errorf("parse response error: %w", err)
	}

	if result.Code != "" && result.Code != "0" {
		return nil, fmt.Errorf("okx klines api error code=%s msg=%s", result.Code, result.Msg)
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
			TradeID:    fmt.Sprintf("okx_%s_trade_%03d", prefix(traderID, 10), i+1),
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
