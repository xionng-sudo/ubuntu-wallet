#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rollback_model.py
=================
Roll back the production model to the most recent archived version.

Registry layout (all paths relative to --model-dir, default: <repo_root>/models):
  models/
    registry.json                # model version history
    current/                     # production model pointer directory (ml-service loads from here)
      model_meta.json
      lightgbm_event_v3.pkl
      ...
    archive/                     # versioned backups
      <version>/
        model_meta.json
        lightgbm_event_v3.pkl
        ...

Steps performed by this script:
  1. Read models/registry.json
  2. Find the most recent "archived" entry (the one just before current prod)
  3. Replace models/current/ with a fresh copy from archive/<version>/
  4. Update registry.json: set that entry to "prod", set old prod to "archived"

Usage
-----
  # Preview which version would be restored (dry run):
  python scripts/rollback_model.py --model-dir models --dry-run

  # Perform the rollback:
  python scripts/rollback_model.py --model-dir models
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _load_registry(model_dir: str) -> Dict[str, Any]:
    path = os.path.join(model_dir, "registry.json")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"registry.json not found at {path}. "
            "Train at least one model to create the registry."
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_registry(model_dir: str, registry: Dict[str, Any]) -> None:
    registry["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    path = os.path.join(model_dir, "registry.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)


def _promote_to_current(model_dir: str, archive_abs: str, dry_run: bool) -> None:
    """
    Replace models/current/ with a copy of the given archive directory.

    This is the only write-path that changes what ml-service loads at runtime.
    """
    current_dir = os.path.join(model_dir, "current")
    if not dry_run:
        if os.path.isdir(current_dir):
            shutil.rmtree(current_dir)
        shutil.copytree(archive_abs, current_dir)
        print(f"Replaced {current_dir} with {archive_abs}", flush=True)
    else:
        print(f"[DRY-RUN] Would replace {current_dir} with {archive_abs}", flush=True)


def _find_current_prod(entries: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    prods = [e for e in entries if e.get("status") == "prod"]
    if not prods:
        return None
    # Most recently trained prod entry
    return sorted(prods, key=lambda e: e.get("trained_at", ""), reverse=True)[0]


def _find_rollback_target(entries: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the most recent archived entry (candidate for rollback)."""
    archived = [e for e in entries if e.get("status") == "archived"]
    if not archived:
        return None
    return sorted(archived, key=lambda e: e.get("trained_at", ""), reverse=True)[0]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Roll back production model to the most recent archived version"
    )
    ap.add_argument(
        "--model-dir",
        default=os.path.join(REPO_ROOT, "models"),
        help="Model directory (default: <repo_root>/models)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without making any changes",
    )
    args = ap.parse_args()

    model_dir = os.path.abspath(args.model_dir)
    dry = args.dry_run
    prefix = "[DRY-RUN] " if dry else ""

    print(f"{prefix}model_dir = {model_dir}", flush=True)

    # Load registry
    try:
        registry = _load_registry(model_dir)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", flush=True)
        return 2

    entries: List[Dict[str, Any]] = registry.get("entries", [])
    if not entries:
        print("ERROR: registry has no entries. Nothing to roll back.", flush=True)
        return 2

    current = _find_current_prod(entries)
    if current is None:
        print("WARNING: no entry with status=prod found in registry.", flush=True)

    target = _find_rollback_target(entries)
    if target is None:
        print("ERROR: no archived entry found in registry. Nothing to roll back to.", flush=True)
        return 2

    print(f"\nCurrent production model:")
    print(f"  model_version = {current.get('model_version') if current else 'none'}")
    print(f"  trained_at    = {current.get('trained_at') if current else 'none'}")
    print(f"\nTarget rollback model (most recent archived):")
    print(f"  model_version = {target.get('model_version')}")
    print(f"  trained_at    = {target.get('trained_at')}")

    archive_rel = target.get("archive_dir", "")
    if not archive_rel:
        print("ERROR: target entry has no archive_dir. Cannot restore files.", flush=True)
        return 2

    archive_abs = os.path.realpath(os.path.join(model_dir, archive_rel))

    # Guard against path traversal: the resolved archive directory must be
    # strictly inside model_dir to prevent restoring from an unexpected location
    # if registry.json were corrupted or tampered with.
    model_dir_real = os.path.realpath(model_dir)
    if not (archive_abs == model_dir_real or archive_abs.startswith(model_dir_real + os.sep)):
        print(
            f"ERROR: archive_dir '{archive_rel}' resolves outside model_dir '{model_dir}'. "
            "Aborting to prevent path traversal.",
            flush=True,
        )
        return 2

    print(f"\n{prefix}Restoring models/current/ from: {archive_abs}")

    if not dry and not os.path.isdir(archive_abs):
        print(f"ERROR: archive directory does not exist: {archive_abs}", flush=True)
        return 2

    # Update registry statuses
    for e in entries:
        if current and e.get("model_version") == current.get("model_version"):
            if not dry:
                e["status"] = "archived"
            print(
                f"\n{prefix}Marking current prod as archived: {e.get('model_version')}", flush=True
            )
        if e.get("model_version") == target.get("model_version"):
            if not dry:
                e["status"] = "prod"
            print(f"{prefix}Marking rollback target as prod : {e.get('model_version')}", flush=True)

    if not dry:
        _save_registry(model_dir, registry)
        _promote_to_current(model_dir, archive_abs, dry_run=False)
        print("\nRegistry updated.", flush=True)
        print(f"Rollback complete. Active model is now: {target.get('model_version')}", flush=True)
        print(
            "Restart ml-service to load the restored model:\n"
            "  sudo systemctl restart ml-service",
            flush=True,
        )
    else:
        _promote_to_current(model_dir, archive_abs, dry_run=True)
        print(
            f"\n[DRY-RUN] Rollback would succeed. "
            f"Run without --dry-run to apply.",
            flush=True,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
