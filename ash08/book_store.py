"""Durable paper book. Render free disk dies on sleep — this is the book.

Load order for the live desk (data_dir named ash08_data):
  1. runtime ash08_data/paper_state.json
  2. packaged ash08/data/paper_book.json (shipped in git)
  3. GitHub raw main (public repo)

Save always writes runtime JSON. Packaged + GitHub only for the live desk.
Tests using a temp dir never touch the packaged file or GitHub.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

LOG = logging.getLogger("ash08.book")

PACKAGED = Path(__file__).resolve().parent / "data" / "paper_book.json"
GITHUB_RAW = (
    "https://raw.githubusercontent.com/damandamanaulakh-tech/ASH08/main/"
    "ash08/data/paper_book.json"
)
GITHUB_API = (
    "https://api.github.com/repos/damandamanaulakh-tech/ASH08/contents/"
    "ash08/data/paper_book.json"
)
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        st = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        LOG.warning("book read %s: %s", path, e)
        return None
    return st if isinstance(st, dict) else None


def _has_book(st: Optional[dict]) -> bool:
    if not st:
        return False
    if st.get("positions") or st.get("orders"):
        return True
    return False


def _is_live_dir(data_dir: str | Path) -> bool:
    return Path(data_dir).name == "ash08_data"


def dump_state(engine) -> Dict[str, Any]:
    gov = engine.governor.to_dict() if hasattr(engine.governor, "to_dict") else {
        "level": getattr(engine.governor, "level", "L0_NORMAL"),
        "exposure_pct": getattr(engine.governor, "exposure_pct", 100),
        "rationale": getattr(engine.governor, "rationale", ""),
    }
    return {
        "saved_at": _utc(),
        "governor": gov,
        "orders": list(engine.orders),
        "positions": list(engine.positions),
        "pending_orders": list(getattr(engine, "pending_orders", []) or []),
        "journal": list(getattr(engine, "journal", []) or [])[-300:],
        "shadow": dict(getattr(engine, "shadow", None) or {}),
        "equity_history": list(getattr(engine, "equity_history", None) or [])[-500:],
        "peak_equity": float(getattr(engine, "peak_equity", 0) or 0),
        "clock_last": dict(getattr(engine, "clock_last", None) or {}),
        "cash": round(float(engine.cash), 2),
        "book_value": float(engine.book_value),
    }


def apply_state(engine, st: dict) -> None:
    engine.orders = st.get("orders") or []
    engine.positions = st.get("positions") or []
    engine.pending_orders = list(st.get("pending_orders") or [])
    engine.journal = list(st.get("journal") or [])[-300:]
    engine.shadow = dict(st.get("shadow") or {})
    engine.equity_history = list(st.get("equity_history") or [])
    engine.clock_last = dict(st.get("clock_last") or {})
    try:
        engine.peak_equity = float(st.get("peak_equity") or engine.book_value)
    except Exception:
        engine.peak_equity = float(engine.book_value)
    if st.get("cash") is not None:
        try:
            engine.cash = float(st["cash"])
        except Exception:
            pass
    g = st.get("governor") or {}
    if g.get("level") and hasattr(engine, "governor"):
        try:
            from ash08.paper_engine import GovState
            engine.governor = GovState(
                str(g.get("level")),
                float(g.get("exposure_pct") or engine.governor.exposure_pct),
                str(g.get("rationale") or ""),
            )
        except Exception:
            engine.governor.level = g.get("level", engine.governor.level)
            engine.governor.exposure_pct = float(
                g.get("exposure_pct", engine.governor.exposure_pct)
            )


def load(data_dir: str | Path = "ash08_data") -> Tuple[Optional[dict], str]:
    root = Path(data_dir)
    local = _read(root / "paper_state.json")
    if _has_book(local):
        return local, "local"
    if not _is_live_dir(root):
        return None, "empty"
    packaged = _read(PACKAGED)
    if _has_book(packaged):
        return packaged, "packaged"
    raw = _fetch_github_raw()
    if _has_book(raw):
        return raw, "github"
    return None, "empty"


def save(state: dict, data_dir: str | Path = "ash08_data") -> Dict[str, Any]:
    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    out = {"local": False, "packaged": False, "github": False}
    blob = json.dumps(state, indent=2, default=str)
    (root / "paper_state.json").write_text(blob, encoding="utf-8")
    out["local"] = True
    if _is_live_dir(root):
        try:
            PACKAGED.parent.mkdir(parents=True, exist_ok=True)
            PACKAGED.write_text(blob, encoding="utf-8")
            out["packaged"] = True
        except Exception as e:
            LOG.warning("packaged save: %s", e)
        out["github"] = _github_put(blob)
        _supabase_put(state)
    return out


def _fetch_github_raw() -> Optional[dict]:
    req = urllib.request.Request(
        GITHUB_RAW,
        headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        LOG.warning("github raw book: %s", e)
        return None


def _github_put(blob: str) -> bool:
    tok = (
        os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
        or os.environ.get("ASH08_GITHUB_TOKEN")
        or ""
    ).strip()
    if not tok:
        return False
    sha = None
    try:
        req = urllib.request.Request(
            GITHUB_API,
            headers={
                "User-Agent": BROWSER_UA,
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {tok}",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            meta = json.loads(resp.read().decode("utf-8"))
            sha = meta.get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            LOG.warning("github get book: %s", e.code)
            return False
    except Exception as e:
        LOG.warning("github get book: %s", e)
        return False
    body = {
        "message": f"paper book { _utc() }",
        "content": base64.b64encode(blob.encode("utf-8")).decode("ascii"),
        "branch": "main",
    }
    if sha:
        body["sha"] = sha
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        GITHUB_API,
        data=data,
        method="PUT",
        headers={
            "User-Agent": BROWSER_UA,
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
        return True
    except Exception as e:
        LOG.warning("github put book: %s", e)
        return False


def _supabase_put(state: dict) -> None:
    try:
        from ash08.supabase_store import SupabaseStore

        store = SupabaseStore()
        if not store.enabled:
            return
        row = {
            "id": 1,
            "saved_at": state.get("saved_at") or _utc(),
            "cash": state.get("cash"),
            "payload": state,
        }
        try:
            store._request("POST", "paper_book", row)
        except Exception:
            store._request("PATCH", "paper_book", row, query="id=eq.1")
    except Exception as e:
        LOG.warning("supabase paper_book: %s", e)
