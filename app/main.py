"""
Language School Scheduler — REST API + web UI.

Run from the repo root:
    uvicorn app.main:app --reload

Then open http://localhost:8000
"""

from __future__ import annotations

import copy
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from scheduler.diagnose import diagnose
from scheduler.solver import solve
from . import assistant, diff, erp, export, operations, store

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


# ── workspaces & undo ─────────────────────────────────────────────────────────

class WorkspaceCreateRequest(BaseModel):
    name: str
    seed_from_active: bool = False


class WorkspaceActivateRequest(BaseModel):
    key: str


@app.get("/api/workspaces")
def get_workspaces():
    return store.list_workspaces()


@app.post("/api/workspaces")
def post_workspace(req: WorkspaceCreateRequest):
    if not req.name.strip():
        raise HTTPException(status_code=422, detail="Workspace name is required.")
    ws = store.create_workspace(req.name.strip(), seed_from_active=req.seed_from_active)
    return {"ok": True, "created": ws, **store.list_workspaces()}


@app.post("/api/workspaces/activate")
def post_workspace_activate(req: WorkspaceActivateRequest):
    try:
        store.activate_workspace(req.key)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True, **store.list_workspaces()}


@app.get("/api/history")
def get_history():
    return {"items": store.history_list()}


@app.post("/api/undo")
def post_undo():
    snap = store.undo()
    if snap is None:
        raise HTTPException(status_code=404, detail="Nothing to undo.")
    return {"ok": True, "label": snap["label"],
            "school": snap["school"], "result": snap.get("schedule")}


# ── school data ───────────────────────────────────────────────────────────────

@app.get("/api/school")
def get_school():
    return store.load_school()


@app.put("/api/school")
def put_school(school: dict):
    errors = store.validate_school(school)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    store.push_history("Manual edit of school data")
    store.save_school(school)
    return {"ok": True, "school": school}


@app.post("/api/school/reset")
def reset_school():
    store.push_history("Reset to sample data")
    return {"ok": True, "school": store.reset_school()}


# ── solving ───────────────────────────────────────────────────────────────────

def _solve_and_store(school: dict, time_limit_seconds: int) -> dict:
    previous = store.load_last_schedule()
    if previous and previous.get("schedule"):
        # tie-break towards the previous solve so nothing moves without a
        # reason (weight-1 sticky_slots; not persisted — school is a copy)
        school = copy.deepcopy(school)
        store.attach_baseline(school, previous, field="sticky_slots")
    result = solve(school, time_limit_seconds=time_limit_seconds)
    if not result["schedule"]:
        # explain WHY it is unsolvable (static checks + relaxation probes)
        result["warnings"].extend(diagnose(school, time_limit_per_probe=10))
    # what changed vs the previous solve, so the user never loses track
    result["changes"] = diff.compute_changes(previous, result)
    store.save_last_schedule(result)
    return result


@app.post("/api/solve")
def post_solve(req: SolveRequest = SolveRequest()):
    school = store.load_school()
    errors = store.validate_school(school)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    store.push_history("Re-solve of the schedule")
    return _solve_and_store(school, req.time_limit_seconds)


@app.get("/api/schedule")
def get_schedule():
    return store.load_last_schedule() or {"status": None, "schedule": [], "warnings": []}


# ── ERP import ────────────────────────────────────────────────────────────────

class ErpPeriodsRequest(BaseModel):
    source: str


class ErpPreviewRequest(BaseModel):
    source: str
    academic_period_id: str | None = None


class ErpApplyRequest(BaseModel):
    source: str
    mapping: dict
    academic_period_id: str | None = None


@app.get("/api/erp/sources")
def erp_sources():
    try:
        return {"sources": erp.public_sources()}
    except erp.ErpError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/erp/periods")
def erp_periods(req: ErpPeriodsRequest):
    try:
        return {"periods": erp.fetch_periods(erp.get_source(req.source))}
    except erp.ErpError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/erp/preview")
def erp_preview(req: ErpPreviewRequest):
    try:
        source = erp.get_source(req.source)
        rows = erp.fetch_rows(source, req.academic_period_id)
        out = erp.build_plan(store.load_school(), source, rows,
                             erp.load_mapping())
    except erp.ErpError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return out["plan"]


@app.post("/api/erp/apply")
def erp_apply(req: ErpApplyRequest):
    try:
        source = erp.get_source(req.source)
        rows = erp.fetch_rows(source, req.academic_period_id)
        out = erp.build_plan(store.load_school(), source, rows, req.mapping)
    except erp.ErpError as e:
        raise HTTPException(status_code=502, detail=str(e))

    errors = store.validate_school(out["school"])
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})

    store.push_history(f"ERP import from {source['name']}")
    erp.save_mapping(out["plan"]["mapping"])
    store.save_school(out["school"])
    return {"ok": True, "plan": out["plan"], "school": out["school"]}


# ── exports ───────────────────────────────────────────────────────────────────

def _last_schedule_or_404() -> dict:
    result = store.load_last_schedule()
    if not result or not result.get("schedule"):
        raise HTTPException(status_code=404, detail="No solved schedule yet — run the solver first.")
    return result


@app.get("/api/export/ics")
def export_ics():
    result = _last_schedule_or_404()
    return Response(
        content=export.build_ics(result, store.load_school()),
        media_type="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="school-timetable.ics"'},
    )


@app.get("/api/export/pdf")
def export_pdf():
    result = _last_schedule_or_404()
    return Response(
        content=export.build_pdf(result, store.load_school()),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="school-timetable.pdf"'},
    )


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

    store.push_history("AI change: " + (preview[0] if preview else "assistant edit"))
    store.save_school(new_school)
    response = {"ok": True, "preview": preview, "school": new_school, "result": None}

    if req.resolve:
        response["result"] = _solve_and_store(new_school, req.time_limit_seconds)
    return response


# ── static UI (must be mounted last) ──────────────────────────────────────────

static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
