"""ASH08 store - local JSON always; Supabase best-effort (no hard fail on 403)."""
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
        self.last_error: Optional[str] = None

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
        data = None if body is None else json.dumps(body, default=str).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            self.last_error = f"HTTP {e.code}: {err[:300]}"
            LOG.error("Supabase %s %s: %s", e.code, path, err[:300])
            raise

    def _write_json(self, name: str, obj: dict) -> None:
        (self.data_dir / name).write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")

    def _read_json(self, name: str) -> Optional[dict]:
        p = self.data_dir / name
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

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
        self._write_json(f"universe_{bucket}.json", row)
        if self.enabled:
            try:
                self._request("POST", "universe_snapshots", row)
            except Exception as e:
                LOG.warning("save_universe supabase skipped: %s", e)

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
                LOG.warning("load_universe supabase: %s", e)
        return self._read_json(f"universe_{bucket}.json")

    def save_scan(self, payload):
        row = {
            "asof": payload.get("asof") or _utc_now_iso(),
            "universe_bucket": payload.get("universe_bucket") or payload.get("bucket"),
            "select_count": int(payload.get("select_count") or payload.get("select") or 0),
            "watch_count": int(payload.get("watch_count") or payload.get("watch") or 0),
            "reject_count": int(payload.get("reject_count") or payload.get("reject") or 0),
            "rows": payload.get("rows") or [],
        }
        self._write_json("scan_latest.json", row)
        if self.enabled:
            try:
                self._request("POST", "scan_results", row)
            except Exception as e:
                LOG.warning("save_scan supabase skipped: %s", e)

    def load_scan(self):
        if self.enabled:
            try:
                data = self._request("GET", "scan_results", query="order=asof.desc&limit=1")
                if data:
                    return data[0]
            except Exception as e:
                LOG.warning("load_scan supabase: %s", e)
        return self._read_json("scan_latest.json")

    def save_paper_state(self, state):
        self._write_json("paper_state.json", state)
        if not self.enabled:
            return
        try:
            gov = state.get("governor") or {}
            try:
                self._request("POST", "governor_state", {
                    "id": 1,
                    "level": gov.get("level"),
                    "exposure_pct": gov.get("exposure_pct"),
                    "rationale": gov.get("rationale"),
                    "updated_at": _utc_now_iso(),
                })
            except Exception:
                try:
                    self._request("PATCH", "governor_state", {
                        "level": gov.get("level"),
                        "exposure_pct": gov.get("exposure_pct"),
                        "rationale": gov.get("rationale"),
                        "updated_at": _utc_now_iso(),
                    }, query="id=eq.1")
                except Exception:
                    pass
        except Exception as e:
            LOG.warning("save_paper supabase skipped: %s", e)

    def health(self):
        out = {
            "supabase_configured": self.enabled,
            "supabase_url_set": bool(self.url),
            "service_key_set": bool(os.environ.get("SUPABASE_SERVICE_ROLE_KEY")),
            "anon_key_set": bool(os.environ.get("SUPABASE_ANON_KEY")),
            "database_url_set": bool(os.environ.get("DATABASE_URL")),
            "upstox_token_set": bool(os.environ.get("UPSTOX_ACCESS_TOKEN")),
            "upstox_key_set": bool(os.environ.get("UPSTOX_API_KEY")),
            "local_data_dir": str(self.data_dir),
            "local_core_exists": (self.data_dir / "universe_core.json").exists(),
            "local_scan_exists": (self.data_dir / "scan_latest.json").exists(),
        }
        if self.enabled:
            try:
                self._request("GET", "governor_state", query="select=id&limit=1")
                out["supabase_reachable"] = True
                out["supabase_error"] = None
            except Exception as e:
                out["supabase_reachable"] = False
                out["supabase_error"] = str(e)[:200]
                if self.last_error:
                    out["supabase_error"] = self.last_error[:200]
        else:
            out["supabase_reachable"] = False
        return out
