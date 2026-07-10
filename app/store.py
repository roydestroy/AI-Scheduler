"""
JSON persistence + validation for the school configuration.

The school lives in a single JSON file (default: data/school.json,
override with the SCHOOL_DATA_DIR env var). On first run it is seeded
from the sample dataset in scheduler.data.default_school().
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from scheduler.data import DAYS, default_school

_DATA_DIR = Path(os.environ.get("SCHOOL_DATA_DIR", "data"))
_SCHOOL_FILE = _DATA_DIR / "school.json"
_SCHEDULE_FILE = _DATA_DIR / "last_schedule.json"

_lock = threading.Lock()

MAX_TICK = 24 * 4  # 96 ticks in a day


# ── load / save ───────────────────────────────────────────────────────────────

def load_school() -> dict:
    with _lock:
        if not _SCHOOL_FILE.exists():
            school = default_school()
            _write(_SCHOOL_FILE, school)
            return school
        return json.loads(_SCHOOL_FILE.read_text(encoding="utf-8"))


def save_school(school: dict) -> None:
    with _lock:
        _write(_SCHOOL_FILE, school)


def load_last_schedule() -> dict | None:
    with _lock:
        if not _SCHEDULE_FILE.exists():
            return None
        return json.loads(_SCHEDULE_FILE.read_text(encoding="utf-8"))


def save_last_schedule(result: dict) -> None:
    with _lock:
        _write(_SCHEDULE_FILE, result)


def reset_school() -> dict:
    school = default_school()
    save_school(school)
    with _lock:
        if _SCHEDULE_FILE.exists():
            _SCHEDULE_FILE.unlink()
    return school


def _write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ── validation ────────────────────────────────────────────────────────────────

def validate_school(school: dict) -> list[str]:
    """Return a list of human-readable problems (empty = valid)."""
    errors: list[str] = []

    def err(msg: str):
        errors.append(msg)

    for key in ("locations", "settings", "day_hours", "rooms", "teachers", "classes", "students"):
        if key not in school:
            err(f"Missing top-level key: {key}")
    if errors:
        return errors

    locations = school["locations"]
    settings  = school["settings"]
    levels    = settings.get("levels", [])

    if not locations:
        err("At least one location is required.")

    for key in ("ticks_per_period", "travel_periods", "young_learner_cutoff",
                "young_learner_levels", "levels", "day_pairs_2x", "day_triplets_3x"):
        if key not in settings:
            err(f"Missing settings key: {key}")

    # day_hours
    for day, locs in school["day_hours"].items():
        if day not in DAYS:
            err(f"Unknown day '{day}' in day_hours.")
            continue
        for loc, w in locs.items():
            if loc not in locations:
                err(f"day_hours {day}: unknown location '{loc}'.")
            if w is None:
                continue
            if not isinstance(w, dict) or "open" not in w or "close" not in w:
                err(f"day_hours {day}/{loc}: window must be null or {{open, close}}.")
                continue
            if not (0 <= w["open"] < w["close"] <= MAX_TICK):
                err(f"day_hours {day}/{loc}: invalid window (open must be before close).")

    def check_windows(owner: str, windows) -> None:
        if not isinstance(windows, list):
            err(f"{owner}: blocked_windows must be a list.")
            return
        for w in windows:
            if (not isinstance(w, (list, tuple)) or len(w) != 3
                    or not all(isinstance(x, int) for x in w)):
                err(f"{owner}: blocked window must be [day_idx, open_tick, close_tick].")
                continue
            d, o, c = w
            if not (0 <= d < len(DAYS)):
                err(f"{owner}: blocked window has invalid day index {d}.")
            if not (0 <= o < c <= MAX_TICK):
                err(f"{owner}: blocked window {o}-{c} is invalid.")

    def check_unique_ids(kind: str, items) -> None:
        seen = set()
        for it in items:
            i = it.get("id")
            if not i:
                err(f"A {kind} is missing an id.")
            elif i in seen:
                err(f"Duplicate {kind} id: {i}")
            seen.add(i)

    # rooms
    check_unique_ids("room", school["rooms"])
    for r in school["rooms"]:
        if r.get("location") not in locations:
            err(f"Room {r.get('id')}: unknown location '{r.get('location')}'.")
        if not r.get("name"):
            err(f"Room {r.get('id')}: name is required.")

    # teachers
    check_unique_ids("teacher", school["teachers"])
    for t in school["teachers"]:
        tid = t.get("id")
        if t.get("home") and t["home"] not in locations:
            err(f"Teacher {tid}: unknown home location '{t['home']}'.")
        for lv in t.get("qualified_levels", []):
            if lv not in levels:
                err(f"Teacher {tid}: unknown level '{lv}'.")
        for d in t.get("available_days", []):
            if not isinstance(d, int) or not (0 <= d < len(DAYS)):
                err(f"Teacher {tid}: invalid available day {d!r}.")
        check_windows(f"Teacher {tid}", t.get("blocked_windows", []))

    # classes
    check_unique_ids("class", school["classes"])
    class_ids = {c.get("id") for c in school["classes"]}
    for c in school["classes"]:
        cid = c.get("id")
        if c.get("level") not in levels:
            err(f"Class {cid}: unknown level '{c.get('level')}'.")
        if not isinstance(c.get("periods_per_session"), int) or not (1 <= c["periods_per_session"] <= 6):
            err(f"Class {cid}: periods_per_session must be 1-6.")
        if c.get("sessions_per_week") not in (1, 2, 3):
            err(f"Class {cid}: sessions_per_week must be 1, 2 or 3.")
        pref = c.get("preferred_location")
        if pref and pref not in locations:
            err(f"Class {cid}: unknown preferred_location '{pref}'.")

    # students
    check_unique_ids("student", school["students"])
    for s in school["students"]:
        sid = s.get("id")
        if s.get("class_id") not in class_ids:
            err(f"Student {sid}: unknown class '{s.get('class_id')}'.")
        check_windows(f"Student {sid}", s.get("blocked_windows", []))

    return errors
