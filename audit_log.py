"""Structured audit log for Provenance Guard.

Stores one JSON object per decision in a JSON-array file. Milestone 4 extends the
entry shape (second signal) and Milestone 5 adds appeal records; the read/write
helpers here stay the same.
"""

import json
import os
import threading

LOG_PATH = os.getenv("AUDIT_LOG_PATH", "audit_log.json")

_lock = threading.Lock()


def _read_all():
    if not os.path.exists(LOG_PATH):
        return []
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def log_entry(entry):
    """Append a structured entry to the audit log (thread-safe)."""
    with _lock:
        entries = _read_all()
        entries.append(entry)
        with open(LOG_PATH, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, indent=2, ensure_ascii=False)


def get_recent(limit=50):
    """Return the most recent entries, newest last."""
    with _lock:
        entries = _read_all()
    return entries[-limit:]


def update_entry(content_id, updates):
    """Merge `updates` into the entry with `content_id`. Returns it, or None."""
    with _lock:
        entries = _read_all()
        target = None
        for entry in entries:
            if entry.get("content_id") == content_id:
                entry.update(updates)
                target = entry
                break
        if target is not None:
            with open(LOG_PATH, "w", encoding="utf-8") as fh:
                json.dump(entries, fh, indent=2, ensure_ascii=False)
        return target


def find_by_content_id(content_id):
    """Return the entry with a given content_id, or None."""
    with _lock:
        entries = _read_all()
    for entry in entries:
        if entry.get("content_id") == content_id:
            return entry
    return None
