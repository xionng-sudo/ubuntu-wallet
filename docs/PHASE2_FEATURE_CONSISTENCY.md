# Phase 2: Hardened Drift Baselines, Self-Heal Fix, and Expanded Feature Parity

## Summary

Phase 2 addresses the known issues deferred after Phase 1 production validation. It is an incremental, production-safe set of improvements. Full architecture migration (Python as single feature source of truth) remains future work.

**Phase 1 recap:** PR #25 fixed the `price_to_ma_*` semantic mismatch (Go was computing `(close−MA)/MA`, now correctly computes `close/MA` matching Python training). Live verification confirmed correctness before Phase 2 began.

---

## A. Self-heal service lock path fix

### Problem
`scripts/ops/check-go-collector.sh` previously used:
```
LOCK_FILE="/run/ubuntu-wallet/check-go-collector.lock"
```
The `check-go-collector.service` runs as user `ubuntu`. The `/run/ubuntu-wallet/` directory requires root to create, causing every run to fail with:
```
mkdir: cannot create directory '/run/ubuntu-wallet': Permission denied
```

This caused the service to exit with `status=1/FAILURE` every minute for an extended period.

### Fix
The lock path was changed (hotfix applied locally, now persisted in git) to:
```
LOCK_FILE="/home/ubuntu/ubuntu-wallet/data/tmp/check-go-collector.lock"
```
This path is writable by the `ubuntu` user and resides within the project data directory.

**File changed:** `scripts/ops/check-go-collector.sh` (line 5)
**Documentation updated:** `go-collector/OPS-NOTES.md` sections 1.3 and 4.3

---

## B. Drift report: invalid training baseline handling

### Problem
When `train_std == 0` (or effectively zero), the previous code used:
```python
denom_std = max(abs(train_std), 1e-6)
mean_drift = abs(live_mean - train_mean) / denom_std
```
If a live feature had any nonzero value (e.g., `live_mean = 0.5`), this produced:
```
mean_drift = 0.5 / 1e-6 = 500000
```
These enormous values appeared in drift reports and High-Drift feature tables, making it impossible to distinguish real drift from degenerate baseline issues.

### Fix
Added `invalid_baseline` detection in `scripts/report_drift.py`:

```python
invalid_baseline = abs(train_std) < 1e-8
```

When `invalid_baseline` is `True`:
- `mean_drift` and `std_drift` are set to `null` / `None`
- The feature is **excluded from the High-Drift section**
- The feature is **listed in a separate "Invalid Training Baseline" section** in the markdown report
- PSI is skipped (no meaningful baseline to compare against)
- The JSON report includes `"invalid_baseline": true` for machine-readable filtering

The markdown report now contains:
1. **High-Drift Features (mean_drift > 1σ)** — only features with valid baselines
2. **Features with Invalid Training Baseline (train_std ≈ 0)** — operator-visible list with raw live stats and a clear action note

### Operator action required
Features listed in the Invalid Baseline section require `train_feature_stats.json` to be regenerated from training data that includes real (non-placeholder) values. Until then, they are unmonitored for drift.

---

## C. Trader-flow baseline issue

### Root cause
`trader_buy_ratio`, `trader_sell_ratio`, `trader_net_flow` (and their `tf4h_*` / `tf1d_*` variants) have `mean=0, std=0` in `train_feature_stats.json` because the training pipeline in `ml-service/feature_builder.py` fills these as placeholder zeros when real trade-flow data is unavailable:

```python
# ml-service/feature_builder.py lines 315–317
for col in ["trader_buy_ratio", "trader_sell_ratio", "trader_net_flow"]:
    if col not in df.columns:
        df[col] = 0.0
```

Any model trained without real trader-flow data will record `mean=0, std=0` in its stats file.

### Fix in this PR
The `invalid_baseline` detection introduced in Section B automatically catches all trader-flow features (and any other all-zero features). They are now surfaced in the "Invalid Training Baseline" section instead of producing enormous drift numbers.

### Remaining work (not in this PR)
- **If real trader-flow data is available in the training set:** regenerate `train_feature_stats.json` using that data. The reported `invalid_baseline` features will then switch to normal drift monitoring automatically.
- **If real trader-flow data is not available:** accept that these features are unmonitored and document them explicitly in per-symbol model metadata.

---

## D. Expanded feature parity test coverage

### New tests in `go-collector/features/parity_test.go`

| Test | Purpose |
|------|---------|
| `TestRollingMean_MatchesPythonSemantics` | Verifies `meanLast(closes, w)` matches Python's `rolling(w).mean()` — they are identical (simple mean). |
| `TestRollingStd_GoUsesPopulationStd` | Documents that Go uses **population std** (ddof=0) while Python uses **sample std** (ddof=1). Quantifies the known difference: `python_std = go_std * sqrt(n/(n-1))`. |
| `TestVWAP_GoUsesWindowedFormula` | Documents that Go VWAP is computed over the **last 20 candles** (windowed), while Python's `TechnicalAnalyzer._calc_vwap()` uses **cumulative VWAP** from the first row. These are semantically different. |
| `TestVolumeRatio_MatchesPythonFormula` | Verifies `volume / sma(vols, 20)` matches Python's `df["volume"] / df["volume_sma_20"]`. These are equivalent. |

