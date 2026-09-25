"""Single point of truth for reading/writing JSON records.

Everything is flat files for now. Keeping all the I/O behind these
functions means if this ever moves to a real DB, only this file changes.
"""
import json
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
RECORDINGS_DIR = BASE_DIR / "storage" / "recordings"
EDITED_RECORDINGS_DIR = RECORDINGS_DIR / "edited"
TRIMMED_RECORDINGS_DIR = RECORDINGS_DIR / "trimmed"
EXECUTIONS_DIR = BASE_DIR / "storage" / "executions"
SEARCHES_DIR = BASE_DIR / "storage" / "searches"

for d in (RECORDINGS_DIR, EDITED_RECORDINGS_DIR, TRIMMED_RECORDINGS_DIR, EXECUTIONS_DIR, SEARCHES_DIR):
    d.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False, default=str)
    os.replace(tmp_path, path)


def _read_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_recording(test_case: dict) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = test_case.get("name") or f"session_{ts}"
    path = RECORDINGS_DIR / f"{name}.json"
    test_case["name"] = name
    test_case["saved_at"] = datetime.now().isoformat()
    _write_json(path, test_case)
    logger.info("saved recording to %s", path)
    return path


def save_edited_recording(test_case: dict) -> Path:
    """Same as save_recording(), but writes into storage/recordings/edited/
    so a saved edit never overwrites, or even sits alongside, the original
    recording it was derived from."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = test_case.get("name") or f"session_{ts}"
    path = EDITED_RECORDINGS_DIR / f"{name}.json"
    test_case["name"] = name
    test_case["saved_at"] = datetime.now().isoformat()
    _write_json(path, test_case)
    logger.info("saved edited recording to %s", path)
    return path


def load_recording(path: str) -> dict:
    p = Path(path)
    if not p.is_absolute():
        # allow both "recordings/foo.json" and "foo.json"
        candidate = BASE_DIR / "storage" / p
        p = candidate if candidate.exists() else BASE_DIR / p
    return _read_json(p)


def list_recordings() -> list:
    """Lists BOTH original recordings and their edited versions (if any),
    each as its own entry - list_recordings() re-reads every file from
    disk on every call, so whatever was most recently saved (original or
    edited) is always what gets returned here. There's no in-memory cache
    to go stale: the Dashboard just needs to call this again (which it
    already does on every page load) to see the latest edit.
    """
    out = []
    for directory, rel_prefix in (
        (RECORDINGS_DIR, "storage/recordings"),
        (EDITED_RECORDINGS_DIR, "storage/recordings/edited"),
        (TRIMMED_RECORDINGS_DIR, "storage/recordings/trimmed"),
    ):
        for f in directory.glob("*.json"):
            try:
                data = _read_json(f)
                out.append({
                    "name": data.get("name", f.stem),
                    "timestamp": data.get("saved_at", ""),
                    "step_count": len(data.get("actions", [])),
                    "path": f"{rel_prefix}/{f.name}",
                })
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("skipping unreadable recording %s: %s", f, e)

    out.sort(key=lambda r: r["timestamp"], reverse=True)
    return out


def save_execution(result: dict) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = EXECUTIONS_DIR / f"execution_{ts}.json"
    _write_json(path, result)
    return path


def save_search(result: dict) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = SEARCHES_DIR / f"search_{ts}.json"
    _write_json(path, result)
    return path
