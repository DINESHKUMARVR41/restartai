import os
from pathlib import Path
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import RequestValidationError
from dotenv import load_dotenv

from .models import RecoveryRequest, TestCallRequest, Supplier
from .recovery_engine import RecoveryEngine
from .call_e_service import CalleService, CallEError

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="RestartAI — Emergency Production Recovery Agent")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
calle_service = CalleService()
engine = RecoveryEngine(calle_service)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse({"success": False, "error": "Invalid request.", "details": exc.errors()}, status_code=422)


@app.exception_handler(ValueError)
async def value_error(request: Request, exc: ValueError):
    return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.exception_handler(RuntimeError)
async def runtime_error(request: Request, exc: RuntimeError):
    return JSONResponse({"success": False, "error": str(exc)}, status_code=502)


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


@app.post("/api/calle/test-call")
async def test_call(payload: TestCallRequest):
    if calle_service.mode != "live":
        return JSONResponse({"error": "CALL-E test calls require CALL_E_MODE=live."}, status_code=400)
    return JSONResponse({"success": True, **(await calle_service.start_test_call(payload.phone, payload.supplier_name))})


@app.get("/api/calle/call/{call_id}")
async def test_call_status(call_id: str):
    if call_id not in calle_service.calls:
        return JSONResponse({"success": False, "error": "CALL-E call not found."}, status_code=404)
    try:
        return JSONResponse(await calle_service.get_call_status(call_id))
    except CallEError as exc:
        return JSONResponse({"success": False, "error": str(exc), "code": exc.code, "details": {"call_id": call_id}}, status_code=502)
