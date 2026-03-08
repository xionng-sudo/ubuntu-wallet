package models

import "time"

// Trader represents a top trader from an exchange
type Trader struct {
	TraderID   string  `json:"trader_id"`
	Nickname   string  `json:"nickname"`
	Exchange   string  `json:"exchange"`
	PNL        float64 `json:"pnl"`
	ROI        float64 `json:"roi"`
	WinRate    float64 `json:"win_rate"`
	TradeCount int     `json:"trade_count"`
}

// Trade represents a single trade record
type Trade struct {
	TradeID    string    `json:"trade_id"`
	TraderID   string    `json:"trader_id"`
	Exchange   string    `json:"exchange"`
	Symbol     string    `json:"symbol"`
	Side       string    `json:"side"`
	Price      float64   `json:"price"`
	Quantity   float64   `json:"quantity"`
	Amount     float64   `json:"amount"`
	Leverage   float64   `json:"leverage"`
	PNL        float64   `json:"pnl"`
	Strategy   string    `json:"strategy"`
	Status     string    `json:"status"`
	OpenTime   time.Time `json:"open_time"`
	CloseTime  time.Time `json:"close_time"`
	UpdateTime time.Time `json:"update_time"`
}

// PriceLevel represents accumulation of trades at a price range
type PriceLevel struct {
	PriceMin   float64   `json:"price_min"`
	PriceMax   float64   `json:"price_max"`
	Buyers     []string  `json:"buyers"`
	Sellers    []string  `json:"sellers"`
	BuyVolume  float64   `json:"buy_volume"`
	SellVolume float64   `json:"sell_volume"`
	Timestamp  time.Time `json:"timestamp"`
}

// MarketData represents current market information
type MarketData struct {
	Symbol    string    `json:"symbol"`
	Price     float64   `json:"price"`
	Volume24h float64   `json:"volume_24h"`
	High24h   float64   `json:"high_24h"`
	Low24h    float64   `json:"low_24h"`
	Change24h float64   `json:"change_24h"`
	Timestamp time.Time `json:"timestamp"`
}

// OHLCV represents a single candlestick
type OHLCV struct {
	Symbol    string    `json:"symbol"`
	Open      float64   `json:"open"`
	High      float64   `json:"high"`
	Low       float64   `json:"low"`
	Close     float64   `json:"close"`
	Volume    float64   `json:"volume"`
	Timestamp time.Time `json:"timestamp"`
	Interval  string    `json:"interval"`
}

// CollectorConfig holds configuration for data collection
type CollectorConfig struct {
	APIKey    string `json:"api_key"`
	APISecret string `json:"api_secret"`
	TopN      int    `json:"top_n"`
	Limit     int    `json:"limit"`
}

