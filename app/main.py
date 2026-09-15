import os
from pathlib import Path
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import RequestValidationError
from dotenv import load_dotenv

from .models import RecoveryRequest, TestCallRequest, Supplier, ChatRequest
from .recovery_engine import RecoveryEngine
from .call_e_service import CalleService, CallEError
from .llm_service import LlmService, LlmError

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="RestartAI — Emergency Production Recovery Agent")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
calle_service = CalleService()
engine = RecoveryEngine(calle_service)
llm_service = LlmService()


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse({"success": False, "error": "Invalid request.", "details": exc.errors()}, status_code=422)


@app.exception_handler(ValueError)
async def value_error(request: Request, exc: ValueError):
    return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.exception_handler(RuntimeError)
async def runtime_error(request: Request, exc: RuntimeError):
    return JSONResponse({"success": False, "error": str(exc)}, status_code=502)


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    import logging
    logging.getLogger("restartai").exception("Unhandled API error path=%s", request.url.path)
    return JSONResponse({"success": False, "error": "RestartAI encountered an unexpected server error.", "detail": str(exc)[:500]}, status_code=500)


@app.exception_handler(CallEError)
async def calle_error(request: Request, exc: CallEError):
    return JSONResponse({"success": False, "error": str(exc), "code": exc.code}, status_code=502)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={"request": request})


@app.get("/api/config")
def config():
    return {"mode": calle_service.mode, "live": calle_service.mode == "live"}


@app.get("/api/maintenance/intelligence")
def maintenance_intelligence(
    part_number: str = "",
    part_description: str = "",
    available_technicians: int = Query(default=3, ge=0),
    required_technicians_override: int | None = Query(default=None, ge=0),
    installation_minutes_override: int | None = Query(default=None, ge=0),
):
    return engine.maintenance_intelligence(
        part_number,
        part_description,
        available_technicians,
        required_technicians_override,
        installation_minutes_override,
    )



@app.post("/api/recovery/start")
async def start_recovery(payload: RecoveryRequest):
    result = await engine.start_async(payload) if calle_service.mode == "live" else engine.start(payload)
    return JSONResponse(result)


@app.post("/api/recovery/{run_id}/replan")
async def replan(run_id: str):
    try:
        result = await engine.replan_async(run_id) if calle_service.mode == "live" else engine.replan(run_id)
        return JSONResponse(result)
    except KeyError:
        return JSONResponse({"error": "Recovery run not found."}, status_code=404)


@app.post("/api/recovery/{run_id}/approve")
def approve(run_id: str):
    try:
        return JSONResponse(engine.approve(run_id))
    except KeyError:
        return JSONResponse({"error": "Recovery run not found."}, status_code=404)


@app.get("/api/recovery/{run_id}")
def get_recovery(run_id: str):
    result = engine.get(run_id)
    if not result:
        return JSONResponse({"error": "Recovery run not found."}, status_code=404)
    return JSONResponse(result)


@app.get("/api/recovery/{run_id}/calls")
def recovery_calls(run_id: str):
    result = engine.get(run_id)
    if not result:
        return JSONResponse({"error": "Recovery run not found."}, status_code=404)
    return JSONResponse({"calls": result.get("calls", [])})


@app.get("/api/config/llm")
def llm_config():
    return {"enabled": llm_service.enabled}


@app.post("/api/recovery/{run_id}/recommendation")
async def recovery_recommendation(run_id: str):
    result = engine.get(run_id)
    if not result:
        return JSONResponse({"error": "Recovery run not found."}, status_code=404)
    if not result.get("plans"):
        return JSONResponse({"success": False, "error": "No recovery plan is available yet."}, status_code=400)
    try:
        return JSONResponse(await llm_service.recommend(result))
    except LlmError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=502)


@app.post("/api/recovery/{run_id}/chat")
async def recovery_chat(run_id: str, payload: ChatRequest):
    result = engine.get(run_id)
    if not result:
        return JSONResponse({"error": "Recovery run not found."}, status_code=404)
    try:
        history = [m.model_dump() for m in payload.history]
        return JSONResponse(await llm_service.chat(result, payload.message, history))
    except LlmError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=502)


@app.post("/api/calle/test-call")
async def test_call(payload: TestCallRequest):
    # The generic connectivity call was the source of repeated "test" calls.
    # Keep the endpoint for backwards compatibility, but never place a call here.
    return JSONResponse({"success": False, "error": "Generic CALL-E test calls are disabled. Use /api/recovery/start for a real supplier recovery call."}, status_code=410)


@app.get("/api/calle/call/{call_id}")
async def test_call_status(call_id: str):
    if call_id not in calle_service.calls:
        return JSONResponse({"success": False, "error": "CALL-E call not found."}, status_code=404)
    try:
        return JSONResponse(await calle_service.get_call_status(call_id))
    except CallEError as exc:
        return JSONResponse({"success": False, "error": str(exc), "code": exc.code, "details": {"call_id": call_id}}, status_code=502)
