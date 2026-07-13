"""
Persistence: workspaces, school data, schedules, and undo history.

Workspaces
----------
Each workspace is an independent school dataset + solved schedule —
typically one per academic year ("2025-2026", "2026-2027"). One is
active at a time; all API operations act on the active workspace.
A new workspace can be seeded from the current one, carrying the solved
schedule along as a *stability baseline* (each class remembers its old
day/time/teacher as `previous_slots`, which the solver treats as a soft
preference — "keep last year's schedule where possible").

Undo
----
Before every mutation the previous state (school + schedule) is pushed
onto a per-workspace history stack (last 30 snapshots). POST /api/undo
restores the most recent snapshot.

Layout on disk (SCHOOL_DATA_DIR, default ./data):
    workspaces.json                  registry {active, workspaces: [...]}
    workspaces/<key>/school.json
    workspaces/<key>/last_schedule.json
    workspaces/<key>/history/<ms>.json
    erp_sources.json / erp_mapping.json   (shared across workspaces)
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import time
from collections import defaultdict
from pathlib import Path

from scheduler.data import DAYS, default_school

_DATA_DIR = Path(os.environ.get("SCHOOL_DATA_DIR", "data"))
_WORKSPACES_DIR = _DATA_DIR / "workspaces"
_REGISTRY_FILE = _DATA_DIR / "workspaces.json"

_lock = threading.RLock()

MAX_TICK = 24 * 4  # 96 ticks in a day
HISTORY_LIMIT = 30


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# ── workspace registry ────────────────────────────────────────────────────────

def _registry() -> dict:
    with _lock:
        if _REGISTRY_FILE.exists():
            return _read(_REGISTRY_FILE)
        # first run (or upgrade from the pre-workspace layout)
        reg = {"active": "default",
               "workspaces": [{"key": "default", "name": "My school"}]}
        ws_dir = _WORKSPACES_DIR / "default"
        ws_dir.mkdir(parents=True, exist_ok=True)
        for fname in ("school.json", "last_schedule.json"):
            legacy = _DATA_DIR / fname
            if legacy.exists() and not (ws_dir / fname).exists():
                legacy.replace(ws_dir / fname)
        _write(_REGISTRY_FILE, reg)
        return reg


def list_workspaces() -> dict:
    reg = _registry()
    return {"active": reg["active"], "workspaces": reg["workspaces"]}


def _slugify(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    return s or "workspace"


def activate_workspace(key: str) -> None:
    with _lock:
        reg = _registry()
        if not any(w["key"] == key for w in reg["workspaces"]):
            raise KeyError(f"Unknown workspace '{key}'.")
        reg["active"] = key
        _write(_REGISTRY_FILE, reg)


def create_workspace(name: str, seed_from_active: bool = False) -> dict:
    """Create (and activate) a new workspace. With seed_from_active the new
    workspace copies the active one's full configuration and roster, and the
    active workspace's solved schedule becomes the stability baseline."""
    with _lock:
        reg = _registry()
        base = _slugify(name)
        key, n = base, 2
        while any(w["key"] == key for w in reg["workspaces"]):
            key, n = f"{base}-{n}", n + 1

        if seed_from_active:
            school = copy.deepcopy(load_school())
            schedule = load_last_schedule()
            if schedule and schedule.get("schedule"):
                attach_baseline(school, schedule)
        else:
            school = default_school()

        ws_dir = _WORKSPACES_DIR / key
        ws_dir.mkdir(parents=True, exist_ok=True)
        _write(ws_dir / "school.json", school)

        reg["workspaces"].append({"key": key, "name": name})
        reg["active"] = key
        _write(_REGISTRY_FILE, reg)
        return {"key": key, "name": name}


def attach_baseline(school: dict, schedule: dict, field: str = "previous_slots") -> None:
    """Record each class's solved slots on the class (default: as
    `previous_slots`, the year-mirroring baseline; `sticky_slots` is the
    lightweight re-solve baseline) so the solver can prefer keeping them."""
    by_class = defaultdict(list)
    for e in schedule.get("schedule", []):
        by_class[e["class_id"]].append({
            "day": e["day_idx"],
            "start_tick": e["start_tick"],
            "room_id": e["room_id"],
            "teacher_id": e["teacher_id"],
        })
    for c in school["classes"]:
        slots = sorted(by_class.get(c["id"], []), key=lambda s: (s["day"], s["start_tick"]))
        if slots:
            c[field] = slots
        else:
            c.pop(field, None)


