# Phase 1: Feature-Consistency Architecture Migration

## Summary

Phase 1 corrects a discovered production mismatch in the `price_to_ma_*` feature family and adds automated guardrails to detect future Go/Python divergence.

---

## 1. Mismatch fixed

**Feature family:** `price_to_ma_{w}`, `tf4h_price_to_ma_{w}`, `tf1d_price_to_ma_{w}` (windows 5, 10, 20, 50)

| | Formula | Example (close=149, MA=147) |
|---|---|---|
| **Python** (training/inference) | `close / rolling_mean_{w}` | 1.0136 |
| **Go** (before Phase 1) | `(close - MA) / MA` | 0.0136 |

The Go implementation in `go-collector/features/compute.go` was using `(close − MA) / MA` (returns a value near 0), while Python training/inference in `ml-service/feature_builder.py` uses `close / MA` (returns a value near 1). This caused significant feature drift for every live prediction.

**Fix:** `go-collector/features/compute.go` — `priceToMA()` now returns `close / ma` to match Python semantics.

---

## 2. Parity regression test added

**File:** `go-collector/features/parity_test.go`

Tests included:
- `TestPriceToMA_MatchesPythonSemantics` — Go vs. inline Python-equivalent formula for windows 5, 10, 20, 50
- `TestPriceToMA_FixedExpectedValues` — hardcoded expected outputs derived from Python formula (deterministic)
- `TestPriceToMA_NotCenteredAroundZero` — guards against regression to old `(close-MA)/MA` formula
- `TestPriceToMA_TF_PrefixConsistency` — verifies tf4h/tf1d prefix path uses the same formula
- `TestPriceToMA_EdgeCases` — boundary conditions (insufficient data, zero window, all-zero closes)

All tests are deterministic and pass in CI (`go test ./features/...`).

---

## 3. Known unresolved issues (confirmed, not fixed in Phase 1)

The following issues were identified during production investigation but are deferred to Phase 2. They must not be treated as resolved after Phase 1 merges.

### 3.1 `trader_*` features have zero mean/std in `train_feature_stats.json`

`trader_buy_ratio`, `trader_sell_ratio`, and `trader_net_flow` are filled with `0.0` as placeholders when real trade-flow data is unavailable (see `ml-service/feature_builder.py` lines 315–317). As a result, any `train_feature_stats.json` generated from training data that lacked real trader-flow will record `mean=0, std=0` for these features, making drift detection for them unreliable.

**Risk:** Drift monitoring cannot detect degradation in trader-flow features if the training baseline is all-zero.

### 3.2 Live Go output may still contain 0-placeholder values for some features

Go feature generation uses `safe()` which returns 0 for NaN/Inf, and several code paths fall back to 0 when there is insufficient history (e.g., `sma`, `ema`, `rsi`, `atr` with fewer candles than the lookback period). Some of these zeros may silently pass through to the model as if they were real feature values.

**Risk:** Partial feature absence or placeholder zeros in live features can degrade prediction quality without triggering alerts if they are not separately monitored.

### 3.3 Phase 1 scope is limited to `price_to_ma_*` only

Only the `price_to_ma_*` family has been audited and corrected. The remaining ~200 features shared between Go and Python have **not** been cross-verified for semantic equivalence. Additional definition mismatches may exist.

---

## 4. File cleanup audit (Phase 1)

The following repository paths were reviewed for safe deletion as part of Phase 1:

- `scripts/report_drift.py` — **retained**. Actively referenced in `tests/test_train_feature_stats.py`, `tests/test_per_symbol_artifacts.py`, `tests/test_multi_symbol.py`, and `systemd/drift-monitor.service`. Still in active use; deletion would break tests and production drift monitoring.
- `systemd/drift-monitor.service` / `systemd/drift-monitor.timer` — **retained**. Live systemd units for scheduled drift monitoring; not obsolete.
- All other scripts reviewed — **retained**. No file in the repository was found to be demonstrably unused or superseded after the drift/reporting path corrections.

**Conclusion:** No safe-to-delete files were identified in Phase 1. The drift/reporting infrastructure is still in use and should not be touched until Phase 2 audit is complete.

---

## 5. Scope boundaries (Phase 1 only)

**In scope (Phase 1):**
- Correcting `price_to_ma_*` formula in Go
- Adding parity regression tests

**Out of scope (deferred to Phase 2):**
- Full migration to Python as single feature source of truth
- Rewriting all feature generation in Go
- Schema/model format changes
- Automated cross-language CI (Python-invoked-from-Go tests)
- Remaining feature consistency audit (all features other than `price_to_ma_*`)
- Fixing `trader_*` zero-baseline in `train_feature_stats.json`
- Eliminating 0-placeholder live feature risks

---

## 6. Phase 2 notes

Phase 2 will further reduce divergence between Go and Python feature generation, potentially moving toward Python as the single source of truth for feature computation. Phase 1 validation (monitoring that live `price_to_ma_*` values now land near 1.0 rather than 0.0) should be confirmed before starting Phase 2.

Recommended Phase 2 entry checklist:
1. Validate Phase 1 fix in production: confirm live `price_to_ma_*` values are now ~1.x, not ~0.
2. Audit `trader_*` feature baseline in training data; regenerate `train_feature_stats.json` with real trader-flow data if available.
3. Full cross-audit of remaining Go vs. Python feature definitions.
4. Decide on single-source-of-truth architecture (Python-generated features forwarded to Go, or Go reimplementing Python faithfully).
