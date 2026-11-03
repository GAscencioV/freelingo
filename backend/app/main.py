import asyncio
import logging
import os
from contextlib import asynccontextmanager

from alembic.config import Config
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from alembic import command
from app.core.analytics import AnalyticsMiddleware
from app.core.config import settings
from app.core.limiter import limiter

logging.basicConfig(
    level=settings.LOG_LEVEL.upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_AVATARS_DIR = "/app/avatars"
_TTS_PREVIEWS_DIR = "/app/tts_previews"
from app.routers import (
    admin,
    admin_dashboard_banner,
    analytics,
    assessment,
    auth,
    chat,
    contact,
    conversation,
    curriculum,
    dashboard_banner,
    feedback,
    flashcards,
    freemium,
    games,
    grammar,
    languages,
    lessons,
    listening,
    memories,
    phrasebook,
    progress,
    reading,
    reviews,
    stt,
    study_plan,
    tts,
    vocabulary,
)
from app.routers import config as config_router
from app.routers import health as health_router
from app.services.speech_manager import SpeechServiceManager
from app.services.stt_service import GeminiSTTService, OpenAISTTService, WhisperSTTService
from app.services.tts_service import GeminiTTSService, KokoroTTSService, OpenAITTSService


def _run_migrations() -> None:
    alembic_cfg = Config("/app/alembic.ini")
    command.upgrade(alembic_cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    if (
        not settings.SECRET_KEY
        or "CHANGE_ME" in settings.SECRET_KEY
        or len(settings.SECRET_KEY) < 32
    ):
        raise RuntimeError(
            "SECRET_KEY is insecure or unconfigured. "
            "Set a random value of at least 32 characters in your .env file."
        )

    await asyncio.to_thread(_run_migrations)

    # Ensure the avatars directory exists on startup (persisted via Docker volume)
    os.makedirs(_AVATARS_DIR, exist_ok=True)
    # Ensure the TTS preview cache directory exists on startup
    os.makedirs(_TTS_PREVIEWS_DIR, exist_ok=True)

    speech_manager = SpeechServiceManager()

    # Register local providers (Kokoro & Whisper)
    kokoro_tts = KokoroTTSService(settings.TTS_BASE_URL, settings.TTS_VOICE)
    speech_manager.register_tts("local", kokoro_tts)

    whisper_stt = WhisperSTTService(settings.STT_BASE_URL)
    speech_manager.register_stt("local", whisper_stt)

    # Register OpenAI providers
    if settings.OPENAI_API_KEY:
        openai_tts_base_url = settings.OPENAI_TTS_BASE_URL or settings.OPENAI_BASE_URL or None
        openai_tts = OpenAITTSService(
            api_key=settings.OPENAI_API_KEY,
            model=settings.OPENAI_TTS_MODEL,
            voice=settings.OPENAI_TTS_VOICE,
            speed=settings.OPENAI_TTS_SPEED,
            base_url=openai_tts_base_url,
        )
        speech_manager.register_tts("openai", openai_tts)

        openai_stt_base_url = settings.OPENAI_STT_BASE_URL or settings.OPENAI_BASE_URL or None
        openai_stt = OpenAISTTService(
            api_key=settings.OPENAI_API_KEY,
            model=settings.OPENAI_STT_MODEL,
            base_url=openai_stt_base_url,
        )
        speech_manager.register_stt("openai", openai_stt)
    elif settings.TTS_PROVIDER == "openai":
        raise ValueError("TTS_PROVIDER=openai requires OPENAI_API_KEY to be set")
    elif settings.STT_PROVIDER == "openai":
        raise ValueError("STT_PROVIDER=openai requires OPENAI_API_KEY to be set")

    # Register Gemini providers
    if settings.GEMINI_API_KEY:
        gemini_tts = GeminiTTSService(
            api_key=settings.GEMINI_API_KEY,
            model=settings.GEMINI_TTS_MODEL,
            voice=settings.GEMINI_TTS_VOICE,
            base_url=settings.GEMINI_BASE_URL,
        )
        speech_manager.register_tts("gemini", gemini_tts)

        gemini_stt = GeminiSTTService(
            api_key=settings.GEMINI_API_KEY,
            model=settings.GEMINI_STT_MODEL,
            base_url=settings.GEMINI_BASE_URL,
        )
        speech_manager.register_stt("gemini", gemini_stt)
    elif settings.TTS_PROVIDER == "gemini":
        raise ValueError("TTS_PROVIDER=gemini requires GEMINI_API_KEY to be set")
    elif settings.STT_PROVIDER == "gemini":
        raise ValueError("STT_PROVIDER=gemini requires GEMINI_API_KEY to be set")

    app.state.speech_manager = speech_manager
    app.state.tts_service = speech_manager.get_tts()
    app.state.stt_service = speech_manager.get_stt()

    yield


app = FastAPI(title="FreeLingo API", version="0.1.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "0"  # Modern browsers ignore it; CSP is the right tool
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; object-src 'none'; base-uri 'self'"  # API-only responses (JSON/binary)
    )
    return response


app.add_middleware(AnalyticsMiddleware)


app.include_router(auth.router)
app.include_router(analytics.router)
app.include_router(admin.router)
app.include_router(admin_dashboard_banner.router)
app.include_router(assessment.router)
app.include_router(study_plan.router)
app.include_router(lessons.router)
app.include_router(flashcards.router)
app.include_router(grammar.router)
app.include_router(chat.router)
app.include_router(progress.router)
app.include_router(listening.router)
app.include_router(reading.router)
app.include_router(reviews.router)
app.include_router(tts.router)
app.include_router(stt.router)
app.include_router(conversation.router)
app.include_router(config_router.router)
app.include_router(contact.router)
app.include_router(curriculum.router)
app.include_router(dashboard_banner.router)
app.include_router(feedback.router)
app.include_router(freemium.router)
app.include_router(games.router)
app.include_router(memories.router)
app.include_router(phrasebook.router)
app.include_router(languages.router)
app.include_router(health_router.router)
app.include_router(vocabulary.router)

if settings.STRIPE_ENABLED:
    import stripe as _stripe

    from app.routers import billing as billing_router

    _stripe.api_key = settings.STRIPE_SECRET_KEY
    app.include_router(billing_router.router)
