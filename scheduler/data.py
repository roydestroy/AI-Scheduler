"""
Language School Scheduler — Data Model v2
==========================================
Time is modelled in 15-minute ticks from a base of 00:00.
  tick(h, m) = h * 4 + m // 15
  E.g. 15:30 → 62,  17:45 → 71,  21:00 → 84

One "period" = 50 min teaching + 10 min break = 60 min = 4 ticks.
A class with `periods_per_session = N` occupies N×4 = N×60min of
consecutive ticks in the same room with the same teacher.

Day index: Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5
"""

from __future__ import annotations

# ── tick helpers ──────────────────────────────────────────────────────────────

TICKS_PER_HOUR   = 4          # 1 tick = 15 min
TICKS_PER_PERIOD = 4          # 1 period = 60 min = 4 ticks (50 min + 10 min break)
DAYS             = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

def tick(h: int, m: int = 0) -> int:
    """Absolute tick from midnight."""
    return h * TICKS_PER_HOUR + m // 15

def tick_label(t: int) -> str:
    h = t // TICKS_PER_HOUR
    m = (t % TICKS_PER_HOUR) * 15
    return f"{h:02d}:{m:02d}"


# ── Locations ─────────────────────────────────────────────────────────────────

LOCATIONS = {
    "A": "School Alpha (Main)",
    "B": "School Beta (Branch)",
}

TRAVEL_PERIODS = 2   # teacher needs 2 consecutive free periods (≥ 80 min) to travel

# ── Per-day operating windows (configurable) ──────────────────────────────────
# open / close are absolute ticks.
# friday_overflow = True means Fri is low-priority (soft penalty).
# Each location can have its own hours.

DAY_HOURS = {
    #  day    location  open           close          notes
    ("Mon", "A"): {"open": tick(15, 30), "close": tick(21,  0)},
    ("Mon", "B"): {"open": tick(15, 30), "close": tick(21,  0)},
    ("Tue", "A"): {"open": tick(15, 30), "close": tick(21,  0)},
    ("Tue", "B"): {"open": tick(15, 30), "close": tick(21,  0)},
    ("Wed", "A"): {"open": tick(15, 30), "close": tick(21,  0)},
    ("Wed", "B"): {"open": tick(15, 30), "close": tick(21,  0)},
    ("Thu", "A"): {"open": tick(15, 30), "close": tick(21,  0)},
    ("Thu", "B"): {"open": tick(15, 30), "close": tick(21,  0)},
    ("Fri", "A"): {"open": tick(15, 30), "close": tick(21,  0), "overflow": True},
    ("Fri", "B"): {"open": tick(15, 30), "close": tick(21,  0), "overflow": True},
    ("Sat", "A"): None,                                           # School A closed Sat
    ("Sat", "B"): {"open": tick(10,  0), "close": tick(14,  0)},
}

# Young-learner cutoff: Junior A/B/Elementary must END by this tick
YOUNG_LEARNER_CUTOFF = tick(20, 0)   # 20:00

# ── Valid day-pair rules for sessions_per_week ────────────────────────────────
# 2×/week → only Mon/Wed or Tue/Thu  (indices 0/2 or 1/3)
# 3×/week → Mon/Wed/Fri or Tue/Thu + one of (Mon,Wed,Fri)
# Fri (index 4) is allowed but penalised.

DAY_PAIRS_2X = [
    (0, 2),   # Mon / Wed
    (1, 3),   # Tue / Thu
]

DAY_TRIPLETS_3X = [
    (0, 2, 4),   # Mon / Wed / Fri
    (1, 3, 0),   # Tue / Thu / Mon
    (1, 3, 2),   # Tue / Thu / Wed
    (1, 3, 4),   # Tue / Thu / Fri
]

# ── Rooms ─────────────────────────────────────────────────────────────────────

ROOMS = [
    {"id": "R1", "location": "A", "name": "Alpha-1"},
    {"id": "R2", "location": "A", "name": "Alpha-2"},
    {"id": "R3", "location": "A", "name": "Alpha-3"},
    {"id": "R4", "location": "B", "name": "Beta-1"},
    {"id": "R5", "location": "B", "name": "Beta-2"},
]

# ── Teachers ──────────────────────────────────────────────────────────────────
# available_days: set of day-indices they work (None = all days except Fri by default)
# blocked_windows: list of (day, open_tick, close_tick) absolute ticks unavailable

TEACHERS = [
    {"id": "T1",  "name": "Anna",     "home": "A",
     "qualified_levels": ["Junior A", "Junior B", "Elementary", "Pre-Intermediate"],
     "available_days": {0,1,2,3,5}, "blocked_windows": []},
    {"id": "T2",  "name": "Petros",   "home": "A",
     "qualified_levels": ["Intermediate", "Upper-Intermediate", "B2"],
     "available_days": {0,1,2,3,5}, "blocked_windows": []},
    {"id": "T3",  "name": "Maria",    "home": "B",
     "qualified_levels": ["B2", "C1", "CPE"],
     "available_days": {0,1,2,3,5}, "blocked_windows": []},
    {"id": "T4",  "name": "Nikos",    "home": "A",
     "qualified_levels": ["Junior A", "Junior B", "Elementary"],
     "available_days": {0,1,2,3,5}, "blocked_windows": []},
    {"id": "T5",  "name": "Elena",    "home": "B",
     "qualified_levels": ["Pre-Intermediate", "Intermediate", "Upper-Intermediate"],
     "available_days": {0,1,2,3,5}, "blocked_windows": []},
    {"id": "T6",  "name": "Kostas",   "home": "A",
     "qualified_levels": ["Elementary", "Pre-Intermediate", "Intermediate"],
     "available_days": {0,1,2,3,5}, "blocked_windows": []},
    {"id": "T7",  "name": "Sofia",    "home": "B",
     "qualified_levels": ["Junior A", "Junior B"],
     "available_days": {0,1,2,3,5}, "blocked_windows": []},
    {"id": "T8",  "name": "Dimitris", "home": "A",
     "qualified_levels": ["Upper-Intermediate", "B2", "C1", "CPE"],
     "available_days": {0,1,2,3,5}, "blocked_windows": []},
    {"id": "T9",  "name": "Ioanna",   "home": "B",
     "qualified_levels": ["Elementary", "Pre-Intermediate", "Intermediate", "B2"],
     "available_days": {0,1,2,3,5}, "blocked_windows": []},
    {"id": "T10", "name": "Thanasis", "home": "A",
     "qualified_levels": ["Intermediate", "Upper-Intermediate", "B2", "C1"],
     "available_days": {0,1,2,3,5}, "blocked_windows": []},
]

