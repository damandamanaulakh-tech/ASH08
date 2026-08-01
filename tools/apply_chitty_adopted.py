from pathlib import Path
import base64
import json
import shutil
import zlib


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


payload_dir = Path("tools/chitty_payload")
payload = "".join(path.read_text(encoding="utf-8") for path in sorted(payload_dir.glob("*.txt")))
files = json.loads(zlib.decompress(base64.b64decode(payload)).decode("utf-8"))

for path, content in files.items():
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

api_path = Path("api.py")
api = api_path.read_text(encoding="utf-8")
api = replace_once(
    api,
    "from ash08.paper_engine import PaperEngine\n",
    "from ash08.chitty_adopted import ChittyAdoptedStore\nfrom ash08.paper_engine import PaperEngine\n",
    "api import",
)
api = replace_once(
    api,
    "_ENGINE = PaperEngine(data_dir=str(DATA_DIR), book_value=BOOK_VALUE)\n_RATE_LOCK = threading.Lock()\n",
    "_ENGINE = PaperEngine(data_dir=str(DATA_DIR), book_value=BOOK_VALUE)\n_CHITTY = ChittyAdoptedStore(DATA_DIR / \"chitty_adopted\")\n_RATE_LOCK = threading.Lock()\n",
    "api store initialization",
)
api = replace_once(
    api,
    "            if path == \"/api/config\":\n                return self._send_json(200, {\"ok\": True, \"config\": public_config()})\n",
    "            if path == \"/api/config\":\n                return self._send_json(200, {\"ok\": True, \"config\": public_config()})\n            if path == \"/api/chitty/adopted\":\n                return self._send_json(200, _CHITTY.status())\n",
    "api GET route",
)
api = replace_once(
    api,
    "            body = self._read_json()\n            if path in {\"/api/pnl/tick\", \"/api/live/refresh\"}:\n",
    "            body = self._read_json()\n            if path == \"/api/chitty/source/register\":\n                try:\n                    source = _CHITTY.register_source(body)\n                except ValueError as exc:\n                    raise ApiError(422, \"CHITTY_SOURCE_INVALID\", str(exc)) from exc\n                return self._send_json(200, {\"ok\": True, \"source\": source, \"decision_impact\": False})\n            if path == \"/api/chitty/telemetry/compute\":\n                try:\n                    snapshot = _CHITTY.compute_and_save(body)\n                except ValueError as exc:\n                    raise ApiError(422, \"CHITTY_TELEMETRY_INVALID\", str(exc)) from exc\n                return self._send_json(200, {\"ok\": True, \"snapshot\": snapshot, \"decision_impact\": False})\n            if path == \"/api/chitty/audit/record\":\n                try:\n                    event = _CHITTY.record_audit(body)\n                except ValueError as exc:\n                    raise ApiError(422, \"CHITTY_AUDIT_INVALID\", str(exc)) from exc\n                return self._send_json(200, {\"ok\": True, \"event\": event, \"decision_impact\": False})\n            if path in {\"/api/pnl/tick\", \"/api/live/refresh\"}:\n",
    "api POST routes",
)
api_path.write_text(api, encoding="utf-8")

dashboard_path = Path("desk/ASH08_Desk_Dashboard.html")
dashboard = dashboard_path.read_text(encoding="utf-8")
dashboard = replace_once(
    dashboard,
    "        <button id=\"refresh-pnl\">Fetch live marks</button>\n",
    "        <button id=\"refresh-pnl\">Fetch live marks</button>\n        <a href=\"/chitty-adopted.html\">Open adopted Chitty telemetry</a>\n",
    "dashboard link",
)
dashboard_path.write_text(dashboard, encoding="utf-8")

shutil.rmtree(payload_dir)
Path("tools/apply_chitty_adopted.py").unlink()
Path(".github/workflows/apply-chitty-adopted.yml").unlink()
