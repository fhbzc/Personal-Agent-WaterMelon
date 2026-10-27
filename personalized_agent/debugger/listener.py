"""Read-only Listener inspection, using the real providers and event store.

No collection is triggered by opening this panel. Counts cover a bounded
sample and never claim to measure completeness without a reference sample.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from ..common.credentials_guard import hide_credentials
from ..common.logging.setup import SECRETS
from ..common.own_interface import scrub_sign_in

MAX_ROWS = 5000


def _application_unit(name, when):
    return {'application': name, 'focus_events': 0, 'text_events': 0,
            'navigation_events': 0, 'screenshots': 0, 'characters': 0,
            'at_character_limit': 0, 'last_focus': None, 'last_text': None,
            'last_event': when, 'methods': Counter(), 'long_focus_without_new_text': 0}


def _safe(value: Any) -> Any:
    if isinstance(value, str):
        return SECRETS.scrub(hide_credentials(scrub_sign_in(value)))
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe(v) for v in value]
    return value


def snapshot(app: Any, *, hours: int = 24, application: str = "",
             source: str = "", limit: int = 30) -> dict:
    """Bounded metadata scan, followed by only the requested stored bodies."""
    hours = max(1, min(168, hours))
    limit = max(1, min(50, limit))
    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=hours)).isoformat()
    if not app.observability_service.monitoring_enabled:
        return {"monitoring": False, "generated_at": now.isoformat()}
    health = app.listener.health()
    rows = app.db.query(
        "SELECT id, source, event_type, timestamp, application, "
        "length(json_extract(data, '$.text')) AS characters, "
        "json_extract(data, '$.how') AS method, "
        "json_extract(data, '$.duration_seconds') AS seconds "
        "FROM raw_events WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT ?",
        (since, MAX_ROWS + 1))
    capped = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    by_source: dict = {}
    by_app: dict = {}
    char_limit = int(app.config.get("listener_screen_max_chars", 4000))
    for row in rows:
        name = row["application"] or "(unknown)"
        src = by_source.setdefault(row["source"], {"events": 0, "last_event": row["timestamp"]})
        src["events"] += 1
        unit = by_app.setdefault(name, _application_unit(name, row['timestamp']))
        kind = row["event_type"]
        if kind == "application.focused":
            unit["focus_events"] += 1
            unit["last_focus"] = unit["last_focus"] or row["timestamp"]
        if kind == "screen.text":
            unit["text_events"] += 1
            unit["last_text"] = unit["last_text"] or row["timestamp"]
            unit["characters"] += row["characters"] or 0
            unit["at_character_limit"] += int((row["characters"] or 0) >= char_limit)
            unit["methods"][row["method"] or "unknown"] += 1
        unit["navigation_events"] += int(kind == "browser.navigation")
        unit["screenshots"] += int(kind == "screen.shot")
    # A focus period beginning after the newest text is a gap to inspect,
    # not evidence that new messages existed or a percentage of content lost.
    for row in rows:
        if row["event_type"] != "application.focus_ended":
            continue
        unit = by_app[row["application"] or "(unknown)"]
        try:
            seconds = float(row["seconds"] or 0)
            began = datetime.fromisoformat(row["timestamp"]) - timedelta(seconds=seconds)
            after = datetime.fromisoformat(unit["last_text"] or since)
            if seconds >= 60 and began > after:
                unit["long_focus_without_new_text"] += 1
        except (ValueError, TypeError, OverflowError):
            pass

    providers, issues = [], []
    for original in health["providers"]:
        status = original.get("status") or {}
        pid = original["provider_id"]
        errors = list(dict.fromkeys(str(e) for e in (
            original.get("last_error"), status.get("last_error"), status.get("error")) if e))
        for browser, result in (status.get("browsers") or {}).items():
            if result != "ok":
                errors.append(f"{browser}: {result}")
        reader = status.get("reader") or {}
        if reader.get("last_error") and reader["last_error"] not in errors:
            errors.append(reader["last_error"])
        if pid == 'screen':
            for attempt in reader.get('applications', []):
                when = attempt.get('last_attempt_at') or ''
                if when < since:
                    continue
                name = attempt.get('application') or '(unknown)'
                unit = by_app.setdefault(name, _application_unit(name, when))
                unit['reading'] = dict(attempt)
                layout = attempt.get('wechat_layout') or {}
                if layout.get('status') == 'unrecognized':
                    issues.append({'kind':'wechat_layout_unrecognized','source':pid,
                        'message':layout.get('reason') or 'layout_unrecognized'})
                if attempt.get('status') in ('failed', 'degraded'):
                    error = f"{name}: {attempt.get('last_error') or 'No readable window text'}"
                    errors.append(error)
                    issues.append({'kind': 'screen_read_failed', 'source': pid,
                                   'application': name, 'at': attempt.get('last_failure_at'),
                                   'message': attempt.get('last_error') or 'No readable window text',
                                   'partial': attempt.get('status') == 'degraded'})
        state = original.get("state", "unknown")
        if state == "running" and (errors or status.get("running") is False):
            state = "degraded"
        stored = by_source.get(pid, {"events": 0, "last_event": None})
        providers.append({"id": pid, "state": state, "status": status,
                          "errors": errors, "stored": stored,
                          "session_recorded": original.get("events", 0),
                          "routing": original.get("routing")})
        if errors:
            issues.append({"kind": "provider_error", "source": pid,
                           "message": "; ".join(errors)})
        if pid == "screenshot" and status.get("taken", 0) > 0 and original.get("events", 0) == 0:
            issues.append({"kind": "screenshot_not_persisted", "source": pid,
                           "taken": status["taken"], "message":
                           "Pictures were taken in this process, but no screenshot events were stored."})
            # The evidence folder is deliberately excluded from filesystem
            # activity. Only an actual directory rejection, not that
            # generic exclusion, establishes a screenshot routing conflict.
            routing = original.get("routing") or {}
            if (routing.get("filter_reasons") or {}).get("directory_excluded", 0):
                issues.append({"kind": "screenshot_path_excluded", "source": pid,
                               "message": "The screenshot evidence folder matches a directory exclusion."})
    apps = sorted(by_app.values(), key=lambda one: one["last_event"], reverse=True)
    for unit in apps:
        unit["methods"] = dict(unit["methods"])
        unit["coverage"] = "unverified" if unit["text_events"] else "no_text_in_window"

    where, args = ["timestamp >= ?"], [since]
    for column, value in (("application", application), ("source", source)):
        if value:
            where.append(f"{column} = ?")
            args.append(value)
    stored_events = app.db.query(
        "SELECT * FROM raw_events WHERE " + " AND ".join(where) +
        " ORDER BY timestamp DESC LIMIT ?", (*args, limit))
    ids = [row["id"] for row in stored_events]
    linked = {}
    if ids:
        linked = {row["event_id"]: row["states"] for row in app.db.query(
            "SELECT event_id, count(DISTINCT state_id) AS states FROM state_materials "
            "WHERE event_id IN (" + ",".join("?" for _ in ids) + ") GROUP BY event_id", ids)}
    events = []
    for row in stored_events:
        try:
            data = json.loads(row["data"])
        except (ValueError, TypeError):
            data = {}
        # Protect the page from large integration payloads. Stored records
        # remain untouched; the UI explicitly labels any preview truncation.
        serialized = json.dumps(data, ensure_ascii=False)
        events.append({"id": row["id"], "timestamp": row["timestamp"],
                       "application": row["application"], "source": row["source"],
                       "event_type": row["event_type"],
                       "data_preview": serialized[:16000],
                       "preview_truncated": len(serialized) > 16000,
                       "linked_states": linked.get(row["id"], 0)})
    screen_status = next((p['status'] for p in providers if p['id'] == 'screen'), {})
    return _safe({
        "monitoring": True, "running": health["running"], "generated_at": now.isoformat(),
        "window": {"hours": hours, "since": since, "sample_events": len(rows),
                   "sample_limit": MAX_ROWS, "sample_limited": capped,
                   "sample_since": rows[-1]["timestamp"] if rows else None},
        "settings": {"enabled": app.config.get("listener_enabled", True),
                     "screen_enabled": app.config.get("listener_screen_enabled", True),
                     "screen_interval_seconds": app.config.get("listener_screen_interval_seconds", 5),
                     # FAST SAMPLING: chats, mail and social media read often.
                     "fast": screen_status.get('fast'),
                     "wechat_immediate_write": screen_status.get('wechat_immediate_write', False),
                     "focus_check_seconds": screen_status.get('focus_check_seconds'),
                     "screen_max_chars": char_limit,
                     "screenshots_enabled": app.config.get("listener_screenshots_enabled", True),
                     "screenshots_per_day": app.config.get("listener_screenshots_per_day", 80)},
        "providers": providers, "applications": apps, "issues": issues, "events": events,
        "wechat": app.raw_store.wechat.list_conversations(20),
        "filters": {"application": application, "source": source, "limit": limit},
        "coverage_note": "Content completeness is unverified. Focus events, duplicate text and stored fragments do not measure message coverage. Only visible foreground content is sampled; no video or audio transcription."})