# ── active-workspace file access ──────────────────────────────────────────────

def _ws_dir() -> Path:
    reg = _registry()
    return _WORKSPACES_DIR / reg["active"]


def load_school() -> dict:
    with _lock:
        f = _ws_dir() / "school.json"
        if not f.exists():
            school = default_school()
            _write(f, school)
            return school
        return _read(f)


def save_school(school: dict) -> None:
    with _lock:
        _write(_ws_dir() / "school.json", school)


def load_last_schedule() -> dict | None:
    with _lock:
        f = _ws_dir() / "last_schedule.json"
        return _read(f) if f.exists() else None


def save_last_schedule(result: dict) -> None:
    with _lock:
        _write(_ws_dir() / "last_schedule.json", result)


def reset_school() -> dict:
    with _lock:
        school = default_school()
        save_school(school)
        f = _ws_dir() / "last_schedule.json"
        if f.exists():
            f.unlink()
        return school


# ── revisions (multi-user conflict detection) ────────────────────────────────

def _rev_of(obj) -> str:
    return hashlib.sha1(
        json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:12]


def school_rev() -> str:
    return _rev_of(load_school())


def schedule_rev() -> str:
    sched = load_last_schedule()
    return _rev_of(sched) if sched is not None else "none"


# ── undo history ──────────────────────────────────────────────────────────────

def _history_dir() -> Path:
    return _ws_dir() / "history"

def _history_files() -> list[Path]:
    d = _history_dir()
    return sorted(d.glob("*.json")) if d.exists() else []


def push_history(label: str) -> None:
    """Snapshot the current state BEFORE a mutation, so it can be undone."""
    with _lock:
        snap = {
            "ts": time.time(),
            "label": label,
            "school": load_school(),
            "schedule": load_last_schedule(),
        }
        _write(_history_dir() / f"{int(snap['ts'] * 1000)}.json", snap)
        files = _history_files()
        for f in files[:-HISTORY_LIMIT]:
            f.unlink()


def history_list() -> list[dict]:
    out = []
    for f in reversed(_history_files()):
        try:
            snap = _read(f)
            out.append({"label": snap.get("label", "change"), "ts": snap.get("ts")})
        except (json.JSONDecodeError, OSError):
            continue
    return out


def undo() -> dict | None:
    """Restore the most recent snapshot. Returns {label, school, schedule}
    or None if there is nothing to undo."""
    with _lock:
        files = _history_files()
        if not files:
            return None
        snap = _read(files[-1])
        save_school(snap["school"])
        sched_file = _ws_dir() / "last_schedule.json"
        if snap.get("schedule") is not None:
            _write(sched_file, snap["schedule"])
        elif sched_file.exists():
            sched_file.unlink()
        files[-1].unlink()
        return snap


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
            err(f"Αίθουσα {r.get('id')}: απαιτείται όνομα.")
        cap = r.get("capacity")
        if cap is not None and (not isinstance(cap, int) or cap < 1):
            err(f"Αίθουσα {r.get('id')}: η χωρητικότητα πρέπει να είναι θετικός ακέραιος ή κενή.")

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
        mh = t.get("max_hours")
        if mh is not None and (not isinstance(mh, int) or mh < 1):
            err(f"Καθηγητής {tid}: οι μέγιστες ώρες πρέπει να είναι θετικός ακέραιος ή κενό.")

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
        if "saturday_preferred" in c and not isinstance(c["saturday_preferred"], bool):
            err(f"Class {cid}: saturday_preferred must be true/false.")
        pin = c.get("pinned_teacher")
        if pin and pin not in {t.get("id") for t in school["teachers"]}:
            err(f"Τμήμα {cid}: ο σταθερός καθηγητής '{pin}' δεν υπάρχει.")
        for sl in c.get("pinned_slots", []):
            if (not isinstance(sl, dict) or not isinstance(sl.get("day"), int)
                    or not isinstance(sl.get("start"), int)
                    or not (0 <= sl["day"] < len(DAYS)) or not (0 <= sl["start"] <= MAX_TICK)):
                err(f"Τμήμα {cid}: μη έγκυρη σταθερή ώρα {sl!r}.")

    # students
    check_unique_ids("student", school["students"])
    for s in school["students"]:
        sid = s.get("id")
        if s.get("class_id") not in class_ids:
            err(f"Student {sid}: unknown class '{s.get('class_id')}'.")
        check_windows(f"Student {sid}", s.get("blocked_windows", []))

    return errors
