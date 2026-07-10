"""
Structured operations on the school configuration.

The AI assistant never edits the data directly: it proposes a list of
operations (JSON), we validate them against the current school, show the
user a human-readable preview, and only apply them after confirmation.

Every operation handler works on a deep copy and returns a description
line for the preview, raising OpError on any problem.

Value conventions (friendly to small local models):
  • days:            "Mon" | "Monday" | 0-based index
  • times:           "17:30" (24h)
  • blocked windows: {"day": "Tue", "from": "16:00", "to": "18:00"}
                     (a bare [day, "16:00", "18:00"] list also works)
  • entities may be referenced by id ("T3") or by name ("Maria"),
    case-insensitively.
"""

from __future__ import annotations

import copy

from scheduler.data import DAYS, parse_time, tick_label

DAY_ALIASES = {}
for i, d in enumerate(DAYS):
    DAY_ALIASES[d.lower()] = i
FULL_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
for i, d in enumerate(FULL_DAYS):
    DAY_ALIASES[d] = i


class OpError(Exception):
    pass


# ── value parsing ─────────────────────────────────────────────────────────────

def norm_day(value) -> int:
    if isinstance(value, int):
        if 0 <= value < len(DAYS):
            return value
        raise OpError(f"Invalid day index {value} (0=Mon … 5=Sat).")
    if isinstance(value, str):
        idx = DAY_ALIASES.get(value.strip().lower())
        if idx is not None:
            return idx
    raise OpError(f"Unknown day {value!r} — use Mon…Sat.")


def norm_time(value) -> int:
    if isinstance(value, int):  # already a tick
        if 0 <= value <= 96:
            return value
        raise OpError(f"Invalid time tick {value}.")
    try:
        t = parse_time(str(value))
    except (ValueError, AttributeError):
        raise OpError(f"Cannot parse time {value!r} — use 24h HH:MM, e.g. \"17:30\".")
    if not (0 <= t <= 96):
        raise OpError(f"Time {value!r} is out of range.")
    return t


def norm_days(value) -> list[int]:
    if not isinstance(value, list):
        raise OpError("Days must be a list, e.g. [\"Mon\", \"Wed\"].")
    return sorted({norm_day(d) for d in value})


def norm_window(value) -> list[int]:
    """→ [day_idx, open_tick, close_tick]"""
    if isinstance(value, dict):
        day = value.get("day")
        start = value.get("from", value.get("start", value.get("open")))
        end   = value.get("to", value.get("end", value.get("close")))
    elif isinstance(value, (list, tuple)) and len(value) == 3:
        day, start, end = value
    else:
        raise OpError(f"Bad blocked window {value!r} — use "
                      "{\"day\": \"Tue\", \"from\": \"16:00\", \"to\": \"18:00\"}.")
    d, o, c = norm_day(day), norm_time(start), norm_time(end)
    if o >= c:
        raise OpError(f"Blocked window on {DAYS[d]}: start must be before end.")
    return [d, o, c]


def norm_windows(value) -> list[list[int]]:
    if not isinstance(value, list):
        raise OpError("blocked_windows must be a list.")
    return [norm_window(w) for w in value]


def window_label(w) -> str:
    return f"{DAYS[w[0]]} {tick_label(w[1])}-{tick_label(w[2])}"


def days_label(days) -> str:
    return ", ".join(DAYS[d] for d in sorted(days)) if days else "none"


# ── entity lookup ─────────────────────────────────────────────────────────────

