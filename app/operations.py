"""
Structured operations on the school configuration.

The AI assistant never edits the data directly: it proposes a list of
operations (JSON), we validate them against the current school, show the
user a human-readable preview (in Greek), and only apply them after
confirmation.

Every operation handler works on a deep copy and returns a description
line for the preview, raising OpError on any problem.

Value conventions (friendly to small local models):
  • days:            "Mon" | "Δευτέρα" | "Δευ" | 0-based index
  • times:           "17:30" (24h)
  • blocked windows: {"day": "Tue", "from": "16:00", "to": "18:00"}
                     (a bare [day, "16:00", "18:00"] list also works)
  • entities may be referenced by id ("T3") or by name ("Maria"),
    case-insensitively.
"""

from __future__ import annotations

import copy
import unicodedata

from scheduler.data import DAYS, parse_time, tick_label

#: display names for days (the data model keeps English keys internally)
GREEK_DAYS = ["Δευ", "Τρί", "Τετ", "Πέμ", "Παρ", "Σάβ"]
GREEK_DAYS_FULL = ["Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο"]
FULL_DAYS_EN = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]


def _fold(s: str) -> str:
    """lowercase + strip Greek accents, for forgiving matching."""
    s = unicodedata.normalize("NFD", str(s).strip().lower())
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn")


DAY_ALIASES: dict[str, int] = {}
for i in range(len(DAYS)):
    for alias in (DAYS[i], FULL_DAYS_EN[i], GREEK_DAYS[i], GREEK_DAYS_FULL[i]):
        DAY_ALIASES[_fold(alias)] = i


class OpError(Exception):
    pass


# ── value parsing ─────────────────────────────────────────────────────────────

def norm_day(value) -> int:
    if isinstance(value, int):
        if 0 <= value < len(DAYS):
            return value
        raise OpError(f"Μη έγκυρη ημέρα {value} (0=Δευτέρα … 5=Σάββατο).")
    if isinstance(value, str):
        idx = DAY_ALIASES.get(_fold(value))
        if idx is not None:
            return idx
    raise OpError(f"Άγνωστη ημέρα {value!r} — γράψτε π.χ. Δευτέρα ή Mon.")


def norm_time(value) -> int:
    if isinstance(value, int):  # already a tick
        if 0 <= value <= 96:
            return value
        raise OpError(f"Μη έγκυρη ώρα {value}.")
    try:
        t = parse_time(str(value))
    except (ValueError, AttributeError):
        raise OpError(f"Δεν αναγνωρίζεται η ώρα {value!r} — γράψτε 24ωρη μορφή, π.χ. \"17:30\".")
    if not (0 <= t <= 96):
        raise OpError(f"Η ώρα {value!r} είναι εκτός ορίων.")
    return t


def norm_days(value) -> list[int]:
    if not isinstance(value, list):
        raise OpError("Οι ημέρες πρέπει να είναι λίστα, π.χ. [\"Δευ\", \"Τετ\"].")
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
        raise OpError(f"Μη έγκυρο διάστημα μη διαθεσιμότητας {value!r} — μορφή: "
                      "{\"day\": \"Τρί\", \"from\": \"16:00\", \"to\": \"18:00\"}.")
    d, o, c = norm_day(day), norm_time(start), norm_time(end)
    if o >= c:
        raise OpError(f"Διάστημα την {GREEK_DAYS_FULL[d]}: η έναρξη πρέπει να προηγείται της λήξης.")
    return [d, o, c]


def norm_windows(value) -> list[list[int]]:
    if not isinstance(value, list):
        raise OpError("Το blocked_windows πρέπει να είναι λίστα.")
    return [norm_window(w) for w in value]


def window_label(w) -> str:
    return f"{GREEK_DAYS[w[0]]} {tick_label(w[1])}-{tick_label(w[2])}"


def days_label(days) -> str:
    return ", ".join(GREEK_DAYS[d] for d in sorted(days)) if days else "καμία"


