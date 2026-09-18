"""UI web local (FastAPI): lista gravações/transcrições e tela de Configurações
para a chave da OpenAI. Roda dentro do mesmo processo do watcher, sempre em
segundo plano — só é aberta no navegador quando o Fabio quiser consultar."""

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import get_openai_api_key, save_openai_api_key
from ..db import list_recordings

BASE_DIR = Path(__file__).parent

app = FastAPI(title="Voice Recorder")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/")
def index(request: Request):
    recordings = list_recordings()
    return templates.TemplateResponse(
        request, "index.html", {"recordings": recordings}
    )


@app.get("/settings")
def settings(request: Request):
    api_key = get_openai_api_key()
    masked_key = f"sk-...{api_key[-4:]}" if api_key else None
    return templates.TemplateResponse(
        request, "settings.html", {"masked_key": masked_key}
    )


@app.post("/settings")
def save_settings(openai_api_key: str = Form(...)):
    if openai_api_key.strip():
        save_openai_api_key(openai_api_key)
    return RedirectResponse("/settings", status_code=303)