# ── Class levels ──────────────────────────────────────────────────────────────

YOUNG_LEARNER_LEVELS = {"Junior A", "Junior B", "Elementary"}

LEVELS = [
    "Junior A", "Junior B", "Elementary",
    "Pre-Intermediate", "Intermediate", "Upper-Intermediate",
    "B2", "C1", "CPE",
]

# ── Classes ───────────────────────────────────────────────────────────────────
# periods_per_session : how many 60-min periods each session lasts
# sessions_per_week   : 2 or 3
# preferred_location  : soft preference

CLASSES = [
    # ── Young learners (2 periods × 2/week) ──────────────────────────────────
    {"id": "C01", "name": "Junior A Gr.1",          "level": "Junior A",
     "periods_per_session": 2, "sessions_per_week": 2, "preferred_location": "A"},
    {"id": "C02", "name": "Junior A Gr.2",          "level": "Junior A",
     "periods_per_session": 2, "sessions_per_week": 2, "preferred_location": "B"},
    {"id": "C03", "name": "Junior B Gr.1",          "level": "Junior B",
     "periods_per_session": 2, "sessions_per_week": 2, "preferred_location": "A"},
    {"id": "C04", "name": "Elementary Gr.1",        "level": "Elementary",
     "periods_per_session": 2, "sessions_per_week": 2, "preferred_location": "A"},
    {"id": "C05", "name": "Elementary Gr.2",        "level": "Elementary",
     "periods_per_session": 2, "sessions_per_week": 2, "preferred_location": "B"},

    # ── General English (2 periods × 2/week) ─────────────────────────────────
    {"id": "C06", "name": "Pre-Intermediate Gr.1",  "level": "Pre-Intermediate",
     "periods_per_session": 2, "sessions_per_week": 2, "preferred_location": "A"},
    {"id": "C07", "name": "Intermediate Gr.1",      "level": "Intermediate",
     "periods_per_session": 2, "sessions_per_week": 2, "preferred_location": "A"},
    {"id": "C08", "name": "Intermediate Gr.2",      "level": "Intermediate",
     "periods_per_session": 2, "sessions_per_week": 2, "preferred_location": "B"},
    {"id": "C09", "name": "Upper-Int. Gr.1",        "level": "Upper-Intermediate",
     "periods_per_session": 2, "sessions_per_week": 2, "preferred_location": "A"},

    # ── Diploma classes (2 periods × 3/week = 6h total) ──────────────────────
    {"id": "C10", "name": "B2 Gr.1",               "level": "B2",
     "periods_per_session": 2, "sessions_per_week": 3, "preferred_location": "A"},
    {"id": "C11", "name": "B2 Gr.2",               "level": "B2",
     "periods_per_session": 2, "sessions_per_week": 3, "preferred_location": "B"},
    {"id": "C12", "name": "C1 Gr.1",               "level": "C1",
     "periods_per_session": 2, "sessions_per_week": 3, "preferred_location": "A"},
    {"id": "C13", "name": "CPE Gr.1",              "level": "CPE",
     "periods_per_session": 2, "sessions_per_week": 3, "preferred_location": "A"},
]

# ── Students ──────────────────────────────────────────────────────────────────
# blocked_windows: list of (day_index, start_tick, end_tick) — absolute ticks
#   meaning the student cannot attend any session that overlaps this window.

def blocked(day: int, h_start: int, h_end: int):
    """Shorthand: block a whole window on a given day."""
    return (day, tick(h_start), tick(h_end))

STUDENTS = [
    # Sibling group 1: Alexis (C01) + Stavros (C04)
    {"id": "S01", "name": "Alexis",   "class_id": "C01", "sibling_group": "SG1",
     "blocked_windows": [], "note": ""},
    {"id": "S02", "name": "Stavros",  "class_id": "C04", "sibling_group": "SG1",
     "blocked_windows": [], "note": ""},

    # Sibling group 2: Niki (C02) + Pantelis (C07)
    {"id": "S03", "name": "Niki",     "class_id": "C02", "sibling_group": "SG2",
     "blocked_windows": [], "note": ""},
    {"id": "S04", "name": "Pantelis", "class_id": "C07", "sibling_group": "SG2",
     "blocked_windows": [], "note": ""},

    # Individual constraints
    {"id": "S05", "name": "Katerina", "class_id": "C10", "sibling_group": None,
     "blocked_windows": [blocked(0, 16, 18)],
     "note": "Piano lesson Mon 16:00-18:00"},
    {"id": "S06", "name": "Giorgos",  "class_id": "C12", "sibling_group": None,
     "blocked_windows": [blocked(2, 18, 20)],
     "note": "Football training Wed 18:00-20:00"},
    {"id": "S07", "name": "Dimitra",  "class_id": "C13", "sibling_group": None,
     "blocked_windows": [blocked(4, 15, 19)],
     "note": "Works Fri until 19:00"},
]
