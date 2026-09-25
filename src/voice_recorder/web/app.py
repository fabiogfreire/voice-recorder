"""UI web local (FastAPI): lista gravações/transcrições e tela de Configurações
para a chave da OpenAI. Roda dentro do mesmo processo do watcher, sempre em
segundo plano — só é aberta no navegador quando o Fabio quiser consultar."""

import json
from pathlib import Path
from typing import List, Optional, Tuple

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
from ..db import delete_recording, get_recording, list_recordings, update_recording
from ..transcription.openai_client import estimate_cost_usd, identify_participants_in_transcript
from ..transcription.worker import enqueue_transcription, transcribe_preview

BASE_DIR = Path(__file__).parent

app = FastAPI(title="Voice Recorder")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def _format_duration_minutes(seconds: Optional[float]) -> Optional[str]:
    if seconds is None:
        return None
    return f"{seconds / 60:.1f}".replace(".", ",")


def _format_cost_usd(seconds: Optional[float], track_count: Optional[int]) -> Optional[str]:
    if seconds is None or not track_count:
        return None
    return f"{estimate_cost_usd(seconds, track_count):.2f}".replace(".", ",")


def _parse_participants(value: Optional[str]) -> List[str]:
    if not value:
        return []
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []


# Registrados como globais do Jinja em vez de pré-calculados em `index()`
# pra manter a linha da tabela simples de ler no template — a lógica de
# formatação (duração some quando não calculada, custo some fora do
# status "recorded") já vive nos próprios helpers acima.
templates.env.globals["format_duration_minutes"] = _format_duration_minutes
templates.env.globals["format_cost_usd"] = _format_cost_usd
templates.env.globals["parse_participants"] = _parse_participants


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


@app.post("/recordings/{recording_id}/preview")
def preview(recording_id: int):
    """Transcreve só o primeiro minuto — síncrono de propósito (ao
    contrário de /transcribe, que roda em background): um clipe de 60s
    leva poucos segundos na API, e a UI precisa do resultado pronto no
    redirect pra trocar o botão "Prévia" por "Ver prévia" na mesma
    requisição, sem depender do auto-refresh de 3s (que só dispara pra
    status "recording", ver base.html)."""
    if get_recording(recording_id) is None:
        raise HTTPException(status_code=404)

    api_key = get_openai_api_key()
    if not api_key:
        update_recording(
            recording_id,
            error_message="Chave da OpenAI não configurada — configure em /settings.",
        )
        return RedirectResponse("/", status_code=303)

    transcribe_preview(recording_id, api_key)
    return RedirectResponse("/", status_code=303)


@app.post("/recordings/{recording_id}/identify-participants")
def identify_participants(recording_id: int):
    """Mecanismo B (SPEC-identificacao-participantes.md): best-effort, só
    faz sentido com 2+ participantes capturados (call em grupo) e um
    transcript completo já pronto pra reescrever. Síncrono como /preview —
    é uma única chamada de texto, rápida."""
    row = get_recording(recording_id)
    if row is None or not row["transcript_path"]:
        raise HTTPException(status_code=404)

    participants = json.loads(row["participants"]) if row["participants"] else []
    if len(participants) < 2 or row["participants_identified"]:
        return RedirectResponse("/", status_code=303)  # nada a fazer

    api_key = get_openai_api_key()
    if not api_key:
        update_recording(
            recording_id,
            error_message="Chave da OpenAI não configurada — configure em /settings.",
        )
        return RedirectResponse("/", status_code=303)

    transcript_path = Path(row["transcript_path"])
    try:
        original_text = transcript_path.read_text(encoding="utf-8")
        updated_text = identify_participants_in_transcript(original_text, participants, api_key)
        transcript_path.write_text(updated_text, encoding="utf-8")
        update_recording(recording_id, participants_identified=1, error_message=None)
    except Exception as exc:
        update_recording(recording_id, error_message=str(exc)[:500])

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