### Known parity gaps documented (not fixed in Phase 2)

| Feature | Go formula | Python formula | Gap type |
|---------|-----------|---------------|----------|
| `rolling_std_*` | population std, ddof=0 | sample std, ddof=1 | constant factor `sqrt(n/(n-1))` |
| `vwap` | windowed (last 20 candles) | cumulative from series start | different semantics |
| `adx`, `plus_di`, `minus_di` | Wilder smoothing from scratch each call | same general approach in `ta` library | potentially different seed/warmup |

Fixing `rolling_std_*` ddof requires either:
1. Updating Go to use ddof=1 (add the `sqrt(n/(n-1))` correction), or
2. Updating the Python training/inference to use ddof=0

Either change requires a model retraining cycle and is deferred to a future PR.

---

## E. Placeholder-zero (`safe()`) behavior in Go

### Current behavior
Go's `safe()` converts NaN/Inf to `0`. Additionally, many indicator functions return `0` when there is insufficient historical data (fewer candles than the lookback period):

```go
func safe(v float64) float64 {
    if math.IsNaN(v) || math.IsInf(v, 0) {
        return 0
    }
    return v
}
```

Features susceptible to placeholder-zero behavior at startup or with short history:
- `sma_*`, `ema_*` — return 0 if `len(closes) < period`
- `atr` — returns 0 if `len(closes) < period + 1`
- `adx`, `plus_di`, `minus_di` — return 0 if `len(closes) < period + 2`
- `bollinger_*`, `keltner_*` — return 0 for insufficient history
- `trader_buy_ratio`, `trader_sell_ratio`, `trader_net_flow` — return 0 if no real trade-flow data is available from exchange

### Current mitigation
- The `isStartup=true` flag in `go-collector` triggers a lookback kline fetch on startup to warm up history. This reduces (but does not eliminate) the zero-fill window.
- The `invalid_baseline` detection in drift reports will surface features that are systematically zero in training data.

### Remaining work (not in this PR)
- Add explicit `is_placeholder` flags or `null` instead of `0` for features that cannot be computed due to insufficient history.
- This requires changes to the feature output schema and the model input pipeline, deferred to a future architecture migration PR.

---

## F. Remaining longer-term work toward single-source-of-truth

The following items remain open after Phase 2 and should be addressed in future PRs:

1. **Full Go/Python feature audit (~200 features):** Only `price_to_ma_*`, `rolling_mean_*`, `rolling_std_*` (documented gap), `vwap` (documented gap), and `volume_ratio` have been audited. The remaining ~170 features have not been cross-verified.

2. **Fix `rolling_std_*` ddof:** Change Go from ddof=0 to ddof=1 (or Python to ddof=0) and retrain. Estimated risk: low impact on model performance, high confidence of improvement in feature consistency.

3. **Fix VWAP semantics:** Align Go to use cumulative VWAP (matching Python), or update Python to use windowed VWAP. Cumulative VWAP is more standard in trading contexts.

4. **Single-source feature generation:** Ultimately the most robust solution is to compute features in Python (the training source of truth) and forward them to Go for model serving, eliminating the duplicate implementation entirely.

---

## Acceptance criteria (Phase 2)

- [x] `check-go-collector.service` no longer fails with `/run/ubuntu-wallet` permission error
- [x] Drift reports no longer show bogus enormous values for features with `train_std=0`
- [x] `trader_buy_ratio`, `trader_sell_ratio`, `trader_net_flow` (and tf variants) appear in "Invalid Training Baseline" section instead of "High-Drift Features"
- [x] Additional parity tests cover `rolling_mean`, `rolling_std` (ddof gap documented), VWAP (semantic gap documented), `volume_ratio`
- [x] All tests pass in CI (`go test ./features/...`, Python unit tests for drift report)
- [x] Documentation clearly marks this as Phase 2 incremental work with remaining items listed

---

## Files changed in Phase 2

| File | Change |
|------|--------|
| `scripts/ops/check-go-collector.sh` | Lock path already fixed (hotfix preserved in git) |
| `go-collector/OPS-NOTES.md` | Updated lock path references in sections 1.3 and 4.3; added Phase 2 context note |
| `scripts/report_drift.py` | Added `invalid_baseline` detection, `None` drift for zero-std features, separate markdown section |
| `go-collector/features/parity_test.go` | Added Phase 2 parity tests: rolling_mean, rolling_std ddof, VWAP windowed, volume_ratio |
| `tests/test_train_feature_stats.py` | Fixed pre-existing test bugs (wrong kwarg names); added invalid_baseline test class |
| `docs/PHASE2_FEATURE_CONSISTENCY.md` | This file |
