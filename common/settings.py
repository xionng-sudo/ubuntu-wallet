from __future__ import annotations

"""
common/settings.py
==================
Shared Stage + FeatureFlags control plane for ubuntu-wallet.

Usage
-----
    from common.settings import get_settings

    s = get_settings()
    if s.flags.ENABLE_MODEL_REGISTRY:
        ...  # new registry-aware path
    else:
        ...  # legacy flat-root path

Environment variables
---------------------
STAGE
    One of S0, S1, S2, S3, S4 (default: S1).
    Controls the set of flags that are on by default.

ENABLE_<FLAG_NAME>
    Set to "true"/"1"/"yes" or "false"/"0"/"no" to override a single flag
    regardless of the active stage.  Example:
        ENABLE_DRIFT_MONITOR=true  # force-enable even in S1
        ENABLE_DAILY_EVAL_REPORT=false  # force-disable even in S2+

See docs/STAGES_FLAGS_CN.md for the full flag-to-stage mapping.
"""

import dataclasses
import os
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import Optional


# ---------------------------------------------------------------------------
# Stage enum
# ---------------------------------------------------------------------------

class Stage(str, Enum):
    S0_RESEARCH = "S0"       # 研究验证期 – experimental, non-destructive tools only
    S1_PROD_CANDIDATE = "S1" # 生产候选 – stable baseline + reports + registry
    S2_OPERABLE = "S2"       # 可运维 – drift/calibration monitoring added
    S3_PRE_LIVE = "S3"       # 准实盘 – risk guards enabled
    S4_AUTOMATION = "S4"     # 平台化 – scheduled retrain + auto promote/rollback


# ---------------------------------------------------------------------------
# Helpers for env-var parsing
# ---------------------------------------------------------------------------

def _parse_bool(s: str) -> bool:
    """Parse a human-friendly boolean string into a Python bool."""
    s = s.strip().lower()
    if s in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "f", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid bool value: {s!r}")


def _get_env_bool(name: str) -> Optional[bool]:
    """Return the boolean value of an env-var, or None if not set."""
    v = os.getenv(name)
    if v is None or v == "":
        return None
    return _parse_bool(v)


def _get_env_stage() -> Stage:
    """Read STAGE env-var and return the corresponding Stage enum."""
    v = os.getenv("STAGE", "S1").strip().upper()
    try:
        return Stage(v)
    except ValueError as e:
        allowed = ", ".join(s.value for s in Stage)
        raise ValueError(
            f"Invalid STAGE={v!r}. Allowed values: {allowed}"
        ) from e


# ---------------------------------------------------------------------------
# FeatureFlags dataclass – one bool per capability
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FeatureFlags:
    # ---- Reports / evaluation (Issues 3, 4, 7) ----
    ENABLE_THRESHOLD_GRID_REPORT: bool = False    # Issue 3
    ENABLE_DAILY_EVAL_REPORT: bool = False         # Issue 4
    ENABLE_CALIBRATION_REPORT: bool = False        # Issue 7

    # ---- Monitoring (Issue 6) ----
    ENABLE_DRIFT_MONITOR: bool = False             # Issue 6

    # ---- Model governance (Issues 2, 14) ----
    ENABLE_MODEL_REGISTRY: bool = False            # Issue 2
    ENABLE_PROMOTE_ROLLBACK_AUTOMATION: bool = False  # Issue 14

    # ---- Features / data (Issues 1, 5) ----
    ENABLE_MTF_FEATURES: bool = False              # Issue 1 – multi-timeframe
    ENABLE_EXOGENOUS_FEATURES: bool = False        # Issue 5 – funding/OI/taker

    # ---- Risk / trading guards (Issue 12) ----
    ENABLE_PERP_RISK_GUARDS: bool = False          # Issue 12

    # ---- Automation (Issue 13) ----
    ENABLE_SCHEDULED_RETRAIN: bool = False         # Issue 13


# ---------------------------------------------------------------------------
# Default flags per stage
# ---------------------------------------------------------------------------

STAGE_DEFAULT_FLAGS: dict[Stage, FeatureFlags] = {
    # S0: research-only – non-destructive analysis tools
    Stage.S0_RESEARCH: FeatureFlags(
        ENABLE_THRESHOLD_GRID_REPORT=True,
        ENABLE_MTF_FEATURES=True,
    ),

    # S1: production candidate – reporting pipeline + model registry on
    Stage.S1_PROD_CANDIDATE: FeatureFlags(
        ENABLE_THRESHOLD_GRID_REPORT=True,
        ENABLE_DAILY_EVAL_REPORT=True,
        ENABLE_MODEL_REGISTRY=True,
        ENABLE_MTF_FEATURES=True,
    ),

    # S2: operable – add drift + calibration monitoring
    Stage.S2_OPERABLE: FeatureFlags(
        ENABLE_THRESHOLD_GRID_REPORT=True,
        ENABLE_DAILY_EVAL_REPORT=True,
        ENABLE_MODEL_REGISTRY=True,
        ENABLE_MTF_FEATURES=True,
        ENABLE_DRIFT_MONITOR=True,
        ENABLE_CALIBRATION_REPORT=True,
    ),

    # S3: pre-live – perp risk guards enabled
    Stage.S3_PRE_LIVE: FeatureFlags(
        ENABLE_THRESHOLD_GRID_REPORT=True,
        ENABLE_DAILY_EVAL_REPORT=True,
        ENABLE_MODEL_REGISTRY=True,
        ENABLE_MTF_FEATURES=True,
        ENABLE_DRIFT_MONITOR=True,
        ENABLE_CALIBRATION_REPORT=True,
        ENABLE_PERP_RISK_GUARDS=True,
    ),

    # S4: full automation – scheduled retrain + auto promote/rollback
    Stage.S4_AUTOMATION: FeatureFlags(
        ENABLE_THRESHOLD_GRID_REPORT=True,
        ENABLE_DAILY_EVAL_REPORT=True,
        ENABLE_MODEL_REGISTRY=True,
        ENABLE_MTF_FEATURES=True,
        ENABLE_DRIFT_MONITOR=True,
        ENABLE_CALIBRATION_REPORT=True,
        ENABLE_PERP_RISK_GUARDS=True,
        ENABLE_SCHEDULED_RETRAIN=True,
        ENABLE_PROMOTE_ROLLBACK_AUTOMATION=True,
    ),
}


# ---------------------------------------------------------------------------
# Settings aggregator
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    STAGE: Stage
    flags: FeatureFlags


def _apply_overrides(base: FeatureFlags) -> FeatureFlags:
    """Apply per-flag environment-variable overrides on top of stage defaults."""
    data = {f.name: getattr(base, f.name) for f in dataclasses.fields(base)}
    for key in list(data.keys()):
        override = _get_env_bool(key)
        if override is not None:
            data[key] = override
    return FeatureFlags(**data)


@lru_cache(maxsize=None)
def get_settings() -> Settings:
    """
    Return the cached Settings object built from the current environment.

    Call ``get_settings.cache_clear()`` in tests to reset between cases.
    """
    stage = _get_env_stage()
    base = STAGE_DEFAULT_FLAGS[stage]
    flags = _apply_overrides(base)
    return Settings(STAGE=stage, flags=flags)
