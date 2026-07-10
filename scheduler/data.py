"""
Language School Scheduler — Data Model v3
==========================================
Time is modelled in 15-minute ticks from a base of 00:00.
  tick(h, m) = h * 4 + m // 15
  E.g. 15:30 → 62,  17:45 → 71,  21:00 → 84

One "period" = 50 min teaching + 10 min break = 60 min = 4 ticks.
A class with `periods_per_session = N` occupies N×4 = N×60min of
consecutive ticks in the same room with the same teacher.

Day index: Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5

Since v3 the whole school configuration is a single JSON-serialisable
dict (the "school" dict) built by `default_school()`. The solver takes
this dict as input, so the same engine can be driven by the CLI, the
REST API, or a file on disk.

School dict shape
-----------------
{
  "locations":  {loc_id: display_name},
  "settings": {
      "ticks_per_period":     int,
      "travel_periods":       int,
      "young_learner_cutoff": int (tick),
      "young_learner_levels": [level, ...],
      "levels":               [level, ...],
      "day_pairs_2x":         [[d, d], ...],
      "day_triplets_3x":      [[d, d, d], ...],
  },
  "day_hours": {day_name: {loc_id: {"open": tick, "close": tick} | None}},
  "rooms":     [{"id", "location", "name"}, ...],
  "teachers":  [{"id", "name", "home", "qualified_levels": [...],
                 "available_days": [d, ...],
                 "blocked_windows": [[day, open, close], ...]}, ...],
  "classes":   [{"id", "name", "level", "periods_per_session",
                 "sessions_per_week", "preferred_location"}, ...],
  "students":  [{"id", "name", "class_id", "sibling_group": str | None,
                 "blocked_windows": [[day, open, close], ...],
                 "note": str}, ...],
}
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

def parse_time(text: str) -> int:
    """'17:30' → tick. Accepts '17', '17:30', '5:30pm' is NOT supported."""
    text = text.strip()
    if ":" in text:
        h, m = text.split(":", 1)
    else:
        h, m = text, "0"
    return tick(int(h), int(m))


def blocked(day: int, h_start: int, h_end: int) -> list:
    """Shorthand: block a whole window on a given day (hours on the hour)."""
    return [day, tick(h_start), tick(h_end)]


# ── default sample school ─────────────────────────────────────────────────────

def default_school() -> dict:
    """A realistic sample dataset: 2 branches, 5 rooms, 10 teachers,
    13 classes, sibling groups and per-student constraints."""

    levels = [
        "Junior A", "Junior B", "Elementary",
        "Pre-Intermediate", "Intermediate", "Upper-Intermediate",
        "B2", "C1", "CPE",
    ]

    weekday_window = {"open": tick(15, 30), "close": tick(21, 0)}

    return {
        "locations": {
            "A": "School Alpha (Main)",
            "B": "School Beta (Branch)",
        },

        "settings": {
            "ticks_per_period":     TICKS_PER_PERIOD,
            "travel_periods":       2,             # ≥ 2 free periods to switch branch
            "young_learner_cutoff": tick(20, 0),   # Junior/Elementary end by 20:00
            "young_learner_levels": ["Junior A", "Junior B", "Elementary"],
            "levels":               levels,
            # 2×/week → Mon/Wed or Tue/Thu; 3×/week → listed triplets
            "day_pairs_2x":    [[0, 2], [1, 3]],
            "day_triplets_3x": [[0, 2, 4], [1, 3, 0], [1, 3, 2], [1, 3, 4]],
        },

        "day_hours": {
            "Mon": {"A": dict(weekday_window), "B": dict(weekday_window)},
            "Tue": {"A": dict(weekday_window), "B": dict(weekday_window)},
            "Wed": {"A": dict(weekday_window), "B": dict(weekday_window)},
            "Thu": {"A": dict(weekday_window), "B": dict(weekday_window)},
            "Fri": {"A": dict(weekday_window), "B": dict(weekday_window)},
            "Sat": {"A": None,                                            # A closed Sat
                    "B": {"open": tick(10, 0), "close": tick(14, 0)}},
        },

        "rooms": [
            {"id": "R1", "location": "A", "name": "Alpha-1", "capacity": 12},
            {"id": "R2", "location": "A", "name": "Alpha-2", "capacity": 10},
            {"id": "R3", "location": "A", "name": "Alpha-3", "capacity": 8},
            {"id": "R4", "location": "B", "name": "Beta-1", "capacity": 12},
            {"id": "R5", "location": "B", "name": "Beta-2", "capacity": 8},
        ],

        "teachers": [
            {"id": "T1",  "name": "Anna",     "home": "A",
             "qualified_levels": ["Junior A", "Junior B", "Elementary", "Pre-Intermediate"],
             "available_days": [0, 1, 2, 3, 5], "blocked_windows": []},
            {"id": "T2",  "name": "Petros",   "home": "A",
             "qualified_levels": ["Intermediate", "Upper-Intermediate", "B2"],
             "available_days": [0, 1, 2, 3, 5], "blocked_windows": []},
            {"id": "T3",  "name": "Maria",    "home": "B",
             "qualified_levels": ["B2", "C1", "CPE"],
             "available_days": [0, 1, 2, 3, 5], "blocked_windows": []},
            {"id": "T4",  "name": "Nikos",    "home": "A",
             "qualified_levels": ["Junior A", "Junior B", "Elementary"],
             "available_days": [0, 1, 2, 3, 5], "blocked_windows": []},
            {"id": "T5",  "name": "Elena",    "home": "B",
             "qualified_levels": ["Pre-Intermediate", "Intermediate", "Upper-Intermediate"],
             "available_days": [0, 1, 2, 3, 5], "blocked_windows": []},
            {"id": "T6",  "name": "Kostas",   "home": "A",
             "qualified_levels": ["Elementary", "Pre-Intermediate", "Intermediate"],
             "available_days": [0, 1, 2, 3, 5], "blocked_windows": []},
            {"id": "T7",  "name": "Sofia",    "home": "B",
             "qualified_levels": ["Junior A", "Junior B"],
             "available_days": [0, 1, 2, 3, 5], "blocked_windows": []},
            {"id": "T8",  "name": "Dimitris", "home": "A",
             "qualified_levels": ["Upper-Intermediate", "B2", "C1", "CPE"],
             "available_days": [0, 1, 2, 3, 5], "blocked_windows": []},
            {"id": "T9",  "name": "Ioanna",   "home": "B",
             "qualified_levels": ["Elementary", "Pre-Intermediate", "Intermediate", "B2"],
             "available_days": [0, 1, 2, 3, 5], "blocked_windows": []},
            {"id": "T10", "name": "Thanasis", "home": "A",
             "qualified_levels": ["Intermediate", "Upper-Intermediate", "B2", "C1"],
             "available_days": [0, 1, 2, 3, 5], "blocked_windows": []},
        ],

        "classes": [
            # ── Young learners (2 periods × 2/week) ──────────────────────────
            {"id": "C01", "name": "Junior A Gr.1",         "level": "Junior A",
             "periods_per_session": 2, "sessions_per_week": 2, "preferred_location": "A"},
            {"id": "C02", "name": "Junior A Gr.2",         "level": "Junior A",
             "periods_per_session": 2, "sessions_per_week": 2, "preferred_location": "B"},
            {"id": "C03", "name": "Junior B Gr.1",         "level": "Junior B",
             "periods_per_session": 2, "sessions_per_week": 2, "preferred_location": "A"},
            {"id": "C04", "name": "Elementary Gr.1",       "level": "Elementary",
             "periods_per_session": 2, "sessions_per_week": 2, "preferred_location": "A"},
            {"id": "C05", "name": "Elementary Gr.2",       "level": "Elementary",
             "periods_per_session": 2, "sessions_per_week": 2, "preferred_location": "B"},

            # ── General English (2 periods × 2/week) ─────────────────────────
            {"id": "C06", "name": "Pre-Intermediate Gr.1", "level": "Pre-Intermediate",
             "periods_per_session": 2, "sessions_per_week": 2, "preferred_location": "A"},
            {"id": "C07", "name": "Intermediate Gr.1",     "level": "Intermediate",
             "periods_per_session": 2, "sessions_per_week": 2, "preferred_location": "A"},
            {"id": "C08", "name": "Intermediate Gr.2",     "level": "Intermediate",
             "periods_per_session": 2, "sessions_per_week": 2, "preferred_location": "B"},
            {"id": "C09", "name": "Upper-Int. Gr.1",       "level": "Upper-Intermediate",
             "periods_per_session": 2, "sessions_per_week": 2, "preferred_location": "A"},

            # ── Diploma classes (2 periods × 3/week = 6h total) ──────────────
            {"id": "C10", "name": "B2 Gr.1",               "level": "B2",
             "periods_per_session": 2, "sessions_per_week": 3, "preferred_location": "A"},
            {"id": "C11", "name": "B2 Gr.2",               "level": "B2",
             "periods_per_session": 2, "sessions_per_week": 3, "preferred_location": "B"},
            {"id": "C12", "name": "C1 Gr.1",               "level": "C1",
             "periods_per_session": 2, "sessions_per_week": 3, "preferred_location": "A"},
            {"id": "C13", "name": "CPE Gr.1",              "level": "CPE",
             "periods_per_session": 2, "sessions_per_week": 3, "preferred_location": "A"},
        ],

        "students": [
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
        ],
    }
