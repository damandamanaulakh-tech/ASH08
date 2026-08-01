"""Additive Chitty telemetry and governance registry.

This module implements only the 31 net-new items approved in the audit handoff.
It is deliberately isolated from scanner decisions, position sizing, governor
state, order eligibility, and exits.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA_VERSION = "chitty-net-new-v1"
DECISION_IMPACT = False

# The registry is intentionally data, not runtime configuration. Every entry is
# additive and namespaced; none shadows an existing ASH08 parameter.
ADOPTED_PARAMETERS: Tuple[Dict[str, str], ...] = (
    {"id": "CN-001", "name": "Source file SHA-256", "family": "Source governance", "scope": "governance"},
    {"id": "CN-002", "name": "Duplicate source mapping", "family": "Source governance", "scope": "governance"},
    {"id": "CN-003", "name": "ISIN and listing metadata extension", "family": "Instrument metadata", "scope": "metadata"},
    {"id": "CN-004", "name": "NIFTY 200 / NIFTY 500 membership tags", "family": "Universe metadata", "scope": "metadata"},
    {"id": "CN-005", "name": "History first observation date", "family": "Coverage metadata", "scope": "telemetry"},
    {"id": "CN-006", "name": "History last observation date", "family": "Coverage metadata", "scope": "telemetry"},
    {"id": "CN-007", "name": "History row count", "family": "Coverage metadata", "scope": "telemetry"},
    {"id": "CN-008", "name": "History coverage percentage", "family": "Coverage metadata", "scope": "telemetry"},
    {"id": "CN-009", "name": "Adjusted-data flag and adjustment version", "family": "Data integrity", "scope": "metadata"},
    {"id": "CN-010", "name": "Daily return percentage", "family": "Price feature", "scope": "telemetry"},
    {"id": "CN-011", "name": "Gap percentage", "family": "Price feature", "scope": "telemetry"},
    {"id": "CN-012", "name": "Range percentage", "family": "Price feature", "scope": "telemetry"},
    {"id": "CN-013", "name": "Body percentage", "family": "Price feature", "scope": "telemetry"},
    {"id": "CN-014", "name": "Upper-wick percentage", "family": "Price feature", "scope": "telemetry"},
    {"id": "CN-015", "name": "Lower-wick percentage", "family": "Price feature", "scope": "telemetry"},
    {"id": "CN-016", "name": "SMA20 / SMA50 / SMA200 position state", "family": "Trend feature", "scope": "telemetry"},
    {"id": "CN-017", "name": "EMA20 hold", "family": "Trend feature", "scope": "telemetry"},
    {"id": "CN-018", "name": "ROC20 percentage", "family": "Momentum feature", "scope": "telemetry"},
    {"id": "CN-019", "name": "RSI14 raw value", "family": "Momentum feature", "scope": "telemetry"},
    {"id": "CN-020", "name": "MACD raw values", "family": "Momentum feature", "scope": "telemetry"},
    {"id": "CN-021", "name": "Breakout 20-session percentage", "family": "Breakout feature", "scope": "telemetry"},
    {"id": "CN-022", "name": "Volume ratio 20-session", "family": "Participation feature", "scope": "telemetry"},
    {"id": "CN-023", "name": "ATR14 percentage", "family": "Volatility feature", "scope": "telemetry"},
    {"id": "CN-024", "name": "Sector concentration count", "family": "Portfolio context", "scope": "telemetry"},
    {"id": "CN-025", "name": "Relative strength versus Nifty", "family": "Relative-strength feature", "scope": "telemetry"},
    {"id": "CN-026", "name": "Paper-trade evidence record", "family": "Paper-trade audit", "scope": "audit"},
    {"id": "CN-027", "name": "Rule-followed flag and override reason", "family": "Human audit", "scope": "audit"},
    {"id": "CN-028", "name": "Emotion score", "family": "Human journal", "scope": "audit"},
    {"id": "CN-029", "name": "Synthetic promotion lock", "family": "Research governance", "scope": "audit"},
    {"id": "CN-030", "name": "Matched cases count", "family": "Research evidence", "scope": "audit"},
    {"id": "CN-031", "name": "Failed cases count", "family": "Research evidence", "scope": "audit"},
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def registry_payload() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "adopted_count": len(ADOPTED_PARAMETERS),
        "decision_impact": DECISION_IMPACT,
        "discussion_queue_included": 0,
        "parameters": [dict(item) for item in ADOPTED_PARAMETERS],
        "invariants": [
            "no existing parameter is changed or shadowed",
            "no discussion-queue item is implemented",
            "telemetry cannot change scanner, sizing, governor, orders, or exits",
            "missing evidence is represented as UNKNOWN, never fabricated",
        ],
    }


def _finite(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def _positive(value: Any, field: str) -> float:
    number = _finite(value, field)
    if number <= 0:
        raise ValueError(f"{field} must be positive")
    return number


def _parse_date(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("bar date is required")
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed = datetime.strptime(raw[:10], "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"invalid bar date: {raw}") from exc
    return parsed.date().isoformat()


def _unknown(reason: str) -> Dict[str, Any]:
    return {"status": "UNKNOWN", "value": None, "reason": reason}


def _available(value: Any, **extra: Any) -> Dict[str, Any]:
    payload = {"status": "AVAILABLE", "value": value}
    payload.update(extra)
    return payload


def _round(value: float, digits: int = 8) -> float:
    return round(float(value), digits)


def _normalize_bars(raw_bars: Any, label: str = "bars") -> List[Dict[str, Any]]:
    if not isinstance(raw_bars, list):
        raise ValueError(f"{label} must be a list")
    rows: Dict[str, Dict[str, Any]] = {}
    for index, raw in enumerate(raw_bars):
        if not isinstance(raw, dict):
            raise ValueError(f"{label}[{index}] must be an object")
        date = _parse_date(raw.get("date") or raw.get("asof") or raw.get("timestamp"))
        open_price = _positive(raw.get("open"), f"{label}[{index}].open")
        high = _positive(raw.get("high"), f"{label}[{index}].high")
        low = _positive(raw.get("low"), f"{label}[{index}].low")
        close = _positive(raw.get("close"), f"{label}[{index}].close")
        volume = _finite(raw.get("volume"), f"{label}[{index}].volume")
        if volume < 0:
            raise ValueError(f"{label}[{index}].volume must be non-negative")
        if high < max(open_price, close) or low > min(open_price, close) or high < low:
            raise ValueError(f"{label}[{index}] has inconsistent OHLC values")
        if date in rows:
            raise ValueError(f"duplicate {label} date: {date}")
        rows[date] = {
            "date": date,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    return [rows[key] for key in sorted(rows)]


def _sma(values: Sequence[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _ema_series(values: Sequence[float], period: int) -> List[Optional[float]]:
    output: List[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return output
    alpha = 2.0 / (period + 1.0)
    previous = sum(values[:period]) / period
    output[period - 1] = previous
    for index in range(period, len(values)):
        previous = alpha * values[index] + (1.0 - alpha) * previous
        output[index] = previous
    return output


def _wilder_rsi(values: Sequence[float], period: int = 14) -> Optional[float]:
    if len(values) < period + 1:
        return None
    deltas = [values[index] - values[index - 1] for index in range(1, len(values))]
    gains = [max(delta, 0.0) for delta in deltas]
    losses = [max(-delta, 0.0) for delta in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for index in range(period, len(deltas)):
        avg_gain = ((period - 1) * avg_gain + gains[index]) / period
        avg_loss = ((period - 1) * avg_loss + losses[index]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _macd(values: Sequence[float]) -> Optional[Dict[str, float]]:
    ema12 = _ema_series(values, 12)
    ema26 = _ema_series(values, 26)
    macd_values: List[float] = []
    macd_indexes: List[int] = []
    for index, (fast, slow) in enumerate(zip(ema12, ema26)):
        if fast is not None and slow is not None:
            macd_values.append(fast - slow)
            macd_indexes.append(index)
    signal_series = _ema_series(macd_values, 9)
    if not signal_series or signal_series[-1] is None or not macd_values:
        return None
    line = macd_values[-1]
    signal = float(signal_series[-1])
    return {"line": _round(line), "signal": _round(signal), "histogram": _round(line - signal)}


def _wilder_atr(bars: Sequence[Dict[str, Any]], period: int = 14) -> Optional[float]:
    if len(bars) < period:
        return None
    true_ranges: List[float] = []
    for index, bar in enumerate(bars):
        if index == 0:
            true_ranges.append(bar["high"] - bar["low"])
        else:
            prev_close = bars[index - 1]["close"]
            true_ranges.append(max(
                bar["high"] - bar["low"],
                abs(bar["high"] - prev_close),
                abs(bar["low"] - prev_close),
            ))
    atr = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr = ((period - 1) * atr + tr) / period
    return atr


def _relative_strength(stock_bars: Sequence[Dict[str, Any]], benchmark_bars: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    stock = {row["date"]: row["close"] for row in stock_bars}
    benchmark = {row["date"]: row["close"] for row in benchmark_bars}
    common = sorted(set(stock).intersection(benchmark))
    output: Dict[str, Any] = {}
    for label, horizon in (("1m", 21), ("3m", 63), ("6m", 126)):
        if len(common) < horizon + 1:
            output[label] = _unknown(f"requires {horizon + 1} aligned sessions")
            continue
        end_date = common[-1]
        start_date = common[-(horizon + 1)]
        stock_return = stock[end_date] / stock[start_date] - 1.0
        benchmark_return = benchmark[end_date] / benchmark[start_date] - 1.0
        output[label] = _available(_round(100.0 * (stock_return - benchmark_return)),
                                   start_date=start_date, end_date=end_date)
    return output


def compute_telemetry(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("telemetry payload must be an object")
    symbol = str(payload.get("symbol") or "").strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    bars = _normalize_bars(payload.get("bars"), "bars")
    if not bars:
        raise ValueError("bars must contain at least one valid observation")
    closes = [row["close"] for row in bars]
    latest = bars[-1]
    previous = bars[-2] if len(bars) >= 2 else None

    expected_sessions_raw = payload.get("expected_sessions")
    expected_sessions: Optional[int] = None
    if expected_sessions_raw not in (None, ""):
        try:
            expected_sessions = int(expected_sessions_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("expected_sessions must be an integer") from exc
        if expected_sessions <= 0:
            raise ValueError("expected_sessions must be positive")

    metadata_raw = payload.get("metadata") or {}
    if not isinstance(metadata_raw, dict):
        raise ValueError("metadata must be an object")
    metadata = {
        "instrument_key": str(metadata_raw.get("instrument_key") or ""),
        "isin": str(metadata_raw.get("isin") or ""),
        "exchange": str(metadata_raw.get("exchange") or ""),
        "series": str(metadata_raw.get("series") or ""),
        "listing_status": str(metadata_raw.get("listing_status") or ""),
        "active": metadata_raw.get("active") if isinstance(metadata_raw.get("active"), bool) else None,
        "tradable": metadata_raw.get("tradable") if isinstance(metadata_raw.get("tradable"), bool) else None,
        "nifty_200_member": metadata_raw.get("nifty_200_member") if isinstance(metadata_raw.get("nifty_200_member"), bool) else None,
        "nifty_500_member": metadata_raw.get("nifty_500_member") if isinstance(metadata_raw.get("nifty_500_member"), bool) else None,
        "membership_effective_date": str(metadata_raw.get("membership_effective_date") or ""),
        "sector": str(metadata_raw.get("sector") or ""),
        "adjusted_data": metadata_raw.get("adjusted_data") if isinstance(metadata_raw.get("adjusted_data"), bool) else None,
        "adjustment_version": str(metadata_raw.get("adjustment_version") or ""),
    }

    features: Dict[str, Any] = {
        "CN-005": _available(bars[0]["date"]),
        "CN-006": _available(bars[-1]["date"]),
        "CN-007": _available(len(bars)),
        "CN-008": _available(_round(100.0 * len(bars) / expected_sessions),
                              expected_sessions=expected_sessions) if expected_sessions else _unknown("expected_sessions not supplied"),
        "CN-009": _available({"adjusted_data": metadata["adjusted_data"], "adjustment_version": metadata["adjustment_version"]})
        if metadata["adjusted_data"] is not None and (not metadata["adjusted_data"] or metadata["adjustment_version"])
        else _unknown("adjusted_data flag and version evidence are incomplete"),
    }

    if previous:
        prev_close = previous["close"]
        features.update({
            "CN-010": _available(_round(100.0 * (latest["close"] / prev_close - 1.0))),
            "CN-011": _available(_round(100.0 * (latest["open"] / prev_close - 1.0))),
            "CN-012": _available(_round(100.0 * (latest["high"] - latest["low"]) / prev_close)),
            "CN-013": _available(_round(100.0 * (latest["close"] - latest["open"]) / prev_close)),
            "CN-014": _available(_round(100.0 * (latest["high"] - max(latest["open"], latest["close"])) / prev_close)),
            "CN-015": _available(_round(100.0 * (min(latest["open"], latest["close"]) - latest["low"]) / prev_close)),
        })
    else:
        for key in ("CN-010", "CN-011", "CN-012", "CN-013", "CN-014", "CN-015"):
            features[key] = _unknown("requires a previous completed session")

    sma20, sma50, sma200 = _sma(closes, 20), _sma(closes, 50), _sma(closes, 200)
    if sma20 is None or sma50 is None or sma200 is None:
        features["CN-016"] = _unknown("requires 200 completed sessions")
    else:
        if latest["close"] > sma20 > sma50 > sma200:
            state = "UPTREND"
        elif latest["close"] < sma20 < sma50 < sma200:
            state = "DOWNTREND"
        else:
            state = "MIXED"
        features["CN-016"] = _available({"state": state, "sma20": _round(sma20), "sma50": _round(sma50), "sma200": _round(sma200)})

    ema20_series = _ema_series(closes, 20)
    ema20 = ema20_series[-1] if ema20_series else None
    features["CN-017"] = _available({"ema20": _round(float(ema20)), "hold": latest["close"] >= float(ema20)}) \
        if ema20 is not None else _unknown("requires 20 completed sessions")

    features["CN-018"] = _available(_round(100.0 * (closes[-1] / closes[-21] - 1.0))) \
        if len(closes) >= 21 else _unknown("requires 21 completed sessions")

    rsi = _wilder_rsi(closes, 14)
    features["CN-019"] = _available(_round(rsi)) if rsi is not None else _unknown("requires 15 completed sessions")

    macd = _macd(closes)
    features["CN-020"] = _available(macd) if macd is not None else _unknown("requires sufficient EMA26 and signal history")

    if len(bars) >= 21:
        prior_high = max(row["high"] for row in bars[-21:-1])
        features["CN-021"] = _available(_round(100.0 * (latest["close"] / prior_high - 1.0)), reference_high=_round(prior_high))
        avg_volume = sum(row["volume"] for row in bars[-21:-1]) / 20.0
        features["CN-022"] = _available(_round(latest["volume"] / avg_volume)) if avg_volume > 0 else _unknown("prior 20-session mean volume is zero")
    else:
        features["CN-021"] = _unknown("requires current plus 20 prior completed sessions")
        features["CN-022"] = _unknown("requires current plus 20 prior completed sessions")

    atr = _wilder_atr(bars, 14)
    features["CN-023"] = _available(_round(100.0 * atr / latest["close"]), atr14=_round(atr)) \
        if atr is not None else _unknown("requires 14 completed sessions")

    open_positions = payload.get("open_positions") or []
    if not isinstance(open_positions, list):
        raise ValueError("open_positions must be a list")
    sector = metadata["sector"]
    if not sector:
        features["CN-024"] = _unknown("sector metadata not supplied")
    else:
        count = sum(1 for item in open_positions if isinstance(item, dict) and str(item.get("sector") or "") == sector)
        features["CN-024"] = _available(count, sector=sector)

    benchmark_raw = payload.get("benchmark_bars")
    if benchmark_raw in (None, []):
        features["CN-025"] = _unknown("benchmark_bars not supplied")
    else:
        benchmark_bars = _normalize_bars(benchmark_raw, "benchmark_bars")
        features["CN-025"] = _available(_relative_strength(bars, benchmark_bars))

    return {
        "schema_version": SCHEMA_VERSION,
        "decision_impact": DECISION_IMPACT,
        "symbol": symbol,
        "computed_at": utc_now(),
        "asof": latest["date"],
        "metadata": metadata,
        "features": features,
    }


class ChittyAdoptedStore:
    """Durable additive storage isolated from the paper-engine state."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.source_path = self.root / "source_manifest.json"
        self.telemetry_path = self.root / "telemetry_latest.json"
        self.events_path = self.root / "audit_events.jsonl"

    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def _atomic_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)

    def register_source(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        name = str(payload.get("source_name") or "").strip()
        content = payload.get("content")
        if not name:
            raise ValueError("source_name is required")
        if not isinstance(content, str):
            raise ValueError("content must be a UTF-8 string")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        manifest = self._read_json(self.source_path, {"schema_version": SCHEMA_VERSION, "sources": []})
        sources = list(manifest.get("sources") or [])
        canonical = next((item for item in sources if item.get("sha256") == digest), None)
        existing = next((item for item in sources if item.get("source_name") == name), None)
        record = {
            "source_name": name,
            "sha256": digest,
            "registered_at": utc_now(),
            "duplicate_of_hash": digest if canonical and canonical.get("source_name") != name else "",
            "canonical_source_name": canonical.get("source_name") if canonical else name,
        }
        if existing:
            sources = [item for item in sources if item.get("source_name") != name]
        sources.append(record)
        manifest = {"schema_version": SCHEMA_VERSION, "sources": sorted(sources, key=lambda item: item.get("source_name", ""))}
        self._atomic_json(self.source_path, manifest)
        return record

    def compute_and_save(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        snapshot = compute_telemetry(payload)
        data = self._read_json(self.telemetry_path, {"schema_version": SCHEMA_VERSION, "symbols": {}})
        symbols = dict(data.get("symbols") or {})
        symbols[snapshot["symbol"]] = snapshot
        self._atomic_json(self.telemetry_path, {"schema_version": SCHEMA_VERSION, "symbols": symbols})
        return snapshot

    def _append_event(self, event: Dict[str, Any]) -> None:
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, sort_keys=True, allow_nan=False)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def record_audit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        event_type = str(payload.get("event_type") or "").strip()
        allowed = {"paper_trade_evidence", "rule_followed", "emotion", "research_evidence"}
        if event_type not in allowed:
            raise ValueError(f"event_type must be one of {sorted(allowed)}")
        body = dict(payload)
        body.pop("event_id", None)
        body.pop("recorded_at", None)

        if event_type == "paper_trade_evidence":
            required = ("trade_id", "symbol", "instrument_key", "entry_time", "entry_price", "quantity", "parameter_set_id", "quote_timestamp")
            missing = [key for key in required if body.get(key) in (None, "")]
            if missing:
                raise ValueError(f"paper trade evidence missing: {', '.join(missing)}")
            body["entry_price"] = _positive(body["entry_price"], "entry_price")
            body["quantity"] = int(_positive(body["quantity"], "quantity"))
            for optional_number in ("exit_price", "gross_pnl", "net_pnl", "costs"):
                if body.get(optional_number) not in (None, ""):
                    body[optional_number] = _finite(body[optional_number], optional_number)

        elif event_type == "rule_followed":
            if not isinstance(body.get("rule_followed"), bool):
                raise ValueError("rule_followed must be boolean")
            if body["rule_followed"] is False:
                if not str(body.get("actor") or "").strip() or not str(body.get("reason") or "").strip():
                    raise ValueError("actor and reason are required when rule_followed is false")

        elif event_type == "emotion":
            try:
                score = int(body.get("emotion_score"))
            except (TypeError, ValueError) as exc:
                raise ValueError("emotion_score must be an integer from 1 to 5") from exc
            if score < 1 or score > 5:
                raise ValueError("emotion_score must be an integer from 1 to 5")
            body["emotion_score"] = score
            body["decision_impact"] = False

        elif event_type == "research_evidence":
            matched = int(body.get("matched_cases", 0))
            failed = int(body.get("failed_cases", 0))
            if matched < 0 or failed < 0 or failed > matched:
                raise ValueError("research counts must satisfy 0 <= failed_cases <= matched_cases")
            body["matched_cases"] = matched
            body["failed_cases"] = failed
            source_class = str(body.get("source_class") or "").strip().lower()
            production_enabled = body.get("production_enabled") is True
            independently_validated = body.get("independent_real_validation") is True
            manually_approved = body.get("manual_approval") is True
            if source_class in {"synthetic", "ai", "ai-generated"} and production_enabled and not (independently_validated and manually_approved):
                raise ValueError("synthetic promotion lock: independent real validation and manual approval are required")
            body["promotion_locked"] = source_class in {"synthetic", "ai", "ai-generated"} and not (independently_validated and manually_approved)

        event = {"event_id": str(uuid.uuid4()), "recorded_at": utc_now(), **body}
        self._append_event(event)
        return event

    def recent_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        if not self.events_path.exists():
            return []
        lines = self.events_path.read_text(encoding="utf-8").splitlines()[-max(1, min(limit, 500)):]
        output = []
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                output.append(item)
        return output

    def status(self) -> Dict[str, Any]:
        registry = registry_payload()
        sources = self._read_json(self.source_path, {"schema_version": SCHEMA_VERSION, "sources": []})
        telemetry = self._read_json(self.telemetry_path, {"schema_version": SCHEMA_VERSION, "symbols": {}})
        return {
            "ok": True,
            **registry,
            "sources": list(sources.get("sources") or []),
            "telemetry": list((telemetry.get("symbols") or {}).values()),
            "recent_events": self.recent_events(),
        }
