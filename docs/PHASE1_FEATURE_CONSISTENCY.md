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

## 3. Scope boundaries (Phase 1 only)

**In scope (Phase 1):**
- Correcting `price_to_ma_*` formula in Go
- Adding parity regression tests

**Out of scope (deferred to Phase 2):**
- Full migration to Python as single feature source of truth
- Rewriting all feature generation in Go
- Schema/model format changes
- Automated cross-language CI (Python-invoked-from-Go tests)

---

## 4. Phase 2 notes

Phase 2 will further reduce divergence between Go and Python feature generation, potentially moving toward Python as the single source of truth for feature computation. Phase 1 validation (monitoring that live `price_to_ma_*` values now land near 1.0 rather than 0.0) should be confirmed before starting Phase 2.
