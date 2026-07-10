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

**v0.3 — solver core + REST API + web UI + local AI assistant.**

- [x] **Phase 1** — CP-SAT solver core with realistic constraints
- [x] **Phase 2** — FastAPI wrapper (REST endpoints, JSON persistence)
- [x] **Phase 3** — Web UI (visual weekly grid, data entry, AI chat)
- [ ] **Phase 4** — Conflict explanations, PDF / Google Calendar export,
      Saturday-preference flag, Docker image

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

### Enabling the AI assistant (free, runs on your own machine)

1. Install [Ollama](https://ollama.com) (macOS / Windows / Linux).
2. Pull the default model: `ollama pull qwen2.5:7b` (~4.7 GB, needs ~8 GB RAM).
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
| `LLM_MODEL` | `qwen2.5:7b` | `qwen2.5:3b` is faster on weak machines |
| `LLM_API_KEY` | `ollama` | only needed for hosted providers |

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

The solver minimises total penalty. A score of **0** means every preference
was satisfied as well as every hard rule.

---

## Architecture

```
AI-Scheduler/
├── scheduler/               # the deterministic engine
│   ├── data.py              #   data model + sample school (default_school())
│   ├── solver.py            #   CP-SAT model — solve(school_dict)
│   └── report.py            #   CLI report (python -m scheduler.report)
├── app/                     # the interactive app
│   ├── main.py              #   FastAPI: REST API + serves the UI
│   ├── store.py             #   JSON persistence + validation (data/school.json)
│   ├── operations.py        #   structured edit operations (validate/preview/apply)
│   ├── assistant.py         #   LLM client (Ollama / any OpenAI-compatible API)
│   └── static/              #   single-page UI (no build step)
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
