# ubuntu-wallet: Architecture & Operations Guide

> **Audience**: Engineers and operators who need to understand, deploy, retrain, and maintain the ubuntu-wallet ML trading system for ETH perpetual futures.
>
> **中文用户**：请参阅根目录 [README.md](../README.md)（中文详细版）和 [ARCHITECTURE_CN.md](ARCHITECTURE_CN.md)（中文完整架构与部署手册），内容更为详细。

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture Diagram](#2-architecture-diagram)
3. [Component Responsibilities](#3-component-responsibilities)
4. [Directory Structure](#4-directory-structure)
5. [Data Flow](#5-data-flow)
6. [Training Pipeline](#6-training-pipeline)
7. [Walk-Forward Cross-Validation](#7-walk-forward-cross-validation)
8. [Inference Flow](#8-inference-flow)
9. [Prediction Logging Format](#9-prediction-logging-format)
10. [Evaluation Flow](#10-evaluation-flow)
11. [Deployment (Systemd)](#11-deployment-systemd)
12. [Configuration Reference](#12-configuration-reference)
13. [Multi-Timeframe Design](#13-multi-timeframe-design)
14. [Maintenance & Operations](#14-maintenance--operations)
15. [Common Failure Modes & Debugging](#15-common-failure-modes--debugging)
16. [What to Watch Out For](#16-what-to-watch-out-for)

---

## 1. System Overview

ubuntu-wallet is a production ML trading system for ETH perpetual futures. It combines real-time market data collection, a stacked ensemble ML model, a live inference API, and a prediction log that feeds evaluation and live trading decisions.

The system targets **1-hour candle resolution** with 4h and 1d trend context. The core signal model (`event_v3`) classifies each bar close into one of three outcomes:

| Label | Value | Meaning |
|-------|-------|---------|
| SHORT | 0 | Bearish — enter short or stay out of longs |
| FLAT  | 1 | Neutral — no strong directional view |
| LONG  | 2 | Bullish — enter long or stay out of shorts |

The system is designed for **simulated / research operation**. Live order execution requires wiring `eth_perp_engine_binance.py` out of dry-run mode.

---

## 2. Architecture Diagram

```
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │                         ubuntu-wallet system                                │
 │                                                                             │
 │  ┌──────────────────────────────────────────────────────────────────────┐  │
 │  │                      go-collector (Go, port 8080)                    │  │
 │  │                                                                      │  │
 │  │   Binance ──┐                                                        │  │
 │  │   OKX    ───┼── OHLCV poll ──► data/<SYMBOL>/klines_1h/4h/1d.json  │  │
 │  │   Coinbase ─┘   (market/)       traders.json                        │  │
 │  │              symbols: BTCUSDT ETHUSDT SOLUSDT BNBUSDT (+Phase2)     │  │
 │  │                                 signals.json                        │  │
 │  │   HTTP API:  /healthz  /signals  /features  /traders                │  │
 │  └──────────────────────────┬───────────────────────────────────────────┘  │
 │                             │ reads klines files                           │
 │                             ▼                                              │
 │  ┌──────────────────────────────────────────────────────────────────────┐  │
 │  │                    ml-service (Python, port 9000)                    │  │
 │  │                                                                      │  │
 │  │   POST /predict ──► feature_builder.py ──► model_loader.py          │  │
 │  │                             │                     │                  │  │
 │  │                    multi-TF features       LightGBM base             │  │
 │  │                    (1h + 4h prefix         XGBoost base              │  │
 │  │                     + 1d prefix)           LR stacking               │  │
 │  │                                            calibration               │  │
 │  │   GET /healthz                                  │                    │  │
 │  │                                                 ▼                    │  │
 │  │                                    prediction_logger.py              │  │
 │  │                                    data/predictions_log.jsonl        │  │
 │  └──────────────────────────────────────────────────────────────────────┘  │
 │                                                                             │
 │  ┌──────────────────────────────────────────────────────────────────────┐  │
 │  │                  python-analyzer (training pipeline)                 │  │
 │  │                                                                      │  │
 │  │   labeling.py ──────────────────────────────────────────────────┐   │  │
 │  │     ternary labels (forward return vs threshold)                │   │  │
 │  │     triple-barrier labels (TP/SL/timeout)                       │   │  │
 │  │                                                                  │   │  │
 │  │   walkforward_cv.py ──────────────────────────────────────────┐ │   │  │
 │  │     time-series CV, gap-aware splits                          │ │   │  │
 │  │                                                               ▼ ▼   │  │
 │  │   train_event_stack_v3.py ──► LightGBM + XGBoost → LR stack      │  │
 │  │                                                  → calibration    │  │
 │  │                                                  → models/        │  │
 │  └──────────────────────────────────────────────────────────────────────┘  │
 │                                                                             │
 │  ┌──────────────────────────────────────────────────────────────────────┐  │
 │  │                  scripts/ (backtest, evaluation, trading)            │  │
 │  │                                                                      │  │
 │  │   backtest_event_v3_http.py  (grid search → best params)            │  │
 │  │   evaluate_from_logs.py      (evaluate logged predictions)           │  │
 │  │   live_trader_eth_perp_binance.py  (dry-run live loop)              │  │
 │  └──────────────────────────────────────────────────────────────────────┘  │
 └─────────────────────────────────────────────────────────────────────────────┘

 STORAGE (data/)
 ├── klines_1h.json      ← 1-hour OHLCV history
 ├── klines_4h.json      ← 4-hour OHLCV history
 ├── klines_1d.json      ← daily OHLCV history
 ├── traders.json        ← top trader leaderboard snapshots
 └── predictions_log.jsonl ← append-only prediction audit trail

 MODELS (models/)
 ├── lightgbm_event_v3.pkl / _scaler.pkl
 ├── xgboost_event_v3.json / _scaler.pkl
 ├── stacking_event_v3.pkl
 ├── calibration_event_v3.pkl / _meta.json
 ├── feature_columns_event_v3.json   ← canonical feature list
 └── model_meta.json
```

---

## 3. Component Responsibilities

### 3.1 go-collector

- Polls Binance exchange API on a configurable interval (default every 60 seconds).
- Downloads OHLCV candles for **multiple symbols** (Phase 1: BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT; Phase 2 optional: XRPUSDT, DOGEUSDT, ADAUSDT) and writes per-symbol kline files:
  - `data/<SYMBOL>/klines_1h.json`, `klines_4h.json`, `klines_1d.json`, `klines_15m.json`
  - The **primary symbol** (default: ETHUSDT; set via `PRIMARY_SYMBOL` env) additionally writes `klines_1m.json`, `klines_5m.json`
- Configure symbols via `SYMBOLS=BTCUSDT,ETHUSDT,...` or `ENABLE_PHASE2_SYMBOLS=true` env vars.
- Configure the primary symbol explicitly with `PRIMARY_SYMBOL=ETHUSDT` (defaults to ETHUSDT for backward compatibility; must be present in the enabled symbol list).
- Legacy root-level `data/klines_*.json` mirrors the **primary symbol** data (`LEGACY_ETHUSDT_COMPAT=true`, the default); these files always reflect PRIMARY_SYMBOL (ETHUSDT by default), preserving the semantics that existed before multi-symbol support. Set to `false` after migrating consumers to `data/<SYMBOL>/klines_*.json`.
- Tracks Binance futures leaderboard (top 50 traders), writing `data/traders.json`.
- Computes over 70 technical features per bar in `features/` (SMA, EMA, MACD, RSI, ATR, Bollinger, volatility, returns).
- Calls `ml-service /predict` and publishes the result to `data/signals.json` and via the `/signals` HTTP endpoint.
- Exposes `GET /healthz`, `GET /features`, `GET /traders` for downstream consumers and health checks.
  - `/api/healthz` includes `enabled_symbols` and `primary_symbol` fields.

### 3.2 ml-service

- FastAPI application (`app.py`) running on port 9000.
- Loads trained model artifacts from `models/` at startup.
- `POST /predict` builds multi-timeframe features, runs inference through the stacking ensemble, optionally calibrates probabilities, applies 3-class decision thresholds, and returns a JSON signal.
- Every prediction is appended to `data/predictions_log.jsonl` for audit and offline evaluation.
- `GET /healthz` exposes model version, feature count, and calibration state.

### 3.3 python-analyzer

The offline ML pipeline. Not a long-running service; run interactively or from cron.

- **`labeling.py`** — converts raw OHLCV into ternary or triple-barrier labels.
- **`train_event_stack_v3.py`** — end-to-end training: feature engineering, label generation, base model fitting (LightGBM + XGBoost), stacking (LogisticRegression), calibration, and artifact export.
- **`walkforward_cv.py`** — time-series walk-forward evaluation without lookahead bias.
- **`technical_analysis.py`** — shared `TechnicalAnalyzer` class used by both training and inference (ensures consistency).
- **`ml_predictor.py`** — legacy single-model training utilities.
- **`backtest_multi_tf.py`** — OHLCV-based multi-timeframe backtesting engine.

### 3.4 scripts/

Executable workflows for operators.

| Script | Purpose |
|--------|---------|
| `run.sh` | Start/stop all services, individual modes |
| `backtest_event_v3_http.py` | Grid search over (threshold, TP, SL, horizon) via live ml-service |
| `evaluate_from_logs.py` | Replay logged predictions against actual price data |
| `live_trader_eth_perp_binance.py` | Dry-run live trading loop (calls ml-service) |
| `eth_perp_engine_binance.py` | Strategy engine with risk controls (single position, circuit breaker) |
| `install.sh` | One-click Ubuntu 22.04 install (Python, Go, TA-Lib, venv, build) |

### 3.5 systemd/

Production service management on Ubuntu 22.04+.

| Unit | Type | Description |
|------|------|-------------|
| `go-collector.service` | Service | Runs go-collector binary, auto-restarts |
| `ml-service.service` | Service | Runs uvicorn/FastAPI, auto-restarts |
| `evaluate-predictions.service` | oneshot | Triggered to run `evaluate_from_logs.py` |
| `check-go-collector.service` | oneshot | Health check, sends Telegram alert on failure |
| `evaluate-predictions.timer` | Timer | Runs evaluate-predictions.service at 00:06, 06:06, 12:06, 18:06 local time |
| `check-go-collector.timer` | Timer | Triggers health check every 60s |

---

## 4. Directory Structure

```
ubuntu-wallet/
├── go-collector/
│   ├── main.go                      # HTTP server, orchestration
│   ├── models/models.go             # Shared data structures
│   ├── collector/
│   │   ├── binance.go               # Binance Futures API client
│   │   ├── okx.go                   # OKX Derivatives API client
│   │   └── coinbase.go              # Coinbase API client
│   ├── features/
│   │   └── features.go              # Technical indicator computation
│   ├── signal/
│   │   └── signal.go                # Rule-based + ml-service client
│   ├── market/
│   │   └── market.go                # OHLCV persistence (klines_*.json)
│   └── go.mod / go.sum
│
├── python-analyzer/
│   ├── train_event_stack_v3.py      # Main training script (event_v3)
│   ├── labeling.py                  # Label generation (ternary / triple-barrier)
│   ├── walkforward_cv.py            # Walk-forward CV
│   ├── technical_analysis.py        # Shared TA (TechnicalAnalyzer class)
│   ├── ml_predictor.py              # Legacy single-model training
│   ├── backtest_multi_tf.py         # OHLCV-based backtest engine
│   ├── data_collector.py            # Exchange APIs (CCXT) + Go Collector client
│   ├── config.py                    # Hyperparameters and environment config
│   ├── main.py                      # Orchestrator (data → analysis → train → dashboard)
│   ├── alerts.py                    # Alert routing + Telegram
│   ├── visualization.py             # Dash dashboard
│   └── requirements.txt
│
├── ml-service/
│   ├── app.py                       # FastAPI application
│   ├── feature_builder.py           # Multi-timeframe feature construction
│   ├── model_loader.py              # Model loading and predict_proba routing
│   ├── calibration.py               # Isotonic/sigmoid calibration
│   ├── prediction_logger.py         # JSONL prediction log
│   └── requirements.txt
│
├── scripts/
│   ├── run.sh                       # Service orchestration (full/collector/train/...)
│   ├── install.sh                   # Ubuntu 22.04 one-click install
│   ├── backtest_event_v3_http.py    # Grid search backtest
│   ├── evaluate_from_logs.py        # Evaluate logged predictions
│   ├── live_trader_eth_perp_binance.py  # Dry-run live trader
│   ├── eth_perp_engine_binance.py   # Strategy engine (risk controls)
│   └── ops/
│       └── check-go-collector.sh    # Health check + Telegram alert
│
├── systemd/
│   ├── go-collector.service
│   ├── ml-service.service
│   ├── evaluate-predictions.service
│   ├── check-go-collector.service
│   ├── check-go-collector.timer
│   ├── collector.env.example
│   ├── telegram.env.example
│   └── OPS-NOTES.md
│
├── data/                            # Runtime data (git-ignored)
│   ├── klines_1h.json
│   ├── klines_4h.json
│   ├── klines_1d.json
│   ├── traders.json
│   └── predictions_log.jsonl
│
├── models/                          # Trained artifacts (git-ignored)
│   ├── lightgbm_event_v3.pkl
│   ├── lightgbm_event_v3_scaler.pkl
│   ├── xgboost_event_v3.json
│   ├── xgboost_event_v3_scaler.pkl
│   ├── stacking_event_v3.pkl
│   ├── calibration_event_v3.pkl
│   ├── calibration_event_v3_meta.json
│   ├── feature_columns_event_v3.json
│   └── model_meta.json
│
└── docs/
    ├── ARCHITECTURE.md              # This file
    └── ETH_perp_risk_rules.md
```

---

## 5. Data Flow

### 5.1 Collection Loop (every 60s)

```
go-collector wakes up
   └─► Resolve symbols from SYMBOLS env (default: BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT)
   └─► Resolve primary symbol from PRIMARY_SYMBOL env (default: ETHUSDT)
   └─► For each symbol:
         └─► Binance REST: 15m/1h/4h/1d OHLCV candles
         └─► Write data/<SYMBOL>/klines_{15m,1h,4h,1d}.json
   └─► Primary symbol only: also fetch 1m/5m candles
   └─► Legacy compat: also write data/klines_{1h,4h,1d}.json mirroring PRIMARY_SYMBOL (LEGACY_ETHUSDT_COMPAT=true)
   └─► Binance REST: top-50 leaderboard traders
   └─► OKX REST: funding rates / derivatives data
   └─► Write data/traders.json
   └─► Compute 70+ technical features for latest bar (using primary symbol klines)
   └─► POST http://127.0.0.1:9000/predict
         └─► Receive {signal, confidence, ...}
   └─► Write data/signals.json
```

### 5.2 Prediction Request Flow

```
Client (go-collector or operator) sends:
   POST /predict  {"symbol": "ETHUSDT", "interval": "1h"}

ml-service:
1. feature_builder.build_event_v3_feature_row(data_dir, model_dir, as_of_ts)
      a. Load klines_1h.json → df_1h (with all technical indicators)
      b. Load klines_4h.json → df_4h (with tf4h_ prefix features)
      c. Load klines_1d.json → df_1d (with tf1d_ prefix features)
      d. merge_asof(df_1h, df_4h) by timestamp
      e. merge_asof(df_1h_4h, df_1d) by timestamp
      f. Load feature_columns_event_v3.json → canonical column list
      g. Select/zero-fill columns → X (1, n_features)

2. model_loader.predict_proba(loaded_model, X)
      a. Scale X with LightGBM scaler → predict_proba(X) → p_lgb [1,3]
      b. Scale X with XGBoost scaler → predict_proba(X) → p_xgb [1,3]
      c. Concatenate [p_lgb, p_xgb] → (1, 6) meta-features
      d. stacking_model.predict_proba(meta) → p_stack [1,3]

3. calibration.calibrate_proba(p_stack) → p_cal [1,3]
      - Per-class isotonic regression (one-vs-rest)
      - Renormalize rows to sum 1

4. Apply decision thresholds (env: EVENT_V3_P_ENTER, EVENT_V3_DELTA):
      if p_cal[LONG] >= 0.65 and (p_cal[LONG] - p_cal[SHORT]) >= 0.0:
          signal = LONG
      elif p_cal[SHORT] >= 0.65 and (p_cal[SHORT] - p_cal[LONG]) >= 0.0:
          signal = SHORT
      else:
          signal = FLAT

5. prediction_logger.log(record) → data/predictions_log.jsonl (append)

6. Return JSON response
```

### 5.3 Training Data Flow

```
data/klines_1h.json  ──┐
data/klines_4h.json  ──┼─► build_multi_tf_feature_df()
data/klines_1d.json  ──┘         │
                                  │  (full history DataFrame)
                                  ▼
                        labeling.make_ternary_labels()
                     or labeling.make_triple_barrier_labels()
                                  │
                                  │  X (n_samples, n_features)
                                  │  y (n_samples,) ∈ {0,1,2}
                                  ▼
                        train/test split (80/20 by time)
                                  │
                    ┌─────────────┴──────────────┐
                    ▼                            ▼
              LightGBM 3-class           XGBoost 3-class
              (5-fold OOF)               (5-fold OOF)
                    │                            │
                    └─────────┬──────────────────┘
                              │  meta-features (6 cols)
                              ▼
                    LogisticRegression stacking
                              │
                              ▼
                    calibration on test holdout
                              │
                              ▼
                    models/ artifact export
```

---

## 6. Training Pipeline

### 6.1 Prerequisites

```bash
# Ensure data exists for the target symbol (go-collector must have run with KLINES_LOOKBACK_MODE=on_startup)
# e.g. for ETHUSDT:
ls data/ETHUSDT/klines_1h.json data/ETHUSDT/klines_4h.json data/ETHUSDT/klines_1d.json

cd ~/ubuntu-wallet
```

### 6.2 Step-by-Step Training (event_v3)

**Step 1 — Ternary labeling, default params:**

```bash
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/python-analyzer/train_event_stack_v3.py \
  --data-dir ~/ubuntu-wallet/data \
  --model-dir ~/ubuntu-wallet/models \
  --label-method ternary \
  --horizon 12 \
  --up-thresh 0.015 \
  --down-thresh 0.015 \
  --calibration isotonic
```

**Step 2 — Triple-barrier labeling (more realistic, recommended for live):**

```bash
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/python-analyzer/train_event_stack_v3.py \
  --data-dir ~/ubuntu-wallet/data \
  --model-dir ~/ubuntu-wallet/models \
  --label-method triple_barrier \
  --horizon 12 \
  --tp-pct 0.0175 \
  --sl-pct 0.009 \
  --p-enter 0.65 \
  --delta 0.0 \
  --calibration isotonic
```

**Step 3 — Verify outputs:**

```bash
ls -lh ../models/
# Expected:
# lightgbm_event_v3.pkl
# lightgbm_event_v3_scaler.pkl
# xgboost_event_v3.json
# xgboost_event_v3_scaler.pkl
# stacking_event_v3.pkl
# calibration_event_v3.pkl
# calibration_event_v3_meta.json
# feature_columns_event_v3.json   ← critical, must match inference
# model_meta.json

cat ../models/model_meta.json     # Check version hash, training date, n_features
```

**Step 4 — Restart ml-service to pick up new models:**

```bash
sudo systemctl restart ml-service
curl -fsS http://127.0.0.1:9000/healthz | python3 -m json.tool
# Verify: model_version matches new training run
```

### 6.3 Label Method Comparison

| Method | Pros | Cons |
|--------|------|------|
| `ternary` | Simple, fast to compute | No path simulation; ignores intrabar excursions |
| `triple_barrier` | Realistic TP/SL exit simulation | More complex; label distribution depends heavily on TP/SL ratio |

**Recommendation**: Use `triple_barrier` for production training. Use `ternary` for rapid research / ablations.

### 6.4 Calibration Notes

Calibration is fitted on the held-out **test set** (20% by time). It must not be fitted on training data.

- **`isotonic`** — non-parametric monotone mapping. Best for large test sets (> 500 samples). Can overfit small sets.
- **`sigmoid`** — Platt scaling (logistic regression). More robust on small test sets but less flexible.
- **`none`** — skip calibration; raw stacking probabilities are used.

After calibration, check that the calibration curve is closer to the diagonal using the Brier score printed during training.

### 6.5 Key Hyperparameters

```python
# LightGBM / XGBoost base models (config.py ML_CONFIG)
n_estimators: 500
max_depth: 8
learning_rate: 0.05

# Stacking meta-learner
LogisticRegression(C=1.0, max_iter=1000, multi_class='multinomial')

# Train/test split
train_ratio: 0.80   # first 80% of time for training

# Feature scaling
StandardScaler  # fit on train, apply on test and at inference
```

---

## 7. Walk-Forward Cross-Validation

Walk-forward CV is used to assess model stability over time without lookahead bias. It is **not** used during production training — it is a research/evaluation tool.

### 7.1 Usage

```bash
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/python-analyzer/walkforward_cv.py \
  --data-dir ~/ubuntu-wallet/data \
  --model-dir ~/ubuntu-wallet/models \
  --n-splits 5 \
  --gap-bars 12 \
  --min-train-bars 500 \
  --expanding \
  --label-method triple_barrier \
  --horizon 12 \
  --tp-pct 0.0175 \
  --sl-pct 0.009 \
  --confidence-threshold 0.65
```

### 7.2 Split Logic

With `--expanding` (default), each validation fold sees all prior data as training:

```
Total bars:  [════════════════════════════════════════════]

Fold 0:  Train [▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░]
                                   gap    Val [════════════]

Fold 1:  Train [▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░]
                                          gap    Val [═════]
...
```

The `gap_bars` parameter (default 12) excludes `horizon` bars between train end and val start to prevent any label leakage (forward-looking labels from training touching validation period).

### 7.3 Per-Fold Metrics

For each fold, the following are reported:

```
fold | train_start | train_end | val_start | val_end | n_train | n_val
     | auc_macro   | f1_macro  | precision_long | precision_short
     | brier_score | coverage  | precision_at_threshold
```

**What to look for**:
- Consistent AUC across folds (degradation → regime change or overfitting).
- `brier_score` should decrease after calibration.
- `coverage` (fraction of bars where signal != FLAT) — very low coverage may indicate thresholds are too high.
- `precision_at_threshold` — precision of LONG/SHORT signals above confidence threshold; should be above 0.55 consistently.

### 7.4 Rolling vs Expanding Window

Use `--expanding` (default) for most scenarios. Use `--rolling-train-bars 2000` if you suspect model staleness causes drift — rolling ensures the model only sees recent data.

---

## 8. Inference Flow

### 8.1 Starting ml-service

```bash
~/ubuntu-wallet/ml-service/.venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port 9000
```

Or via systemd (production):

```bash
sudo systemctl start ml-service
```

### 8.2 Health Check

```bash
curl -fsS http://127.0.0.1:9000/healthz | python3 -m json.tool
```

Expected response:

```json
{
  "ok": true,
  "model_version": "event_v3:lightgbm:2026-03-12T16:46:11.648910Z:11439d248ae6",
  "model_expected_n_features": 156,
  "calibration_available": true,
  "calibration_method": "isotonic"
}
```

If `ok` is `false`, check `journalctl -u ml-service -n 50`.

### 8.3 Manual Prediction

```bash
# Latest bar prediction
curl -s -X POST http://127.0.0.1:9000/predict \
  -H "Content-Type: application/json" \
  -d '{"symbol": "ETHUSDT", "interval": "1h"}' \
  | python3 -m json.tool
```

```json
{
  "signal": "LONG",
  "confidence": 0.7210,
  "calibrated_confidence": 0.7015,
  "calibration_method": "isotonic",
  "model_version": "event_v3:lightgbm:2026-03-12T16:46:11.648910Z:11439d248ae6",
  "reasons": ["p_long=0.7210>=0.65 delta=0.1850>=0.0"]
}
```

### 8.4 Historical / Backtest Prediction

```bash
# Predict as if running at a specific past timestamp (for backtesting)
curl -s -X POST http://127.0.0.1:9000/predict \
  -H "Content-Type: application/json" \
  -d '{"symbol": "ETHUSDT", "interval": "1h", "as_of_ts": "2026-03-10T08:00:00Z"}' \
  | python3 -m json.tool
```

When `as_of_ts` is provided, `feature_builder` slices the klines history at that timestamp so no future data is used. This is what `backtest_event_v3_http.py` calls in a loop.

### 8.5 Model Version String Format

```
event_v3:<base_model>:<trained_at_iso>:<hash_8chars>

Example:
event_v3:lightgbm:2026-03-12T16:46:11.648910Z:11439d248ae6
```

The hash is derived from model artifact content (SHA-256 prefix). Track this to confirm the correct model is loaded after a retrain.

---

## 9. Prediction Logging Format

Every call to `POST /predict` appends one line to `data/predictions_log.jsonl`.

### 9.1 Full Record Schema

```json
{
  "ts": "2026-03-15T12:00:00Z",
  "symbol": "ETHUSDT",
  "interval": "1h",
  "proba_long": 0.7210,
  "proba_short": 0.1820,
  "proba_flat": 0.0970,
  "cal_proba_long": 0.7015,
  "cal_proba_short": 0.1780,
  "cal_proba_flat": 0.1205,
  "signal": "LONG",
  "confidence": 0.7210,
  "calibrated_confidence": 0.7015,
  "calibration_method": "isotonic",
  "model_version": "event_v3:lightgbm:2026-03-12T16:46:11.648910Z:11439d248ae6",
  "active_model": "event_v3",
  "threshold_long": 0.65,
  "threshold_short": 0.65,
  "trend_4h": "UP",
  "trend_1d": "NEUTRAL",
  "as_of_ts": "latest"
}
```

### 9.2 Field Notes

| Field | Source | Notes |
|-------|--------|-------|
| `ts` | Wall-clock time of prediction | ISO-8601 UTC |
| `proba_{long,short,flat}` | Raw stacking output | Before calibration |
| `cal_proba_*` | After isotonic/sigmoid mapping | Used for final decision |
| `signal` | Decision after threshold check | LONG / SHORT / FLAT |
| `confidence` | `proba_long` or `proba_short` (whichever triggered) | Raw |
| `calibrated_confidence` | Calibrated equivalent | Used in evaluation |
| `trend_4h`, `trend_1d` | SMA-200 direction on respective TF | UP / DOWN / NEUTRAL |
| `as_of_ts` | `"latest"` for live; ISO-8601 for backtest | Key for replay |

### 9.3 Inspecting the Log

```bash
# Last 5 predictions
tail -n 5 data/predictions_log.jsonl | python3 -c \
  "import sys, json; [print(json.dumps(json.loads(l), indent=2)) for l in sys.stdin]"

# Count by signal
cat data/predictions_log.jsonl | python3 -c \
  "import sys, json; from collections import Counter; \
   c=Counter(json.loads(l)['signal'] for l in sys.stdin); print(c)"

# Filter LONG signals above threshold
cat data/predictions_log.jsonl | python3 -c \
  "import sys, json; \
   [print(l.rstrip()) for l in sys.stdin if json.loads(l)['signal']=='LONG']"
```

### 9.4 Log Rotation

The JSONL file grows unbounded. Rotate monthly or when it exceeds 100 MB:

```bash
# Archive and restart
mv data/predictions_log.jsonl data/predictions_log_$(date +%Y%m).jsonl
touch data/predictions_log.jsonl
```

---

## 10. Evaluation Flow

### 10.1 Overview

`scripts/evaluate_from_logs.py` replays logged predictions against actual OHLCV data using the same triple-barrier logic as the backtest. This gives you a realistic picture of what live trading would have returned.

### 10.2 Running Evaluation

```bash
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/scripts/evaluate_from_logs.py \
  --log-path ~/ubuntu-wallet/data/predictions_log.jsonl \
  --data-dir ~/ubuntu-wallet/data \
  --interval 1h \
  --active-model event_v3 \
  --threshold 0.55 \
  --tp 0.0175 \
  --sl 0.007 \
  --fee 0.0004 \
  --horizon-bars 6 \
  --output-csv /tmp/eval_$(date +%Y%m%d).csv
```

### 10.3 Parameter Notes

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--threshold` | 0.55 | Minimum `calibrated_confidence` to act on a signal |
| `--tp` | 0.0175 | Take-profit: +1.75% from entry |
| `--sl` | 0.007 | Stop-loss: -0.70% from entry |
| `--fee` | 0.0004 | Round-trip taker fee per side (Binance perp) |
| `--horizon-bars` | 6 | Max hold time in 1h bars |

### 10.4 Output Metrics

```
total_predictions: 156
actionable (>= threshold): 42
long_signals: 28
short_signals: 14
tp_exits: 35  (83%)
sl_exits:  4  ( 9%)
timeout_exits: 3  ( 7%)
win_rate: 0.857
avg_return_pct: 1.32%
total_return_pct: 55.4%
max_drawdown_pct: 3.2%
profit_factor: 12.1
sharpe_ratio: 2.4
```

### 10.5 Reconciling Backtest vs Live

When comparing backtest to live evaluation, check:

1. **Model version in log** matches the model used in the backtest — different versions are not comparable.
2. **Threshold consistency** — use the same `threshold` in both.
3. **Timestamp alignment** — `as_of_ts` in predictions_log.jsonl for live predictions is `"latest"`, meaning they were issued at bar close. For historical predictions with `as_of_ts` set, those are the comparable ones.

---

## 11. Deployment (Systemd)

### 11.1 Fresh Install

```bash
# 1. Clone and install
git clone <repo>
cd ubuntu-wallet
sudo bash scripts/install.sh

# 2. Configure environment
sudo mkdir -p /etc/ubuntu-wallet
sudo cp systemd/collector.env.example /etc/ubuntu-wallet/collector.env
sudo cp systemd/telegram.env.example  /etc/ubuntu-wallet/telegram.env
sudo nano /etc/ubuntu-wallet/collector.env  # Add API keys

# 3. Build go-collector
cd go-collector && go build -o ../bin/go-collector . && cd ..

# 4. Train a model before starting ml-service
cd python-analyzer
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/python-analyzer/train_event_stack_v3.py \
  --data-dir ../data --model-dir ../models \
  --label-method triple_barrier \
  --tp-pct 0.0175 --sl-pct 0.009 --calibration isotonic
cd ..

# 5. Install and enable systemd units
sudo cp systemd/*.service /etc/systemd/system/
sudo cp systemd/*.timer   /etc/systemd/system/
sudo systemctl daemon-reload

# 6. Start services
sudo systemctl enable --now go-collector
sudo systemctl enable --now ml-service
sudo systemctl enable --now check-go-collector.timer
```

### 11.2 Service Management

```bash
# Status
sudo systemctl status go-collector ml-service

# Logs (last 100 lines, follow)
journalctl -u go-collector -n 100 -f
journalctl -u ml-service   -n 100 -f

# Restart after config/model change
sudo systemctl restart ml-service

# Stop everything
sudo systemctl stop go-collector ml-service
```

### 11.3 Health Monitor Timer

The `check-go-collector.timer` fires `check-go-collector.service` (oneshot) every 60 seconds. The service runs `scripts/ops/check-go-collector.sh` which:

1. Calls `GET http://127.0.0.1:8080/healthz`.
2. If unreachable or `ok: false`, sends a Telegram alert.
3. Optionally restarts go-collector after a 5-minute cooldown.

The `evaluate-predictions.timer` triggers `evaluate_from_logs.py` four times per day (at 00:06, 06:06, 12:06, 18:06 local time) with a `RandomizedDelaySec=120` jitter. The 120-second randomized delay staggers the evaluation job so it distributes the workload within a 2-minute window (e.g., 00:06:00 to 00:08:00) to avoid multiple instances starting simultaneously if running on multiple nodes.

Configure Telegram in `/etc/ubuntu-wallet/telegram.env`:
```
TELEGRAM_BOT_TOKEN=<your_bot_token>
TELEGRAM_CHAT_ID=<your_chat_id>
```

### 11.4 Environment Files

**`/etc/ubuntu-wallet/collector.env`** (go-collector runtime):

```ini
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
OKX_API_KEY=...
OKX_API_SECRET=...
OKX_PASSPHRASE=...
COINBASE_API_KEY=...
COINBASE_API_SECRET=...
COLLECTOR_PORT=8080
COLLECT_INTERVAL=60s
KLINES_LOOKBACK_MODE=on_startup
DATA_DIR=/home/ubuntu/ubuntu-wallet/data
ML_SERVICE_URL=http://127.0.0.1:9000/predict
```

**ml-service env (in `ml-service.service` or `.env` in ml-service dir)**:

```ini
MODEL_DIR=/home/ubuntu/ubuntu-wallet/models
DATA_DIR=/home/ubuntu/ubuntu-wallet/data
EVENT_V3_P_ENTER=0.65
EVENT_V3_DELTA=0.0
ML_PROBA_LONG=0.55
ML_PROBA_SHORT=0.45
```

### 11.5 Retraining in Production

```bash
# 1. Stop ml-service to avoid reading partial model files
sudo systemctl stop ml-service

# 2. Train new model (klines must be populated)
cd python-analyzer
~/ubuntu-wallet/ml-service/.venv/bin/python ~/ubuntu-wallet/python-analyzer/train_event_stack_v3.py \
  --data-dir ../data --model-dir ../models \
  --label-method triple_barrier \
  --tp-pct 0.0175 --sl-pct 0.009 --calibration isotonic

# 3. Verify model_meta.json updated
cat ../models/model_meta.json

# 4. Restart ml-service
sudo systemctl start ml-service

# 5. Verify healthz shows new model version
curl -fsS http://127.0.0.1:9000/healthz | python3 -m json.tool
```

---

## 12. Configuration Reference

### 12.1 go-collector Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BINANCE_API_KEY` | — | Binance API key (read-only sufficient for collection) |
| `BINANCE_API_SECRET` | — | Binance API secret |
| `OKX_API_KEY` | — | OKX API key |
| `OKX_API_SECRET` | — | OKX API secret |
| `OKX_PASSPHRASE` | — | OKX passphrase |
| `COINBASE_API_KEY` | — | Coinbase API key |
| `COINBASE_API_SECRET` | — | Coinbase API secret |
| `COLLECTOR_PORT` | `8080` | go-collector HTTP port |
| `COLLECT_INTERVAL` | `60s` | Polling frequency (Go duration string) |
| `KLINES_LOOKBACK_MODE` | `on_startup` | `on_startup` = backfill on start; `always` = every tick; `off` = no backfill |
| `DATA_DIR` | `../data` | Path to JSON data directory |
| `ML_SERVICE_URL` | `http://127.0.0.1:9000/predict` | ml-service endpoint |

### 12.2 ml-service Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MODEL_DIR` | `../models` | Path to model artifacts |
| `DATA_DIR` | `../data` | Path to klines JSON files |
| `EVENT_V3_P_ENTER` | `0.65` | Minimum calibrated probability to trigger LONG/SHORT |
| `EVENT_V3_DELTA` | `0.0` | Minimum margin `p_long - p_short` (or vice versa) |
| `ML_PROBA_LONG` | `0.55` | Threshold for legacy binary model (LONG) |
| `ML_PROBA_SHORT` | `0.45` | Threshold for legacy binary model (SHORT) |

### 12.3 Training CLI Arguments (train_event_stack_v3.py)

| Argument | Default | Description |
|----------|---------|-------------|
| `--data-dir` | `data` | Input OHLCV directory |
| `--model-dir` | `models` | Output model directory |
| `--label-method` | `ternary` | `ternary` or `triple_barrier` |
| `--horizon` | `12` | Forward look-ahead bars (1h bars = 12h) |
| `--up-thresh` | `0.015` | Ternary LONG threshold (+1.5%) |
| `--down-thresh` | `0.015` | Ternary SHORT threshold (-1.5%) |
| `--tp-pct` | `0.0175` | Triple-barrier take-profit (+1.75%) |
| `--sl-pct` | `0.009` | Triple-barrier stop-loss (-0.90%) |
| `--p-enter` | `0.65` | Stored in model_meta.json as recommended threshold |
| `--delta` | `0.0` | Stored in model_meta.json as recommended delta |
| `--calibration` | `isotonic` | `isotonic`, `sigmoid`, or `none` |

### 12.4 Technical Analysis Config (config.py TA_CONFIG)

```python
sma_periods:    [7, 25, 99, 200]
ema_periods:    [12, 26, 50, 200]
rsi_period:     14
macd_fast:      12
macd_slow:      26
macd_signal:    9
bollinger_period: 20
bollinger_std:    2
atr_period:     14
stochastic_k:   14
stochastic_d:   3
```

---

## 13. Multi-Timeframe Design

### 13.1 Scheme B Filter Logic

The system uses a **Scheme B** multi-timeframe filter for directional bias:

```
1h signal = LONG is confirmed if:
    4h trend == UP   (must agree)
    AND 1d trend != DOWN  (must not oppose)

1h signal = SHORT is confirmed if:
    4h trend == DOWN   (must agree)
    AND 1d trend != UP  (must not oppose)
```

Trend on 4h and 1d is defined as: price above/below SMA-200.

```python
def get_trend(df, sma_col='sma_200'):
    last = df.iloc[-1]
    if last['close'] > last[sma_col]: return 'UP'
    if last['close'] < last[sma_col]: return 'DOWN'
    return 'NEUTRAL'
```

### 13.2 Online vs Offline Alignment

**At training time** (`train_event_stack_v3.py`):

- Multi-TF features are built using `merge_asof` (backward merge) — for each 1h row, the most recent 4h and 1d bar *that has already closed* is joined.
- This is strictly non-lookahead. A 4h bar closing at 08:00 is available for 1h bars from 08:00 onward.

**At inference time** (`feature_builder.build_event_v3_feature_row`):

- The same `merge_asof` logic is applied.
- The current 1h bar must be **closed** before calling `/predict` to avoid using a partial bar's features. go-collector is designed to call `/predict` only after bar close.

**Critical alignment rule**: Always call `/predict` at or after the 1h candle close (e.g., at xx:01 or xx:02). Calling mid-bar will use features from an incomplete candle that will differ from what the model was trained on.

### 13.3 4h/1d Feature Prefixes

In the training feature matrix and at inference, 4h-derived features are prefixed `tf4h_` and 1d features are prefixed `tf1d_`. Examples:

```
tf4h_close, tf4h_ema_200, tf4h_rsi_14, tf4h_macd, tf4h_volume
tf1d_close, tf1d_sma_200, tf1d_rsi_14, tf1d_atr_14
```

### 13.4 Feature Schema Consistency

`models/feature_columns_event_v3.json` is the **single source of truth** for feature ordering and names. It is:
- Written by `train_event_stack_v3.py` during training.
- Read by `feature_builder.build_event_v3_feature_row` at inference.
- Any mismatch (missing column) is zero-filled with a warning in the logs.

**Never delete or manually edit this file.** Always retrain to regenerate it.

---

## 14. Maintenance & Operations

### 14.1 Routine Checks (Daily)

```bash
# 1. Confirm data is being collected (klines file modified in last 2 minutes)
find data/ -name "klines_1h.json" -mmin -2 && echo "OK" || echo "STALE"

# 2. Check ml-service health
curl -fsS http://127.0.0.1:9000/healthz | python3 -c \
  "import sys, json; d=json.load(sys.stdin); print('OK' if d['ok'] else 'FAIL', d)"

# 3. Check prediction log is growing
wc -l data/predictions_log.jsonl

# 4. Check for ERROR lines in ml-service log
journalctl -u ml-service --since "1 hour ago" | grep -i error | head -20
```

### 14.2 Model Staleness

Models should be retrained when:
- More than 2–4 weeks of new data have accumulated.
- Regime change is suspected (model AUC drops consistently in walk-forward CV).
- The prediction distribution shifts significantly (check via `evaluate_from_logs.py`).

A rough signal for staleness: if the last 2 weeks of logged predictions show `win_rate < 0.45` or `coverage < 0.05` (near-zero actionable signals).

### 14.3 Data Backfill

If go-collector was down for an extended period, backfill klines before retraining:

```bash
# Set KLINES_LOOKBACK_MODE=always and restart collector temporarily
sudo systemctl stop go-collector
# Edit /etc/ubuntu-wallet/collector.env: KLINES_LOOKBACK_MODE=always
sudo systemctl start go-collector
sleep 300  # allow 5 minutes for backfill
# Revert: KLINES_LOOKBACK_MODE=on_startup
sudo systemctl restart go-collector
```

### 14.4 Disk Space

Key files to monitor:

| File | Growth Rate | Action |
|------|-------------|--------|
| `data/klines_1h.json` | ~1 KB/bar | Trim to last 5000 bars if > 100 MB |
| `data/predictions_log.jsonl` | ~500 B/prediction | Archive monthly |
| `journald` logs | Variable | Standard logrotate |

```bash
# Check sizes
du -sh data/* models/*
```

### 14.5 Updating go-collector Binary

```bash
cd go-collector
go build -o ../bin/go-collector .
sudo systemctl restart go-collector
```

---

## 15. Common Failure Modes & Debugging

### 15.1 ml-service Fails to Start

**Symptoms**: `systemctl status ml-service` shows `failed`, port 9000 not responding.

**Checks**:
```bash
journalctl -u ml-service -n 50 --no-pager

# Common errors:
# "FileNotFoundError: models/lightgbm_event_v3.pkl"
#   → Model not trained. Run train_event_stack_v3.py first.
#
# "ModuleNotFoundError: No module named 'lightgbm'"
#   → Venv not activated in service file. Check ExecStart path.
#
# "Address already in use"
#   → Port 9000 occupied. Find and kill: lsof -t -i :9000 | xargs kill
```

### 15.2 Predictions Are All FLAT

**Symptoms**: `signal: FLAT` in every response; `coverage` near zero in evaluation.

**Causes and fixes**:

1. **Threshold too high**: `EVENT_V3_P_ENTER=0.65` with a poorly calibrated model may never exceed threshold.  
   → Temporarily lower `EVENT_V3_P_ENTER=0.55` to verify probabilities are being computed.

2. **Feature schema mismatch**: Many features zero-filled → weak predictions.  
   → Check `journalctl -u ml-service | grep "zero-fill"`. If frequent, retrain.

3. **Calibration overcompression**: If calibrated probabilities are being pushed toward 0.5, calibration may have been fit on too few samples.  
   → Retrain with `--calibration none` to compare raw vs calibrated outputs.

4. **Model version mismatch**: Old model loaded from stale `models/` directory.  
   → Check `/healthz` model_version matches expected hash.

### 15.3 go-collector Stops Writing Data

**Symptoms**: `klines_1h.json` not updated; `/healthz` returns `ok: false` or times out.

**Checks**:
```bash
journalctl -u go-collector -n 100 --no-pager

# Common errors:
# "401 Unauthorized" → Check API keys in collector.env
# "429 Too Many Requests" → Reduce COLLECT_INTERVAL or add jitter
# "connection refused" → Network/firewall issue to Binance
# "write data/klines_1h.json: permission denied" → Fix file permissions
```

### 15.4 Feature Count Mismatch

**Symptom**: `healthz` shows `model_expected_n_features: 156`, but inference logs show a different count.

```bash
# Check what features the model expects
cat models/feature_columns_event_v3.json | python3 -c \
  "import sys, json; cols=json.load(sys.stdin); print(len(cols), cols[:5], '...', cols[-5:])"

# Check what the current klines produce
python3 -c "
from ml_service.feature_builder import build_multi_tf_feature_df
df = build_multi_tf_feature_df('../data')
print(df.shape, df.columns.tolist()[:10])
"
```

If counts diverge, the klines schema or TA library version has changed. Retrain to regenerate `feature_columns_event_v3.json`.

### 15.5 Klines JSON Corrupt

If go-collector crashes mid-write, `klines_*.json` may be truncated or have invalid JSON.

```bash
python3 -c "import json; json.load(open('data/klines_1h.json'))" \
  && echo "JSON valid" || echo "JSON CORRUPT"
```

If corrupt, restore from backup or allow go-collector to re-download with `KLINES_LOOKBACK_MODE=on_startup`.

### 15.6 XGBoost Model Load Error

XGBoost native `.json` format requires matching XGBoost version between training and inference.

```bash
# Check version
python3 -c "import xgboost; print(xgboost.__version__)"

# If training machine and server differ, save as .pkl instead:
# In train_event_stack_v3.py, use joblib.dump(xgb_model, 'xgboost_event_v3.pkl')
# And in model_loader.py, _load_xgb_artifact will detect .pkl automatically
```

---

## 16. What to Watch Out For

### 16.1 Lookahead Bias in Features

The most common silent bug. It occurs when:
- Rolling statistics (e.g., `rolling_std_20`) are computed on the full DataFrame before the train/test split.
- Labels use information that was not available at bar close.

**How the system prevents it**:
- `labeling.py` uses `close.shift(-horizon)` — labels are NaN for the last `horizon` bars and are dropped.
- `merge_asof` with 4h/1d features uses `direction='backward'` — only past bars are merged.
- Walk-forward CV uses `gap_bars` to prevent label overlap.

**What to check**: If your backtest shows suspiciously high win rates (>85% with large coverage), re-examine whether any feature is computed with future data.

### 16.2 Label Leakage via Feature Windows

A 200-period SMA computed on the full dataset leaks information: the SMA at bar 100 includes bars 101–299. Always compute rolling features **per row up to and including that row** (i.e., `rolling(200).mean()` without lookahead).

The `TechnicalAnalyzer` class uses standard pandas rolling which is lag-safe. Verify any custom features added follow the same pattern.

### 16.3 Class Imbalance

Ternary labels typically produce: `FLAT >> LONG ≈ SHORT` (often 60–70% FLAT).

The training script uses class weights or oversampling to handle this, but if FLAT dominates excessively, predictions tend toward FLAT. Check:
```bash
cat models/model_meta.json | python3 -m json.tool | grep -A5 label_dist
```

### 16.4 Bar Close Timing

go-collector must call `/predict` only after a 1h bar is fully closed. If predictions are made on a partial bar (e.g., at xx:55), the close price is not final, and the trained model's expectation of `close` being the bar close does not hold.

**Production safeguard**: Schedule the collect-and-predict loop to run at xx:02 (2 minutes after the hour) to ensure the exchange has published the closed bar.

### 16.5 Calibration Drift

Calibration is fit once on the test holdout. Over time, if the underlying model's raw probability distribution changes (due to market regime shifts), calibration maps may become inaccurate.

**Signal**: Brier score on recent predictions increases noticeably. **Fix**: Retrain the full pipeline including calibration.

### 16.6 Risk Controls (Live Trading)

Before enabling live order execution:

1. Confirm `EthPerpStrategyEngineBinance` is taken out of `dry_run=True` mode.
2. Confirm circuit breaker settings: `max_consecutive_losses=3`.
3. Never exceed `max_position_fraction=0.50` (50% of strategy fund).
4. Position timeout of 6 hours must close regardless of signal state.
5. Review `docs/ETH_perp_risk_rules.md` fully before enabling live trading.

### 16.7 Exchange API Key Permissions

go-collector API keys need:
- **Binance**: Read-only (no trading needed for collection; leaderboard API is public).
- **OKX**: Read market data permission.
- **Coinbase**: Read permission.

Do **not** grant withdrawal permissions to these keys. Use separate dedicated trading keys if live order execution is enabled.

### 16.8 Model Version Tracking

Always record the `model_version` hash when starting a backtest or live session. All entries in `predictions_log.jsonl` include the model version. If you retrain mid-session, your log will contain predictions from two different model versions — ensure `evaluate_from_logs.py` is filtered to a single version using `jq` or the `--active-model` flag behavior.

---

*This document reflects the current state of the ubuntu-wallet codebase. Update after any significant architecture change, new training run configuration, or deployment topology change.*
