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
SNAPSHOTS_DIR = BASE_DIR / "storage" / "snapshots"

for d in (RECORDINGS_DIR, EDITED_RECORDINGS_DIR, TRIMMED_RECORDINGS_DIR, EXECUTIONS_DIR, SEARCHES_DIR, SNAPSHOTS_DIR):
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


def recover_orphaned_drafts() -> list:
    """FIX 5/FIX 6 (crash recovery): a recording session's append-only
    JSONL sidecar (recorder/record_session.py's Recorder._flush_draft)
    has every action captured, one per line, the instant it happens -
    including ones a crash prevented the periodic full-JSON consolidation
    from ever writing, and even a session that crashed before its FIRST
    consolidation, which leaves a sidecar with no matching .json at all.
    Normally that sidecar is deleted the moment a recording finishes and
    saves successfully (see app.py's _finish_recording) - one still
    sitting here means the process that owned it never reached that
    point (killed, crashed, power loss), and everything it captured would
    otherwise be silently lost.

    Called at app startup and again right before a NEW recording session
    starts (see Recorder.start()) - either one is "the next launch" this
    exists for. Reconciles every orphaned sidecar it finds into a
    complete, saved recording (tagged recovered=True so it's obviously
    distinguishable from a normally-finished one) and only removes the
    sidecar once that save has actually succeeded - a save failure here
    leaves the sidecar in place so the NEXT launch/call gets another
    chance at it rather than losing the only remaining copy.

    Only ever safe to call when nothing is actively recording (both call
    sites satisfy this: app startup has no session yet, and this runs
    before the new session's own sidecar is created) - a sidecar
    belonging to a still-running recording would otherwise race with that
    session's own in-progress appends.

    Returns the list of recovered recording paths (empty when there was
    nothing to recover).
    """
    recovered_paths = []
    for jsonl_path in sorted(RECORDINGS_DIR.glob("*.draft.jsonl")):
        name = jsonl_path.name[: -len(".draft.jsonl")]
        actions = []
        try:
            raw = jsonl_path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("couldn't read orphaned draft sidecar %s: %s", jsonl_path, e)
            continue
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                actions.append(json.loads(line))
            except json.JSONDecodeError:
                # a crash mid-write can only ever truncate the LAST line -
                # every earlier line was already a complete, flushed
                # append (each write is one line + immediate close) - skip
                # just that one rather than losing the whole recovery
                continue

        if not actions:
            # nothing was ever captured before the crash - nothing to
            # recover, just clean up the empty/unusable sidecar
            try:
                jsonl_path.unlink()
            except OSError:
                pass
            continue

        first_action = actions[0]
        start_url = first_action.get("page_url") if first_action.get("action_type") == "navigate" else None
        test_case = {
            "name": name,
            "start_url": start_url,
            "actions": actions,
            "recovered": True,
            "recovered_note": (
                f"Recovered after an interrupted recording session - "
                f"{len(actions)} action(s) captured before it stopped."
            ),
        }
        try:
            path = save_recording(test_case)
        except OSError as e:
            logger.error("couldn't save recovered recording %r: %s", name, e)
            continue
        try:
            jsonl_path.unlink()
        except OSError:
            pass
        logger.warning(
            "recovered %d action(s) from an interrupted recording session: %s",
            len(actions), path,
        )
        recovered_paths.append(path)
    return recovered_paths
