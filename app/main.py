import os
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import RequestValidationError
from dotenv import load_dotenv

from .models import RecoveryRequest
from .recovery_engine import RecoveryEngine
from .call_e_service import CalleService

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="RestartAI — Emergency Production Recovery Agent")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
calle_service = CalleService()
engine = RecoveryEngine(calle_service)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse({"error": "Invalid incident input.", "details": exc.errors()}, status_code=422)


@app.exception_handler(ValueError)
async def value_error(request: Request, exc: ValueError):
    return JSONResponse({"error": str(exc)}, status_code=400)


@app.exception_handler(RuntimeError)
async def runtime_error(request: Request, exc: RuntimeError):
    return JSONResponse({"error": str(exc)}, status_code=502)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={"request": request})


@app.get("/api/config")
def config():
    return {"mode": calle_service.mode, "live": calle_service.mode == "live"}


@app.post("/api/recovery/start")
def start_recovery(payload: RecoveryRequest):
    return JSONResponse(engine.start(payload))


@app.post("/api/recovery/{run_id}/replan")
def replan(run_id: str):
    try:
        return JSONResponse(engine.replan(run_id))
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
