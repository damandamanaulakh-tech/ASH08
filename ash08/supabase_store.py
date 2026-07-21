"""ASH08 Supabase store — REST via stdlib. Falls back to local JSON."""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

LOG = logging.getLogger("ash08.supabase")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SupabaseStore:
    def __init__(self, url=None, key=None, data_dir="ash08_data"):
        self.url = (url or os.environ.get("SUPABASE_URL") or "").rstrip("/")
        self.key = (
            key
            or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
            or os.environ.get("SUPABASE_ANON_KEY")
            or ""
        )
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.enabled = bool(self.url and self.key)
        if self.enabled:
            LOG.info("Supabase store enabled")
        else:
            LOG.warning("Supabase env missing — local JSON only")

    def _headers(self):
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    def _request(self, method, path, body=None, query=""):
        if not self.enabled:
            raise RuntimeError("supabase_disabled")
        url = f"{self.url}/rest/v1/{path}"
        if query:
            url = f"{url}?{query}"
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            LOG.error("Supabase HTTP %s %s: %s", e.code, path, err[:500])
            raise

    def save_universe(self, bucket, payload):
        row = {
            "bucket": bucket,
            "asof": payload.get("asof") or _utc_now_iso(),
            "source": payload.get("source"),
            "count": int(payload.get("count") or 0),
            "symbols": payload.get("symbols") or [],
            "rows": payload.get("rows") or [],
            "notes": payload.get("notes") or [],
        }
        (self.data_dir / f"universe_{bucket}.json").write_text(json.dumps(row, indent=2))
        if self.enabled:
            self._request("POST", "universe_snapshots", row)

    def load_universe(self, bucket):
        if self.enabled:
            try:
                data = self._request(
                    "GET", "universe_snapshots",
                    query=f"bucket=eq.{bucket}&order=asof.desc&limit=1",
                )
                if data:
                    return data[0]
            except Exception as e:
                LOG.warning("load_universe: %s", e)
        path = self.data_dir / f"universe_{bucket}.json"
        if path.exists():
            return json.loads(path.read_text())
        return None

    def save_scan(self, payload):
        row = {
            "asof": payload.get("asof") or _utc_now_iso(),
            "universe_bucket": payload.get("universe_bucket") or payload.get("bucket"),
            "select_count": int(payload.get("select_count") or payload.get("select") or 0),
            "watch_count": int(payload.get("watch_count") or payload.get("watch") or 0),
            "reject_count": int(payload.get("reject_count") or payload.get("reject") or 0),
            "rows": payload.get("rows") or [],
        }
        (self.data_dir / "scan_latest.json").write_text(json.dumps(row, indent=2))
        if self.enabled:
            self._request("POST", "scan_results", row)

    def load_scan(self):
        if self.enabled:
            try:
                data = self._request("GET", "scan_results", query="order=asof.desc&limit=1")
                if data:
                    return data[0]
            except Exception as e:
                LOG.warning("load_scan: %s", e)
        path = self.data_dir / "scan_latest.json"
        if path.exists():
            return json.loads(path.read_text())
        return None

    def save_paper_state(self, state):
        (self.data_dir / "paper_state.json").write_text(json.dumps(state, indent=2))
        if not self.enabled:
            return
        gov = state.get("governor") or {}
        try:
            self._request(
                "POST", "governor_state",
                {
                    "id": 1,
                    "level": gov.get("level"),
                    "exposure_pct": gov.get("exposure_pct"),
                    "rationale": gov.get("rationale"),
                    "updated_at": _utc_now_iso(),
                },
            )
        except Exception:
            try:
                self._request(
                    "PATCH", "governor_state",
                    {
                        "level": gov.get("level"),
                        "exposure_pct": gov.get("exposure_pct"),
                        "rationale": gov.get("rationale"),
                        "updated_at": _utc_now_iso(),
                    },
                    query="id=eq.1",
                )
            except Exception as e:
                LOG.warning("governor_state: %s", e)
        for o in state.get("orders") or []:
            try:
                self._request("POST", "paper_orders", {
                    "order_id": o.get("order_id"),
                    "symbol": o.get("symbol"),
                    "side": o.get("side"),
                    "order_type": o.get("order_type"),
                    "qty": o.get("qty"),
                    "sized_qty": o.get("sized_qty"),
                    "fill_price": o.get("fill_price"),
                    "stop": o.get("stop"),
                    "target": o.get("target"),
                    "status": o.get("status"),
                    "governor": o.get("governor") or gov,
                    "created_at": o.get("created_at") or _utc_now_iso(),
                })
            except Exception:
                pass
        for p in state.get("positions") or []:
            try:
                self._request("POST", "paper_positions", {
                    "position_id": p.get("position_id"),
                    "symbol": p.get("symbol"),
                    "qty": p.get("qty"),
                    "side": p.get("side"),
                    "entry": p.get("entry"),
                    "stop": p.get("stop"),
                    "target": p.get("target"),
                    "status": p.get("status"),
                    "ltp": p.get("ltp"),
                    "exit_price": p.get("exit_price"),
                    "exit_reason": p.get("exit_reason"),
                    "opened_at": p.get("opened_at"),
                    "closed_at": p.get("closed_at"),
                })
            except Exception:
                pass

    def health(self):
        out = {
            "supabase_configured": self.enabled,
            "supabase_url_set": bool(self.url),
            "service_key_set": bool(os.environ.get("SUPABASE_SERVICE_ROLE_KEY")),
            "anon_key_set": bool(os.environ.get("SUPABASE_ANON_KEY")),
            "database_url_set": bool(os.environ.get("DATABASE_URL")),
            "upstox_token_set": bool(os.environ.get("UPSTOX_ACCESS_TOKEN")),
            "upstox_key_set": bool(os.environ.get("UPSTOX_API_KEY")),
        }
        if self.enabled:
            try:
                self._request("GET", "governor_state", query="select=id&limit=1")
                out["supabase_reachable"] = True
            except Exception as e:
                out["supabase_reachable"] = False
                out["supabase_error"] = str(e)[:200]
        else:
            out["supabase_reachable"] = False
        return out
