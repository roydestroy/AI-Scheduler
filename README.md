# Language School Scheduler

An interactive weekly-timetabling app for a multi-location language school.

Tell it what changed — *“Maria can't work Tuesdays anymore”*, *“new student
Eleni joins B2 Gr.1, she has dance Wednesdays 5–7pm”* — and it updates the
data and recomputes the whole week's schedule.

Under the hood it combines two engines with very different jobs:

- **[Google OR-Tools](https://developers.google.com/optimization) CP-SAT
  solver** — computes the actual timetable. Deterministic and explainable:
  it can never silently violate a hard constraint.
- **A local AI model (via [Ollama](https://ollama.com), free of charge)** —
  only translates your natural-language requests into structured data edits,
  which you confirm before anything is applied. The AI never invents
  schedules, so a small free model is plenty.

The AI layer is optional: the whole app also works through normal forms.

---

## Status

**v0.4 — feature-complete against the original roadmap.**

- [x] **Phase 1** — CP-SAT solver core with realistic constraints
- [x] **Phase 2** — FastAPI wrapper (REST endpoints, JSON persistence)
- [x] **Phase 3** — Web UI (visual weekly grid, data entry, AI chat)
- [x] **Phase 4** — Conflict explanations, PDF & calendar (.ics) export,
      Saturday-preference flag, Docker image

Possible future work: user accounts, drag-and-drop manual overrides,
direct Google Calendar API sync (the .ics export covers the import path).

---

## Quick start

```bash
# 1. Install dependencies (a virtualenv is recommended)
pip install -r requirements.txt

# 2. Start the web app (from the repo root)
uvicorn app.main:app --reload

# 3. Open http://localhost:8000  →  press “Solve”
```

The app is seeded with a realistic sample school (2 branches, 5 rooms,
10 teachers, 13 classes). Your edits are stored in `data/school.json`.

There is also a plain CLI report:

```bash
python -m scheduler.report
```

### Or run everything with Docker (app + AI in one command)

```bash
docker compose up -d
docker compose exec ollama ollama pull qwen2.5:3b   # once (~1.9 GB)
# open http://localhost:8000
```

### Enabling the AI assistant (free, runs on your own machine)

1. Install [Ollama](https://ollama.com) (macOS / Windows / Linux).
2. Pull the default model: `ollama pull qwen2.5:3b` (~1.9 GB — runs on any
   ordinary laptop). On a machine with ≥16 GB RAM, `qwen2.5:7b` gives
   noticeably better answers (set `LLM_MODEL=qwen2.5:7b`).
3. That's it — the app finds it at `http://localhost:11434` automatically.

The **Assistant** tab then accepts requests like:

> “Close branch B on Saturdays” · “Add a room Beta-3 at branch B” ·
> “Kostas is also qualified for B2 now” · “The Petrov siblings can't come
> before 5pm”

The model proposes concrete changes, the UI shows you exactly what would
change, and only after you click **Apply & re-solve** does the solver
recompute the week.

Any OpenAI-compatible endpoint works — configure with environment variables:

| Variable | Default | Notes |
|---|---|---|
| `LLM_BASE_URL` | `http://localhost:11434/v1` | Ollama, LM Studio, llama.cpp, Groq, OpenRouter… |
| `LLM_MODEL` | `qwen2.5:3b` | `qwen2.5:7b` for better quality on ≥16 GB machines |
| `LLM_API_KEY` | `ollama` | only needed for hosted providers |

---

## Running it for the whole school (multiple PCs / branches)

The app is a web server — install it on **one** always-on PC and every other
computer just opens a browser. Nothing to install on the other machines.

1. On the host PC, start it listening on the network — double-click
   **`start_scheduler.bat`** (Windows), or run
   `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
2. Allow the port through the host's firewall (once, as administrator):
   `netsh advfirewall firewall add rule name="School Scheduler" dir=in action=allow protocol=TCP localport=8000`
3. Other PCs browse to `http://<host-ip>:8000`. Branches connected via
   [Tailscale](https://tailscale.com) use the host's Tailscale IP
   (`tailscale ip -4` on the host).
4. **Set a password** as soon as other PCs connect: set the `APP_PASSWORD`
   environment variable (see `start_scheduler.bat`) — everyone then logs in
   once per browser; without it anyone on the network can edit the schedule.

Working together is safe by design:

- **Conflict guard** — if two people edit the school data at the same time,
  the second save is rejected with a clear "someone else changed the data,
  reload" message instead of silently overwriting the first.
- **Auto-sync** — every open browser picks up changes made elsewhere within
  ~20 seconds: new data, new solves, ERP imports. The front desk at branch B
  sees the re-solved schedule without touching anything.

---

## Workspaces, year-to-year mirroring, and undo

**Workspaces** (header dropdown) are independent datasets + schedules —
one per academic year. Create next year's with the **＋** button and choose
*mirror*: it copies the whole configuration and keeps the current schedule
as a **stability baseline**. When you then solve the new year, S4/S5 pull
every class towards its old day, time and teacher, so families keep their
familiar slots wherever the new constraints allow — and only genuinely
conflicting classes move.

The baseline follows the *students*, not the class name: after you import
the new academic period from the ERP, each class inherits the old slot of
the class most of its students came from — this year's EJ2 group keeps its
Tuesday 17:30 when it becomes next year's EJ3.

**Undo** (header button) reverts the last change — a manual edit, an AI
change, an ERP import, or a re-solve. The last 30 states are kept per
workspace.

**Schedule tools** (toolbar): 🔍 **free-slot finder** (when is a teacher or
room free this week?), a **focus lens** search box (dims everything except a
teacher's or student's sessions), a **Προεπισκόπηση** toggle (dry-run any
change and confirm before it commits), and a **⋯ Περισσότερα** menu with
**parent notice slips** (per class or per student, Greek PDF) and **backup
download**. Backups are also taken automatically before every save
(`data/backups/`, last 20 kept).

**Change tracking**: every solve is compared to the previous one. The
status bar shows a summary ("35 unchanged · 2 teacher changes"), an
expandable list spells out each difference ("B2 Gr.1: teacher Petros →
Maria (Tue 17:30)"), and the affected blocks are outlined in the weekly
grid. Re-solves are also *sticky*: the solver tie-breaks towards the
previous schedule (S6, weight 1 for time/teacher/room), so nothing moves
without an actual reason and the change list stays short and meaningful.

---

## Importing students from your ERP (SQL Server)

If your student records live in a SQL Server (Express) database, the app can
pull them in directly — students, level codes and sibling groups — instead of
manual entry. Each branch's database is one import *source* bound to one
location, so a multi-branch school imports each branch separately into the
same shared dataset.

Setup:

1. Copy `erp_sources.example.json` to `data/erp_sources.json` and edit the
   connection strings (Windows auth locally; SQL auth + TCP/IP for a remote
   branch over VPN/Tailscale).
2. In the web UI → **School Data** → **Import from ERP** → press *Preview*.
3. Pick the **academic period** to import from the dropdown — the ERP's
   "current" period is only the preselected default, never an implicit choice.
4. Review the level-code mapping table (import yes/no, hours per week,
   young-learner flag — remembered per code) and the change list
   (new / moved / removed students), then *Apply import*.

Notes:

- Reads are **strictly read-only** (SELECTs over
  `Students` + `Enrollments` + `AcademicPeriods` for the period you choose).
- Your ERP's level codes become the app's levels — qualify teachers per code.
- Re-running an import updates that branch only; manually added students and
  any blocked-time windows you set on imported students are preserved.

---

## How the scheduling works

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
| H8 | Per-student and per-teacher blocked time windows are respected |
| H9 | Siblings share at least one common day each week |

### Soft constraints (optimised)
| # | Rule | Penalty |
|---|------|---------|
| S1 | Keep each class at its preferred location | 3 / violation |
| S2 | Avoid Friday sessions (reserved for overflow / private lessons) | 2 / session |
| S3 | Classes flagged `saturday_preferred` should use a Saturday slot | 5 / weekday session |
| S4 | Schedule stability: keep each class at last year's day + time | 4 / session |
| S5 | Schedule stability: keep the same teacher at that same slot | 2 / session |
| S6 | Sticky re-solve: tie-break towards the previous solve (time/teacher/room) | 1 / session |
| S7 | Balance teacher load: minimise the busiest teacher's weekly hours | 1 / peak period |

Additional hard constraints you can set from the schedule: **pinned teacher**
(drag a teacher onto a class), **pinned time** (drag a class block to a slot),
**teacher max hours/week**, and **no time-overlap for a student enrolled in two
classes** (H10, automatic). All appear in the **🔒 Περιορισμοί** panel and are
removable with one click.

The solver minimises total penalty. After each solve the status bar shows
which preferences ended up **unmet** — an expandable list naming each class
that landed at the wrong location, on a Friday, or (for Saturday-preferring
classes) off Saturday, with the cost of each. "✓ όλες οι προτιμήσεις
ικανοποιούνται" means a perfect score. (Load-balancing is a silent
tie-breaker and is deliberately excluded from this figure, so 0 really does
mean every stated preference was met.)

### When no schedule exists

The app doesn't just say "infeasible" — it diagnoses the conflict:
static checks (missing qualifications, capacity arithmetic) plus
*relaxation probes*: it re-solves with one rule switched off at a time
and reports which rule is the culprit, e.g. *“a schedule EXISTS if you
relax the young-learner end-time cutoff (H6)”*. The findings appear in
the UI and are passed to the AI assistant so you can discuss fixes in
plain language.

---

## Architecture

```
AI-Scheduler/
├── scheduler/               # the deterministic engine
│   ├── data.py              #   data model + sample school (default_school())
│   ├── solver.py            #   CP-SAT model — solve(school_dict, relax=…)
│   ├── diagnose.py          #   infeasibility explanations (relaxation probes)
│   └── report.py            #   CLI report (python -m scheduler.report)
├── app/                     # the interactive app
│   ├── main.py              #   FastAPI: REST API + serves the UI
│   ├── store.py             #   JSON persistence + validation (data/school.json)
│   ├── operations.py        #   structured edit operations (validate/preview/apply)
│   ├── assistant.py         #   LLM client (Ollama / any OpenAI-compatible API)
│   ├── export.py            #   PDF + iCalendar (.ics) generation
│   └── static/              #   single-page UI (no build step)
├── Dockerfile / docker-compose.yml   # app + Ollama, one command
└── requirements.txt
```

The AI safety model in one sentence: **the model returns operations, not
data** — every proposal is validated against the current school, previewed
to you in plain language, and applied only on confirmation, so a wrong or
hallucinated answer can never corrupt the schedule.

### REST API

| Method & path | Purpose |
|---|---|
| `GET /api/school` | full school configuration |
| `PUT /api/school` | replace configuration (validated) |
| `POST /api/school/reset` | restore the sample dataset |
| `POST /api/solve` | run the solver, returns the schedule |
| `GET /api/schedule` | last solved schedule |
| `GET /api/export/pdf` | printable weekly timetable + per-teacher pages |
| `GET /api/export/ics` | recurring calendar events (import into Google/Outlook/Apple) |
| `GET /api/assistant/status` | is the LLM reachable / model installed? |
| `POST /api/assistant/chat` | message → `{reply, operations, preview, errors}` |
| `POST /api/assistant/apply` | apply confirmed operations (optionally re-solve) |

---

## Notes

- Sample data is intentionally light, so the solver finishes in well under a
  second. With a full real dataset, overflow days are used automatically as
  capacity tightens.
- Everything runs locally: the schedule data never leaves your machine, and
  with Ollama neither do the assistant conversations.
