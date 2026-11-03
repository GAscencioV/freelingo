import os
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_logger import get_logger
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.models.conversation import Conversation
from app.models.study_plan import StudyPlan
from app.models.user import User
from app.schemas.auth import SUPPORTED_UI_LOCALES
from app.schemas.tts_stt import ContextualTTSRequest, TTSRequest
from app.services.chat_markdown import chat_markdown_to_speech
from app.services.prompts.common import TUTOR_DISPLAY_NAME
from app.services.tour_audio import OPENAI_VOICES, get_tour_audio
from app.services.tts_service import OpenAITTSService

router = APIRouter(prefix="/api", tags=["tts"])
logger = get_logger(__name__)

_PREVIEW_DIR = "/app/tts_previews"
_OPENAI_VOICES = frozenset(
    {"alloy", "ash", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer"}
)
_GEMINI_VOICES = frozenset(
    {"Kore", "Puck", "Charon", "Fenrir", "Aoede"}
)
_PREVIEW_TEXT = (
    f"Hello! I'm {TUTOR_DISPLAY_NAME}, your tutor. This is how I sound — warm, clear, "
    "and ready to help you practise every day. Let's get started!"
)


def _resolve_tts_service(request: Request) -> tuple[object, str]:
    """Resolve the TTS service and active provider name using dynamic manager or app.state."""
    provider_override = request.headers.get("X-Speech-Provider")
    manager = getattr(request.app.state, "speech_manager", None)
    if manager is not None:
        try:
            service = manager.get_tts(provider_override)
            provider_name = manager.get_active_tts_provider(provider_override)
            return service, provider_name
        except Exception:
            pass
    service = getattr(request.app.state, "tts_service", None)
    provider_name = (provider_override or settings.TTS_PROVIDER).strip().lower()
    return service, provider_name


@router.post("/tts/tour/{locale}/{step}")
@limiter.limit("20/minute")
async def tour_narration(
    request: Request,
    locale: str,
    step: str,
    body: TTSRequest,
    _current_user: User = Depends(get_current_user),
) -> FileResponse:
    """Speak frontend-localized tour text, reusing persistent audio across users."""
    if locale not in SUPPORTED_UI_LOCALES or step not in {f"step{i}" for i in range(1, 8)}:
        raise HTTPException(status_code=404, detail="Unknown tour narration")

    service, provider_name = _resolve_tts_service(request)
    if service is None:
        raise HTTPException(status_code=503, detail="TTS service is not enabled")

    if provider_name == "openai" and body.voice and body.voice not in OPENAI_VOICES:
        raise HTTPException(status_code=400, detail="Invalid voice name")
    if provider_name == "gemini" and body.voice and body.voice not in _GEMINI_VOICES:
        raise HTTPException(status_code=400, detail="Invalid voice name")

    try:
        path = await get_tour_audio(service, locale=locale, text=body.text, voice=body.voice)
    except TimeoutError:
        raise HTTPException(status_code=504, detail="Tour narration timed out")
    except Exception:
        logger.exception("tour_narration_failed", locale=locale, step=step)
        raise HTTPException(status_code=503, detail="Tour narration is unavailable")

    media_type = getattr(service, "media_type", "audio/mpeg")
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "no-store"})


@router.post("/tts")
@limiter.limit("20/minute")
async def text_to_speech(
    request: Request,
    body: ContextualTTSRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Synthesize text using persisted language context when supplied."""
    t0 = time.perf_counter()
    trace_id = request.headers.get("X-TTS-Trace-ID") or f"tts-{uuid.uuid4().hex[:12]}"

    tts_service, provider_name = _resolve_tts_service(request)
    if tts_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TTS service is not enabled",
        )

    language = None
    study_plan_id = body.study_plan_id
    if body.conversation_id is not None:
        conversation = await db.scalar(
            select(Conversation).where(
                Conversation.id == body.conversation_id,
                Conversation.user_id == current_user.id,
            )
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        study_plan_id = conversation.study_plan_id
        language = conversation.target_language
    if study_plan_id is not None:
        plan = await db.scalar(
            select(StudyPlan).where(
                StudyPlan.id == study_plan_id,
                StudyPlan.user_id == current_user.id,
            )
        )
        if plan is None:
            raise HTTPException(status_code=404, detail="Study plan not found")
        language = plan.target_language

    text = body.text
    if body.conversation_id is not None:
        text = chat_markdown_to_speech(text, language)
        if not text.strip():
            raise HTTPException(status_code=422, detail="No speakable text")

    synth_t0 = time.perf_counter()
    # For local Kokoro TTS, ignore client voice param
    voice = body.voice if provider_name != "local" else None
    if provider_name == "openai" and voice and voice not in _OPENAI_VOICES:
        raise HTTPException(status_code=400, detail="Invalid voice name")
    if provider_name == "gemini" and voice and voice not in _GEMINI_VOICES:
        raise HTTPException(status_code=400, detail="Invalid voice name")

    audio = await tts_service.synthesize(text, voice, language=language)
    synth_ms = (time.perf_counter() - synth_t0) * 1000
    total_ms = (time.perf_counter() - t0) * 1000

    media_type = getattr(tts_service, "media_type", "audio/mpeg")

    logger.info(
        "tts",
        trace=trace_id,
        user_id=current_user.id,
        text_len=len(body.text),
        audio_bytes=len(audio),
        provider=type(tts_service).__name__,
        synth_ms=round(synth_ms, 1),
        total_ms=round(total_ms, 1),
    )

    return Response(
        content=audio,
        media_type=media_type,
        headers={
            "X-TTS-Trace-ID": trace_id,
            "X-TTS-Backend-Synth-Ms": f"{synth_ms:.1f}",
            "X-TTS-Backend-Total-Ms": f"{total_ms:.1f}",
            "X-TTS-Provider": provider_name,
        },
    )


@router.get("/tts/preview/{voice}")
@limiter.limit("60/minute")
async def voice_preview(
    request: Request,
    voice: str,
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    """Return a cached preview audio clip for the given TTS voice.

    Available when TTS_PROVIDER is openai or gemini.
    """
    tts_service, provider_name = _resolve_tts_service(request)
    if tts_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TTS service is not enabled",
        )

    if provider_name not in ("openai", "gemini"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Voice preview is only available with OpenAI or Gemini TTS",
        )

    if provider_name == "openai" and voice not in _OPENAI_VOICES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid voice name")
    if provider_name == "gemini" and voice not in _GEMINI_VOICES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid voice name")

    media_type = getattr(tts_service, "media_type", "audio/mpeg")
    ext = "wav" if media_type == "audio/wav" else "mp3"

    cache_path = os.path.join(_PREVIEW_DIR, f"{provider_name}-{voice}.{ext}")
    if hasattr(tts_service, "get_cache_key"):
        key = tts_service.get_cache_key(_PREVIEW_TEXT, voice)
        cache_path = os.path.join(_PREVIEW_DIR, f"{provider_name}-{voice}-{key}.{ext}")

    if not os.path.exists(cache_path):
        os.makedirs(_PREVIEW_DIR, exist_ok=True)
        audio = await tts_service.synthesize(_PREVIEW_TEXT, voice)
        tmp_path = cache_path + ".tmp"
        with open(tmp_path, "wb") as fh:  # noqa: PTH123
            fh.write(audio)
        os.replace(tmp_path, cache_path)
        logger.info("tts_preview_cached", provider=provider_name, voice=voice, bytes=len(audio))

    return FileResponse(cache_path, media_type=media_type, headers={"Cache-Control": "no-store"})
