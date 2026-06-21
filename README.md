# Language School Scheduler

A constraint-based weekly timetabling engine for a multi-location language
school, built with [Google OR-Tools](https://developers.google.com/optimization)
(CP-SAT solver).

It automatically generates a weekly schedule that respects teacher
qualifications, room availability, travel time between branches, sibling
grouping, per-student constraints, and configurable class durations and
frequencies.

---

## Status

**v0.2 — solver core (proof of concept).**
The engine solves a realistic sample dataset to optimality. The next phases
are a REST API wrapper (FastAPI) and a web UI.

---

## How it works

### Time model
- Time is represented in **15-minute ticks** from midnight, so sessions can
  start at any quarter-hour (15:30, 17:45, etc.).
- **1 period = 50 min teaching + 10 min break = 60 min** (4 ticks).
- A class with `periods_per_session = N` occupies `N` consecutive 60-min
  blocks in one room, with one teacher.

### What the solver decides
For each session of each class it chooses a **day, start time, room, and
teacher** simultaneously, optimising the whole week at once.

### Hard constraints (never violated)
| # | Rule |
|---|------|
| H1 | Each class meets exactly `sessions_per_week` times |
| H2 | Day patterns: 2×/week → Mon/Wed or Tue/Thu; 3×/week → valid triplets |
| H3 | No two classes share a room at overlapping times |
| H4 | No teacher is double-booked |
| H5 | Sessions fit inside each location's per-day operating hours |
| H6 | Young-learner classes (Junior/Elementary) end by a configurable cutoff |
| H7 | Teacher travel: ≥ 2 free periods required to switch branches mid-day |
| H8 | Per-student blocked time windows are respected |
| H9 | Siblings share at least one common day each week |

### Soft constraints (optimised)
| # | Rule | Penalty |
|---|------|---------|
| S1 | Keep each class at its preferred location | 3 / violation |
| S2 | Avoid Friday sessions (reserved for overflow / private lessons) | 2 / session |

The solver minimises total penalty. A score of **0** means every preference
was satisfied as well as every hard rule.

---

## Project structure

```
omr-scheduler/
├── README.md
├── requirements.txt
├── .gitignore
└── scheduler/
    ├── __init__.py
    ├── data.py      # All school data + configuration (teachers, rooms, classes, students)
    ├── solver.py    # CP-SAT model and solve() function
    └── report.py    # Human-readable schedule printout + verification checks
```

---

## Quick start

```bash
# 1. Install dependencies (a virtualenv is recommended)
pip install -r requirements.txt

# 2. Run the solver on the sample data
cd scheduler
python report.py
```

You'll get a daily grid, per-teacher schedules, and automatic verification of
sibling grouping and student constraints.

---

## Configuring your own school

Everything lives in `scheduler/data.py`:

- **`LOCATIONS`** — your branches.
- **`DAY_HOURS`** — operating window per `(day, location)`. Set to `None` to
  close a branch on a given day (e.g. School A is closed Saturdays).
- **`TEACHERS`** — name, home branch, qualified levels, available days,
  blocked time windows.
- **`ROOMS`** — id, location, name.
- **`CLASSES`** — level, `periods_per_session`, `sessions_per_week`,
  preferred location.
- **`STUDENTS`** — class assignment, sibling group, blocked time windows.

Helper functions `tick(h, m)` and `blocked(day, h_start, h_end)` make it easy
to express times and constraints.

---

## Roadmap

- [x] **Phase 1** — CP-SAT solver core with realistic constraints
- [ ] **Phase 2** — FastAPI wrapper (REST endpoints, JSON in/out, Dockerised)
- [ ] **Phase 3** — React web UI (data entry + visual weekly grid)
- [ ] **Phase 4** — Optional AI layer (natural-language constraint entry &
      conflict explanations via the Claude API)
- [ ] Saturday-preference flag for classes that suit weekend slots
- [ ] Export to PDF / Google Calendar

---

## Notes

- Sample data is intentionally light (13 classes, 10 teachers, 5 rooms), so
  the solver finishes in well under a second and doesn't need Friday/Saturday.
  With a full real dataset, overflow days will be used automatically as
  capacity tightens.
- The solver is deterministic and explainable — unlike a pure-LLM approach it
  cannot silently violate a hard constraint.