# ── entity lookup ─────────────────────────────────────────────────────────────

def _find(items: list[dict], ref, kind: str, name_keys=("name",)) -> dict:
    kinds_gr = {"teacher": "καθηγητής/τρια", "room": "αίθουσα",
                "class": "τμήμα", "student": "μαθητής/τρια"}
    label = kinds_gr.get(kind, kind)
    if ref is None:
        raise OpError(f"Λείπει η αναφορά σε {label}.")
    ref_s = _fold(ref)
    for it in items:
        if _fold(it.get("id", "")) == ref_s:
            return it
    matches = [it for it in items
               if any(_fold(it.get(k, "")) == ref_s for k in name_keys)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        ids = ", ".join(m["id"] for m in matches)
        raise OpError(f"Ασαφής αναφορά '{ref}' ({label}) — ταιριάζει με {ids}· "
                      "χρησιμοποιήστε το id.")
    raise OpError(f"Δεν βρέθηκε {label} '{ref}'.")


def _find_location(school: dict, ref) -> str:
    ref_s = _fold(ref)
    for loc_id, loc_name in school["locations"].items():
        if ref_s in (_fold(loc_id), _fold(loc_name)):
            return loc_id
    raise OpError(f"Άγνωστο κτήριο '{ref}'.")


def _find_level(school: dict, ref) -> str:
    for lv in school["settings"]["levels"]:
        if _fold(lv) == _fold(ref):
            return lv
    raise OpError(f"Άγνωστο επίπεδο '{ref}'. Έγκυρα: "
                  f"{', '.join(school['settings']['levels'])}.")


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
        raise OpError("Το add_teacher χρειάζεται όνομα.")
    t = {
        "id": next_id(school["teachers"], "T"),
        "name": name,
        "home": _find_location(school, op.get("home", next(iter(school["locations"])))),
        "qualified_levels": [_find_level(school, lv) for lv in op.get("qualified_levels", [])],
        "available_days": norm_days(op.get("available_days", [0, 1, 2, 3, 4, 5])),
        "blocked_windows": norm_windows(op.get("blocked_windows", [])),
    }
    school["teachers"].append(t)
    return (f"Προσθήκη καθηγητή/τριας {t['name']} ({t['id']}), έδρα {t['home']}, "
            f"επίπεδα: {', '.join(t['qualified_levels']) or 'κανένα'}, "
            f"ημέρες: {days_label(t['available_days'])}")


def _op_update_teacher(school, op):
    t = _find(school["teachers"], op.get("teacher"), "teacher")
    changes = []
    if "name" in op:
        t["name"] = str(op["name"]); changes.append(f"όνομα → {t['name']}")
    if "home" in op:
        t["home"] = _find_location(school, op["home"]); changes.append(f"έδρα → {t['home']}")
    if "qualified_levels" in op:
        t["qualified_levels"] = [_find_level(school, lv) for lv in op["qualified_levels"]]
        changes.append(f"επίπεδα → {', '.join(t['qualified_levels']) or 'κανένα'}")
    if "available_days" in op:
        t["available_days"] = norm_days(op["available_days"])
        changes.append(f"διαθέσιμες ημέρες → {days_label(t['available_days'])}")
    if "remove_available_days" in op:
        drop = set(norm_days(op["remove_available_days"]))
        t["available_days"] = [d for d in t["available_days"] if d not in drop]
        changes.append(f"διαθέσιμες ημέρες → {days_label(t['available_days'])}")
    if "add_available_days" in op:
        add = set(norm_days(op["add_available_days"]))
        t["available_days"] = sorted(set(t["available_days"]) | add)
        changes.append(f"διαθέσιμες ημέρες → {days_label(t['available_days'])}")
    if "blocked_windows" in op:
        t["blocked_windows"] = norm_windows(op["blocked_windows"])
        changes.append("μη διαθέσιμος/η: "
                       + (", ".join(window_label(w) for w in t["blocked_windows"]) or "ποτέ"))
    if "add_blocked_windows" in op:
        t["blocked_windows"] = t.get("blocked_windows", []) + norm_windows(op["add_blocked_windows"])
        changes.append("μη διαθέσιμος/η: " + ", ".join(window_label(w) for w in t["blocked_windows"]))
    if "max_hours" in op:
        if op["max_hours"]:
            t["max_hours"] = int(op["max_hours"])
            changes.append(f"μέγιστες ώρες/εβδ. → {t['max_hours']}")
        else:
            t.pop("max_hours", None)
            changes.append("μέγιστες ώρες → χωρίς όριο")
    if not changes:
        raise OpError(f"update_teacher για {t['name']}: δεν δόθηκαν αναγνωρίσιμα πεδία.")
    return f"Ενημέρωση καθηγητή/τριας {t['name']} ({t['id']}): " + "· ".join(changes)


def _op_remove_teacher(school, op):
    t = _find(school["teachers"], op.get("teacher"), "teacher")
    school["teachers"].remove(t)
    return f"Αφαίρεση καθηγητή/τριας {t['name']} ({t['id']})"


def _op_add_room(school, op):
    name = op.get("name")
    if not name:
        raise OpError("Το add_room χρειάζεται όνομα.")
    r = {
        "id": next_id(school["rooms"], "R"),
        "location": _find_location(school, op.get("location", next(iter(school["locations"])))),
        "name": name,
    }
    if op.get("capacity"):
        r["capacity"] = int(op["capacity"])
    school["rooms"].append(r)
    return (f"Προσθήκη αίθουσας {r['name']} ({r['id']}) στο κτήριο {r['location']}"
            + (f", χωρητικότητα {r['capacity']} μαθητές" if r.get("capacity") else ""))


def _op_update_room(school, op):
    r = _find(school["rooms"], op.get("room"), "room")
    changes = []
    if "name" in op:
        r["name"] = str(op["name"]); changes.append(f"όνομα → {r['name']}")
    if "location" in op:
        r["location"] = _find_location(school, op["location"])
        changes.append(f"κτήριο → {r['location']}")
    if "capacity" in op:
        if op["capacity"]:
            r["capacity"] = int(op["capacity"])
            changes.append(f"χωρητικότητα → {r['capacity']} μαθητές")
        else:
            r.pop("capacity", None)
            changes.append("χωρητικότητα → απεριόριστη")
    if not changes:
        raise OpError(f"update_room για {r['name']}: δεν δόθηκαν αναγνωρίσιμα πεδία.")
    return f"Ενημέρωση αίθουσας {r['name']} ({r['id']}): " + "· ".join(changes)


def _op_remove_room(school, op):
    r = _find(school["rooms"], op.get("room"), "room")
    school["rooms"].remove(r)
    return f"Αφαίρεση αίθουσας {r['name']} ({r['id']})"


def _op_add_class(school, op):
    name = op.get("name")
    if not name:
        raise OpError("Το add_class χρειάζεται όνομα.")
    c = {
        "id": next_id(school["classes"], "C"),
        "name": name,
        "level": _find_level(school, op.get("level")),
        "periods_per_session": int(op.get("periods_per_session", 2)),
        "sessions_per_week": int(op.get("sessions_per_week", 2)),
        "preferred_location": (_find_location(school, op["preferred_location"])
                               if op.get("preferred_location") else None),
        "saturday_preferred": bool(op.get("saturday_preferred", False)),
    }
    school["classes"].append(c)
    return (f"Προσθήκη τμήματος {c['name']} ({c['id']}): επίπεδο {c['level']}, "
            f"{c['periods_per_session']} περίοδοι × {c['sessions_per_week']}/εβδομάδα"
            + (f", προτίμηση κτηρίου {c['preferred_location']}" if c["preferred_location"] else "")
            + (", προτίμηση Σαββάτου" if c["saturday_preferred"] else ""))


def _op_update_class(school, op):
    c = _find(school["classes"], op.get("class"), "class")
    changes = []
    if "name" in op:
        c["name"] = str(op["name"]); changes.append(f"όνομα → {c['name']}")
    if "level" in op:
        c["level"] = _find_level(school, op["level"]); changes.append(f"επίπεδο → {c['level']}")
    if "periods_per_session" in op:
        c["periods_per_session"] = int(op["periods_per_session"])
        changes.append(f"περίοδοι/μάθημα → {c['periods_per_session']}")
    if "sessions_per_week" in op:
        c["sessions_per_week"] = int(op["sessions_per_week"])
        changes.append(f"μαθήματα/εβδομάδα → {c['sessions_per_week']}")
    if "preferred_location" in op:
        c["preferred_location"] = (_find_location(school, op["preferred_location"])
                                   if op["preferred_location"] else None)
        changes.append(f"προτιμώμενο κτήριο → {c['preferred_location'] or 'κανένα'}")
    if "saturday_preferred" in op:
        c["saturday_preferred"] = bool(op["saturday_preferred"])
        changes.append(f"προτίμηση Σαββάτου → {'ναι' if c['saturday_preferred'] else 'όχι'}")
    if "pinned_teacher" in op:
        if op["pinned_teacher"]:
            t = _find(school["teachers"], op["pinned_teacher"], "teacher")
            if c["level"] not in t["qualified_levels"]:
                raise OpError(f"Ο/Η {t['name']} δεν είναι καταρτισμένος/η "
                              f"για το επίπεδο {c['level']}.")
            c["pinned_teacher"] = t["id"]
            changes.append(f"σταθερός καθηγητής → {t['name']} 📌")
        else:
            c.pop("pinned_teacher", None)
            changes.append("αφαίρεση σταθερού καθηγητή")
    if "pin_slot" in op:
        d, ps = norm_day(op["pin_slot"]["day"]), norm_time(op["pin_slot"].get("start"))
        slots = [s for s in c.get("pinned_slots", []) if s["day"] != d]
        slots.append({"day": d, "start": ps})
        c["pinned_slots"] = slots
        changes.append(f"σταθερή ώρα → {GREEK_DAYS[d]} {tick_label(ps)} 📌")
    if "clear_pinned_slots" in op:
        c.pop("pinned_slots", None)
        changes.append("αφαίρεση σταθερών ωρών")
    if not changes:
        raise OpError(f"update_class για {c['name']}: δεν δόθηκαν αναγνωρίσιμα πεδία.")
    return f"Ενημέρωση τμήματος {c['name']} ({c['id']}): " + "· ".join(changes)


def _op_remove_class(school, op):
    c = _find(school["classes"], op.get("class"), "class")
    school["classes"].remove(c)
    school["students"] = [s for s in school["students"] if s["class_id"] != c["id"]]
    return f"Αφαίρεση τμήματος {c['name']} ({c['id']}) και των εγγραφών των μαθητών του"


def _op_add_student(school, op):
    name = op.get("name")
    if not name:
        raise OpError("Το add_student χρειάζεται όνομα.")
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
    desc = f"Προσθήκη μαθητή/τριας {s['name']} ({s['id']}) στο {cls['name']}"
    if s["sibling_group"]:
        desc += f", ομάδα αδελφών {s['sibling_group']}"
    if s["blocked_windows"]:
        desc += ", μη διαθέσιμος/η: " + ", ".join(window_label(w) for w in s["blocked_windows"])
    return desc


def _op_update_student(school, op):
    s = _find(school["students"], op.get("student"), "student")
    changes = []
    if "name" in op:
        s["name"] = str(op["name"]); changes.append(f"όνομα → {s['name']}")
    if "class" in op:
        cls = _find(school["classes"], op["class"], "class")
        s["class_id"] = cls["id"]; changes.append(f"τμήμα → {cls['name']}")
    if "sibling_group" in op:
        s["sibling_group"] = op["sibling_group"] or None
        changes.append(f"ομάδα αδελφών → {s['sibling_group'] or 'καμία'}")
    if "blocked_windows" in op:
        s["blocked_windows"] = norm_windows(op["blocked_windows"])
        changes.append("μη διαθέσιμος/η: "
                       + (", ".join(window_label(w) for w in s["blocked_windows"]) or "ποτέ"))
    if "add_blocked_windows" in op:
        s["blocked_windows"] = s.get("blocked_windows", []) + norm_windows(op["add_blocked_windows"])
        changes.append("μη διαθέσιμος/η: " + ", ".join(window_label(w) for w in s["blocked_windows"]))
    if "note" in op:
        s["note"] = str(op["note"]); changes.append(f"σημείωση → {s['note']}")
    if not changes:
        raise OpError(f"update_student για {s['name']}: δεν δόθηκαν αναγνωρίσιμα πεδία.")
    return f"Ενημέρωση μαθητή/τριας {s['name']} ({s['id']}): " + "· ".join(changes)


def _op_remove_student(school, op):
    s = _find(school["students"], op.get("student"), "student")
    school["students"].remove(s)
    return f"Αφαίρεση μαθητή/τριας {s['name']} ({s['id']})"


def _op_set_day_hours(school, op):
    day = norm_day(op.get("day"))
    loc = _find_location(school, op.get("location"))
    day_name = DAYS[day]
    school["day_hours"].setdefault(day_name, {})
    if op.get("closed") or (op.get("open") is None and op.get("close") is None):
        school["day_hours"][day_name][loc] = None
        return f"Κλειστό το κτήριο {loc} την {GREEK_DAYS_FULL[day]}"
    o, c = norm_time(op.get("open")), norm_time(op.get("close"))
    if o >= c:
        raise OpError(f"set_day_hours {GREEK_DAYS_FULL[day]}/{loc}: "
                      "το άνοιγμα πρέπει να προηγείται του κλεισίματος.")
    school["day_hours"][day_name][loc] = {"open": o, "close": c}
    return f"Ωράριο {GREEK_DAYS_FULL[day]} στο {loc}: {tick_label(o)}-{tick_label(c)}"


def _op_update_settings(school, op):
    st = school["settings"]
    changes = []
    if "travel_periods" in op:
        st["travel_periods"] = int(op["travel_periods"])
        changes.append(f"περίοδοι μετακίνησης → {st['travel_periods']}")
    if "young_learner_cutoff" in op:
        st["young_learner_cutoff"] = norm_time(op["young_learner_cutoff"])
        changes.append(f"όριο λήξης μικρών μαθητών → {tick_label(st['young_learner_cutoff'])}")
    if not changes:
        raise OpError("update_settings: δεν δόθηκαν αναγνωρίσιμα πεδία "
                      "(travel_periods, young_learner_cutoff).")
    return "Ενημέρωση ρυθμίσεων: " + "· ".join(changes)


HANDLERS = {
    "add_teacher":     _op_add_teacher,
    "update_teacher":  _op_update_teacher,
    "remove_teacher":  _op_remove_teacher,
    "add_room":        _op_add_room,
    "update_room":     _op_update_room,
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
        return new_school, preview, ["Οι λειτουργίες πρέπει να είναι λίστα."]

    for i, op in enumerate(operations):
        if not isinstance(op, dict) or "op" not in op:
            errors.append(f"Λειτουργία {i + 1}: λείπει το πεδίο 'op'.")
            continue
        handler = HANDLERS.get(op["op"])
        if handler is None:
            errors.append(f"Λειτουργία {i + 1}: άγνωστη λειτουργία '{op['op']}'.")
            continue
        try:
            preview.append(handler(new_school, op))
        except OpError as e:
            errors.append(f"Λειτουργία {i + 1} ({op['op']}): {e}")
        except (KeyError, TypeError, ValueError) as e:
            errors.append(f"Λειτουργία {i + 1} ({op['op']}): μη έγκυρη τιμή ({e}).")

    return new_school, preview, errors
