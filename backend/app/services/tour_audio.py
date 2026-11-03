"""Persistent, shared cache for localized dashboard tour narration."""

import asyncio
import fcntl
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from app.core.config import settings

OPENAI_VOICES = frozenset(
    {"alloy", "ash", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer"}
)
GEMINI_VOICES = frozenset(
    {"Kore", "Puck", "Charon", "Fenrir", "Aoede"}
)


async def get_tour_audio(
    service: Any,
    *,
    locale: str,
    text: str,
    voice: str | None,
) -> Path:
    effective_voice = voice or getattr(service, "voice", "")
    if hasattr(service, "get_cache_key"):
        key = service.get_cache_key(text, effective_voice, locale)
    else:
        identity = {
            "text": text,
            "locale": locale,
            "provider": type(service).__name__.lower(),
            "model": getattr(service, "model", "unknown"),
            "voice": effective_voice,
            "speed": 1.0,
            "format": "mp3",
        }
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    media_type = getattr(service, "media_type", "audio/mpeg")
    ext = "wav" if media_type == "audio/wav" else "mp3"

    directory = Path(settings.AUDIO_STORAGE_PATH) / "tour" / locale
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{key}.{ext}"

    def cached() -> bool:
        return path.is_file() and path.stat().st_size > 0

    if cached():
        return path

    # The shared audio volume also shares this lock across Uvicorn workers.
    # Nonblocking acquisition keeps provider calls and lock waits cancellable.
    async with asyncio.timeout(60):
        with (directory / f"{key}.lock").open("a+b") as lock:
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.1)
            try:
                if cached():
                    return path
                audio = await service.synthesize(text, effective_voice, language=locale)
                if not audio:
                    raise ValueError("Empty tour narration")
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(
                        dir=directory, suffix=".tmp", delete=False
                    ) as out:
                        temporary = Path(out.name)
                        out.write(audio)
                    os.replace(temporary, path)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    return path
