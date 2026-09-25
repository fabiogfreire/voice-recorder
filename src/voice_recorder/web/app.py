"""UI web local (FastAPI): lista gravações/transcrições e tela de Configurações
para a chave da OpenAI. Roda dentro do mesmo processo do watcher, sempre em
segundo plano — só é aberta no navegador quando o Fabio quiser consultar."""

import json
from pathlib import Path
from typing import Optional, Tuple

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openai import OpenAI

from ..config import (
    get_openai_api_key,
    get_transcription_model_plain,
    get_transcription_model_timestamps,
    save_openai_api_key,
)
from ..db import delete_recording, get_recording, list_recordings
from ..transcription.worker import enqueue_transcription

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


@app.post("/recordings/{recording_id}/transcribe")
def transcribe(recording_id: int):
    if get_recording(recording_id) is None:
        raise HTTPException(status_code=404)
    enqueue_transcription(recording_id)
    return RedirectResponse("/", status_code=303)


@app.get("/recordings/{recording_id}/transcript")
def transcript(request: Request, recording_id: int):
    row = get_recording(recording_id)
    if row is None or not row["transcript_path"]:
        raise HTTPException(status_code=404)
    text = Path(row["transcript_path"]).read_text(encoding="utf-8")
    return templates.TemplateResponse(
        request, "transcript.html", {"recording": row, "text": text}
    )


@app.get("/recordings/{recording_id}/transcript/download")
def download_transcript(recording_id: int):
    row = get_recording(recording_id)
    if row is None or not row["transcript_path"]:
        raise HTTPException(status_code=404)
    path = Path(row["transcript_path"])
    return FileResponse(path, filename=path.name, media_type="text/plain")


@app.post("/recordings/{recording_id}/delete")
def delete(recording_id: int):
    row = get_recording(recording_id)
    if row is None:
        raise HTTPException(status_code=404)

    paths = []
    if row["mic_path"]:
        paths.append(Path(row["mic_path"]))
    if row["loopback_path"]:
        paths.extend(Path(p) for p in json.loads(row["loopback_path"]))
    if row["transcript_path"]:
        paths.append(Path(row["transcript_path"]))

    for path in paths:
        path.unlink(missing_ok=True)

    delete_recording(recording_id)
    return RedirectResponse("/", status_code=303)


@app.get("/settings")
def settings(request: Request):
    api_key = get_openai_api_key()
    masked_key = f"sk-...{api_key[-4:]}" if api_key else None
    return templates.TemplateResponse(
        request, "settings.html", {"masked_key": masked_key}
    )


def _validate_key(key: str) -> Tuple[str, str]:
    """Chamada leve pra checar a chave (client.models.list()) e se o
    projeto tem acesso aos modelos de transcrição configurados. Sem isso,
    um 403 só aparece horas depois, no fim de uma call."""
    try:
        client = OpenAI(api_key=key)
        available = sorted(m.id for m in client.models.list())
    except Exception as exc:
        return f"Chave inválida ou sem permissão: {exc}", "error"

    needed = [get_transcription_model_timestamps(), get_transcription_model_plain()]
    missing = [m for m in needed if m not in available]
    if missing:
        transcription_models = [
            m for m in available if "transcribe" in m or m == "whisper-1"
        ]
        return (
            "Chave válida, mas sem acesso aos modelos configurados: "
            f"{', '.join(missing)}. Modelos de transcrição disponíveis neste "
            f"projeto: {', '.join(transcription_models) or 'nenhum'}.",
            "warning",
        )
    return "Chave válida — acesso aos modelos configurados confirmado.", "success"


@app.post("/settings")
def save_settings(request: Request, openai_api_key: str = Form(...)):
    message: Optional[str] = None
    message_type: Optional[str] = None

    key = openai_api_key.strip()
    if key:
        save_openai_api_key(key)
        message, message_type = _validate_key(key)

    api_key = get_openai_api_key()
    masked_key = f"sk-...{api_key[-4:]}" if api_key else None
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"masked_key": masked_key, "message": message, "message_type": message_type},
    )
