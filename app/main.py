"""
Language School Scheduler — REST API + web UI.

Run from the repo root:
    uvicorn app.main:app --reload

Then open http://localhost:8000
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from scheduler.solver import solve
from . import assistant, operations, store

app = FastAPI(title="Language School Scheduler", version="0.3")


# ── request models ────────────────────────────────────────────────────────────

class SolveRequest(BaseModel):
    time_limit_seconds: int = Field(default=60, ge=1, le=600)


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = Field(default_factory=list)


class ApplyRequest(BaseModel):
    operations: list[dict]
    resolve: bool = False
    time_limit_seconds: int = Field(default=60, ge=1, le=600)


# ── school data ───────────────────────────────────────────────────────────────

@app.get("/api/school")
def get_school():
    return store.load_school()


@app.put("/api/school")
def put_school(school: dict):
    errors = store.validate_school(school)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    store.save_school(school)
    return {"ok": True, "school": school}


@app.post("/api/school/reset")
def reset_school():
    return {"ok": True, "school": store.reset_school()}


# ── solving ───────────────────────────────────────────────────────────────────

@app.post("/api/solve")
def post_solve(req: SolveRequest = SolveRequest()):
    school = store.load_school()
    errors = store.validate_school(school)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    result = solve(school, time_limit_seconds=req.time_limit_seconds)
    store.save_last_schedule(result)
    return result


@app.get("/api/schedule")
def get_schedule():
    return store.load_last_schedule() or {"status": None, "schedule": [], "warnings": []}


# ── AI assistant ──────────────────────────────────────────────────────────────

@app.get("/api/assistant/status")
def assistant_status():
    return assistant.status()


@app.post("/api/assistant/chat")
def assistant_chat(req: ChatRequest):
    school = store.load_school()
    try:
        out = assistant.chat(school, req.message, req.history,
                             schedule=store.load_last_schedule())
    except assistant.AssistantError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Dry-run the proposed operations so the user sees a preview (or errors)
    _, preview, errors = operations.apply_operations(school, out["operations"])
    return {
        "reply": out["reply"],
        "operations": out["operations"],
        "preview": preview,
        "errors": errors,
    }


@app.post("/api/assistant/apply")
def assistant_apply(req: ApplyRequest):
    school = store.load_school()
    new_school, preview, errors = operations.apply_operations(school, req.operations)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})

    val_errors = store.validate_school(new_school)
    if val_errors:
        raise HTTPException(status_code=422, detail={"errors": val_errors})

    store.save_school(new_school)
    response = {"ok": True, "preview": preview, "school": new_school, "result": None}

    if req.resolve:
        result = solve(new_school, time_limit_seconds=req.time_limit_seconds)
        store.save_last_schedule(result)
        response["result"] = result
    return response


# ── static UI (must be mounted last) ──────────────────────────────────────────

static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
