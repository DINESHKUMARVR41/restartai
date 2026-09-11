import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

from .models import RecoveryRequest
from .recovery_engine import RecoveryEngine
from .call_e_service import CalleService

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="RestartAI")

# Static files
app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static"
)

# HTML templates
templates = Jinja2Templates(
    directory=str(BASE_DIR / "templates")
)

# CALL-E + recovery engine
calle_service = CalleService()
engine = RecoveryEngine(calle_service)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"request": request}
    )


@app.post("/api/recovery/start")
def start_recovery(payload: RecoveryRequest):
    result = engine.start(payload)
    return JSONResponse(result)


@app.post("/api/recovery/{run_id}/replan")
def replan(run_id: str):
    result = engine.replan(run_id)
    return JSONResponse(result)


@app.post("/api/recovery/{run_id}/approve")
def approve(run_id: str):
    result = engine.approve(run_id)
    return JSONResponse(result)


@app.get("/api/recovery/{run_id}")
def get_recovery(run_id: str):
    result = engine.get(run_id)

    if not result:
        return JSONResponse(
            {"error": "Run not found"},
            status_code=404
        )

    return JSONResponse(result)