def _find(items: list[dict], ref, kind: str, name_keys=("name",)) -> dict:
    if ref is None:
        raise OpError(f"Missing {kind} reference.")
    ref_s = str(ref).strip().lower()
    for it in items:
        if str(it.get("id", "")).lower() == ref_s:
            return it
    matches = [it for it in items
               if any(str(it.get(k, "")).lower() == ref_s for k in name_keys)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        ids = ", ".join(m["id"] for m in matches)
        raise OpError(f"Ambiguous {kind} '{ref}' — matches {ids}; use the id.")
    raise OpError(f"Unknown {kind} '{ref}'.")


def _find_location(school: dict, ref) -> str:
    ref_s = str(ref).strip().lower()
    for loc_id, loc_name in school["locations"].items():
        if ref_s in (loc_id.lower(), loc_name.lower()):
            return loc_id
    raise OpError(f"Unknown location '{ref}'.")


def _find_level(school: dict, ref) -> str:
    for lv in school["settings"]["levels"]:
        if lv.lower() == str(ref).strip().lower():
            return lv
    raise OpError(f"Unknown level '{ref}'. Valid: {', '.join(school['settings']['levels'])}.")


def next_id(items: list[dict], prefix: str) -> str:
    mx = 0
    for it in items:
        i = str(it.get("id", ""))
        if i.upper().startswith(prefix) and i[len(prefix):].isdigit():
            mx = max(mx, int(i[len(prefix):]))
    return f"{prefix}{mx + 1}"


# ── operation handlers ────────────────────────────────────────────────────────
# Each: handler(school, op) -> description string. Mutates school in place.

def _op_add_teacher(school, op):
    name = op.get("name")
    if not name:
        raise OpError("add_teacher needs a name.")
    t = {
        "id": next_id(school["teachers"], "T"),
        "name": name,
        "home": _find_location(school, op.get("home", next(iter(school["locations"])))),
        "qualified_levels": [_find_level(school, lv) for lv in op.get("qualified_levels", [])],
        "available_days": norm_days(op.get("available_days", [0, 1, 2, 3, 4, 5])),
        "blocked_windows": norm_windows(op.get("blocked_windows", [])),
    }
    school["teachers"].append(t)
    return (f"Add teacher {t['name']} ({t['id']}), home {t['home']}, "
            f"levels: {', '.join(t['qualified_levels']) or 'none'}, "
            f"days: {days_label(t['available_days'])}")


def _op_update_teacher(school, op):
    t = _find(school["teachers"], op.get("teacher"), "teacher")
    changes = []
    if "name" in op:
        t["name"] = str(op["name"]); changes.append(f"name → {t['name']}")
    if "home" in op:
        t["home"] = _find_location(school, op["home"]); changes.append(f"home → {t['home']}")
    if "qualified_levels" in op:
        t["qualified_levels"] = [_find_level(school, lv) for lv in op["qualified_levels"]]
        changes.append(f"levels → {', '.join(t['qualified_levels']) or 'none'}")
    if "available_days" in op:
        t["available_days"] = norm_days(op["available_days"])
        changes.append(f"available days → {days_label(t['available_days'])}")
    if "remove_available_days" in op:
        drop = set(norm_days(op["remove_available_days"]))
        t["available_days"] = [d for d in t["available_days"] if d not in drop]
        changes.append(f"available days → {days_label(t['available_days'])}")
    if "add_available_days" in op:
        add = set(norm_days(op["add_available_days"]))
        t["available_days"] = sorted(set(t["available_days"]) | add)
        changes.append(f"available days → {days_label(t['available_days'])}")
    if "blocked_windows" in op:
        t["blocked_windows"] = norm_windows(op["blocked_windows"])
        changes.append("blocked: " + (", ".join(window_label(w) for w in t["blocked_windows"]) or "none"))
    if "add_blocked_windows" in op:
        t["blocked_windows"] = t.get("blocked_windows", []) + norm_windows(op["add_blocked_windows"])
        changes.append("blocked: " + ", ".join(window_label(w) for w in t["blocked_windows"]))
    if not changes:
        raise OpError(f"update_teacher for {t['name']}: no recognised fields.")
    return f"Update teacher {t['name']} ({t['id']}): " + "; ".join(changes)


def _op_remove_teacher(school, op):
    t = _find(school["teachers"], op.get("teacher"), "teacher")
    school["teachers"].remove(t)
    return f"Remove teacher {t['name']} ({t['id']})"


def _op_add_room(school, op):
    name = op.get("name")
    if not name:
        raise OpError("add_room needs a name.")
    r = {
        "id": next_id(school["rooms"], "R"),
        "location": _find_location(school, op.get("location", next(iter(school["locations"])))),
        "name": name,
    }
    school["rooms"].append(r)
    return f"Add room {r['name']} ({r['id']}) at location {r['location']}"


def _op_remove_room(school, op):
    r = _find(school["rooms"], op.get("room"), "room")
    school["rooms"].remove(r)
    return f"Remove room {r['name']} ({r['id']})"


def _op_add_class(school, op):
    name = op.get("name")
    if not name:
        raise OpError("add_class needs a name.")
    c = {
        "id": next_id(school["classes"], "C"),
        "name": name,
        "level": _find_level(school, op.get("level")),
        "periods_per_session": int(op.get("periods_per_session", 2)),
        "sessions_per_week": int(op.get("sessions_per_week", 2)),
        "preferred_location": (_find_location(school, op["preferred_location"])
                               if op.get("preferred_location") else None),
    }
    school["classes"].append(c)
    return (f"Add class {c['name']} ({c['id']}): level {c['level']}, "
            f"{c['periods_per_session']} periods × {c['sessions_per_week']}/week"
            + (f", prefers {c['preferred_location']}" if c["preferred_location"] else ""))


def _op_update_class(school, op):
    c = _find(school["classes"], op.get("class"), "class")
    changes = []
    if "name" in op:
        c["name"] = str(op["name"]); changes.append(f"name → {c['name']}")
    if "level" in op:
        c["level"] = _find_level(school, op["level"]); changes.append(f"level → {c['level']}")
    if "periods_per_session" in op:
        c["periods_per_session"] = int(op["periods_per_session"])
        changes.append(f"periods/session → {c['periods_per_session']}")
    if "sessions_per_week" in op:
        c["sessions_per_week"] = int(op["sessions_per_week"])
        changes.append(f"sessions/week → {c['sessions_per_week']}")
    if "preferred_location" in op:
        c["preferred_location"] = (_find_location(school, op["preferred_location"])
                                   if op["preferred_location"] else None)
        changes.append(f"preferred location → {c['preferred_location'] or 'none'}")
    if not changes:
        raise OpError(f"update_class for {c['name']}: no recognised fields.")
    return f"Update class {c['name']} ({c['id']}): " + "; ".join(changes)


def _op_remove_class(school, op):
    c = _find(school["classes"], op.get("class"), "class")
    school["classes"].remove(c)
    school["students"] = [s for s in school["students"] if s["class_id"] != c["id"]]
    return f"Remove class {c['name']} ({c['id']}) and its student assignments"


def _op_add_student(school, op):
    name = op.get("name")
    if not name:
        raise OpError("add_student needs a name.")
    cls = _find(school["classes"], op.get("class"), "class")
    s = {
        "id": next_id(school["students"], "S"),
        "name": name,
        "class_id": cls["id"],
        "sibling_group": op.get("sibling_group") or None,
        "blocked_windows": norm_windows(op.get("blocked_windows", [])),
        "note": str(op.get("note", "")),
    }
    school["students"].append(s)
    desc = f"Add student {s['name']} ({s['id']}) to {cls['name']}"
    if s["sibling_group"]:
        desc += f", sibling group {s['sibling_group']}"
    if s["blocked_windows"]:
        desc += ", blocked: " + ", ".join(window_label(w) for w in s["blocked_windows"])
    return desc


def _op_update_student(school, op):
    s = _find(school["students"], op.get("student"), "student")
    changes = []
    if "name" in op:
        s["name"] = str(op["name"]); changes.append(f"name → {s['name']}")
    if "class" in op:
        cls = _find(school["classes"], op["class"], "class")
        s["class_id"] = cls["id"]; changes.append(f"class → {cls['name']}")
    if "sibling_group" in op:
        s["sibling_group"] = op["sibling_group"] or None
        changes.append(f"sibling group → {s['sibling_group'] or 'none'}")
    if "blocked_windows" in op:
        s["blocked_windows"] = norm_windows(op["blocked_windows"])
        changes.append("blocked: " + (", ".join(window_label(w) for w in s["blocked_windows"]) or "none"))
    if "add_blocked_windows" in op:
        s["blocked_windows"] = s.get("blocked_windows", []) + norm_windows(op["add_blocked_windows"])
        changes.append("blocked: " + ", ".join(window_label(w) for w in s["blocked_windows"]))
    if "note" in op:
        s["note"] = str(op["note"]); changes.append(f"note → {s['note']}")
    if not changes:
        raise OpError(f"update_student for {s['name']}: no recognised fields.")
    return f"Update student {s['name']} ({s['id']}): " + "; ".join(changes)


def _op_remove_student(school, op):
    s = _find(school["students"], op.get("student"), "student")
    school["students"].remove(s)
    return f"Remove student {s['name']} ({s['id']})"


def _op_set_day_hours(school, op):
    day = norm_day(op.get("day"))
    loc = _find_location(school, op.get("location"))
    day_name = DAYS[day]
    school["day_hours"].setdefault(day_name, {})
    if op.get("closed") or (op.get("open") is None and op.get("close") is None):
        school["day_hours"][day_name][loc] = None
        return f"Close location {loc} on {day_name}"
    o, c = norm_time(op.get("open")), norm_time(op.get("close"))
    if o >= c:
        raise OpError(f"set_day_hours {day_name}/{loc}: open must be before close.")
    school["day_hours"][day_name][loc] = {"open": o, "close": c}
    return f"Set {day_name} hours at {loc} to {tick_label(o)}-{tick_label(c)}"


def _op_update_settings(school, op):
    st = school["settings"]
    changes = []
    if "travel_periods" in op:
        st["travel_periods"] = int(op["travel_periods"])
        changes.append(f"travel periods → {st['travel_periods']}")
    if "young_learner_cutoff" in op:
        st["young_learner_cutoff"] = norm_time(op["young_learner_cutoff"])
        changes.append(f"young-learner cutoff → {tick_label(st['young_learner_cutoff'])}")
    if not changes:
        raise OpError("update_settings: no recognised fields "
                      "(travel_periods, young_learner_cutoff).")
    return "Update settings: " + "; ".join(changes)


HANDLERS = {
    "add_teacher":     _op_add_teacher,
    "update_teacher":  _op_update_teacher,
    "remove_teacher":  _op_remove_teacher,
    "add_room":        _op_add_room,
    "remove_room":     _op_remove_room,
    "add_class":       _op_add_class,
    "update_class":    _op_update_class,
    "remove_class":    _op_remove_class,
    "add_student":     _op_add_student,
    "update_student":  _op_update_student,
    "remove_student":  _op_remove_student,
    "set_day_hours":   _op_set_day_hours,
    "update_settings": _op_update_settings,
}


# ── public API ────────────────────────────────────────────────────────────────

def apply_operations(school: dict, operations: list[dict]) -> tuple[dict, list[str], list[str]]:
    """Apply ops to a deep copy. Returns (new_school, preview_lines, errors).
    If errors is non-empty the returned school must not be saved."""
    new_school = copy.deepcopy(school)
    preview: list[str] = []
    errors: list[str] = []

    if not isinstance(operations, list):
        return new_school, preview, ["Operations must be a list."]

    for i, op in enumerate(operations):
        if not isinstance(op, dict) or "op" not in op:
            errors.append(f"Operation {i + 1}: missing 'op' field.")
            continue
        handler = HANDLERS.get(op["op"])
        if handler is None:
            errors.append(f"Operation {i + 1}: unknown op '{op['op']}'.")
            continue
        try:
            preview.append(handler(new_school, op))
        except OpError as e:
            errors.append(f"Operation {i + 1} ({op['op']}): {e}")
        except (KeyError, TypeError, ValueError) as e:
            errors.append(f"Operation {i + 1} ({op['op']}): bad value ({e}).")

    return new_school, preview, errors
