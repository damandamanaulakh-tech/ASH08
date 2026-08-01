"""ASH08 local-first store with atomic files and explicit source/freshness metadata."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

LOG = logging.getLogger("ash08.supabase")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _snapshot_id(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:24]


class SupabaseStore:
    def __init__(self, url=None, key=None, data_dir="ash08_data"):
        self.url = (url or os.environ.get("SUPABASE_URL") or "").rstrip("/")
        self.key = key or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or ""
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.enabled = bool(self.url and self.key)
        self.last_error: Optional[str] = None

    def _headers(self):
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=representation",
        }

    def _request(self, method, path, body=None, query=""):
        if not self.enabled:
            raise RuntimeError("supabase_disabled_or_read_only")
        url = f"{self.url}/rest/v1/{path}"
        if query:
            url = f"{url}?{query}"
        data = None if body is None else json.dumps(body, default=str).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            error = exc.read().decode("utf-8", errors="replace")
            self.last_error = f"HTTP {exc.code}: {error[:300]}"
            raise

    def _write_json(self, name: str, obj: dict) -> None:
        path = self.data_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(obj, indent=2, sort_keys=True, default=str)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)

    def _read_json(self, name: str) -> Optional[dict]:
        path = self.data_dir / name
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _with_meta(self, payload: Optional[dict], source: str, status: str = "READY") -> Optional[dict]:
        if payload is None:
            return None
        result = dict(payload)
        result["meta"] = {
            "source": source,
            "status": status,
            "loaded_at": _utc_now_iso(),
            "last_error": self.last_error,
        }
        return result

    def save_universe(self, bucket: str, payload: dict):
        row = dict(payload)
        row.setdefault("asof", _utc_now_iso())
        row["bucket"] = bucket
        row["snapshot_id"] = row.get("snapshot_id") or _snapshot_id(row)
        self._write_json(f"universe_{bucket}.json", row)
        if self.enabled:
            try:
                self._request("POST", "universe_snapshots", row, query="on_conflict=snapshot_id")
            except Exception as exc:
                self.last_error = str(exc)[:300]
                LOG.warning("Supabase universe replication degraded: %s", exc)
        return row

    def load_universe(self, bucket: str):
        if self.enabled:
            try:
                data = self._request("GET", "universe_snapshots", query=f"bucket=eq.{bucket}&order=asof.desc&limit=1")
                if data:
                    return self._with_meta(data[0], "supabase")
            except Exception as exc:
                self.last_error = str(exc)[:300]
        local = self._read_json(f"universe_{bucket}.json")
        return self._with_meta(local, "local", "DEGRADED" if self.last_error else "READY")

    def save_scan(self, payload: dict):
        row = dict(payload)
        row.setdefault("asof", _utc_now_iso())
        row["snapshot_id"] = row.get("snapshot_id") or _snapshot_id(row)
        self._write_json("scan_latest.json", row)
        if self.enabled:
            try:
                self._request("POST", "scan_results", row, query="on_conflict=snapshot_id")
            except Exception as exc:
                self.last_error = str(exc)[:300]
                LOG.warning("Supabase scan replication degraded: %s", exc)
        return row

    def load_scan(self):
        if self.enabled:
            try:
                data = self._request("GET", "scan_results", query="order=asof.desc&limit=1")
                if data:
                    return self._with_meta(data[0], "supabase")
            except Exception as exc:
                self.last_error = str(exc)[:300]
        local = self._read_json("scan_latest.json")
        return self._with_meta(local, "local", "DEGRADED" if self.last_error else "READY")

    def save_paper_state(self, state: dict):
        self._write_json("paper_state.json", state)

    def health(self):
        return {
            "supabase_configured": self.enabled,
            "supabase_url_set": bool(self.url),
            "service_key_set": bool(self.key),
            "write_mode": "service_role" if self.enabled else "local_only",
            "last_error": self.last_error,
            "local_data_dir": str(self.data_dir),
            "local_core_exists": (self.data_dir / "universe_core.json").exists(),
            "local_scan_exists": (self.data_dir / "scan_latest.json").exists(),
            "local_paper_exists": (self.data_dir / "paper_state.json").exists(),
        }
