import base64
import hashlib
import json
import time

import httpx
import openai

from app.core.app_logger import get_logger
from app.services.prompts.speech import build_speech_instructions

logger = get_logger(__name__)


class KokoroTTSService:
    media_type = "audio/mpeg"

    def __init__(self, base_url: str, voice: str) -> None:
        self.base_url = base_url
        self.voice = voice

    async def health(self) -> None:
        """Raise if Kokoro is unreachable."""
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{self.base_url}/v1/models", timeout=5.0)
            r.raise_for_status()

    def get_cache_key(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> str:
        identity = {
            "text": text,
            "locale": language,
            "provider": "local",
            "model": "kokoro",
            "voice": voice or self.voice,
            "speed": 1.0,
            "format": "mp3",
        }
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    async def synthesize(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> bytes:
        """Call Kokoro-FastAPI and return MP3 audio bytes."""
        _ = language  # Kokoro handles language via the voice model itself
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/v1/audio/speech",
                json={
                    "model": "kokoro",
                    "input": text,
                    "voice": voice or self.voice,
                    "response_format": "mp3",
                },
                timeout=30.0,
            )
            response.raise_for_status()
            return response.content


class OpenAITTSService:
    media_type = "audio/mpeg"

    def __init__(
        self,
        api_key: str,
        model: str,
        voice: str,
        speed: float = 1.0,
        timeout: float | None = None,
        base_url: str | None = None,
    ) -> None:
        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = openai.AsyncOpenAI(**client_kwargs)
        self.model = model
        self.voice = voice
        self.speed = speed
        self.timeout = timeout
        self.base_url = base_url

    async def health(self) -> None:
        """Raise if OpenAI TTS is unreachable (lightweight models list call)."""
        await self._client.models.list()

    def get_instructions(self, language: str | None = None) -> str | None:
        if self.model == "gpt-4o-mini-tts" or self.model.startswith("gpt-4o-mini-tts-"):
            return build_speech_instructions(language)
        return None

    def get_cache_key(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> str:
        identity = {
            "text": text,
            "locale": language,
            "provider": "openai",
            "model": self.model,
            "voice": voice or self.voice,
            "speed": self.speed,
            "format": "mp3",
        }
        instructions = self.get_instructions(language)
        if instructions:
            identity["instructions"] = instructions
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    async def synthesize(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> bytes:
        """Call OpenAI TTS API and return MP3 audio bytes."""
        text = text.strip()
        if not text:
            logger.warning("[tts-openai] Empty text received for synthesis")
            return b""

        req_voice = (voice or self.voice).strip()
        input_len = len(text)
        start_t = time.perf_counter()
        logger.info(
            "[tts-openai] request_start model=%s voice=%s chars=%d",
            self.model,
            req_voice,
            input_len,
        )
        request_payload = {
            "model": self.model,
            "voice": req_voice,
            "input": text,
            "response_format": "mp3",
            "speed": self.speed,
        }
        instructions = self.get_instructions(language)
        if instructions:
            request_payload["instructions"] = instructions
        if self.timeout is not None:
            request_payload["timeout"] = self.timeout

        response = await self._client.audio.speech.create(**request_payload)
        audio = response.content
        if not audio:
            raise RuntimeError("OpenAI TTS returned empty audio payload")
        elapsed_ms = (time.perf_counter() - start_t) * 1000
        request_id = getattr(response, "request_id", None)
        if request_id is None:
            headers = getattr(response, "headers", None)
            if headers is not None:
                request_id = headers.get("x-request-id")
        logger.info(
            "[tts-openai] request_ok model=%s voice=%s chars=%d bytes=%d ms=%.1f request_id=%s",
            self.model,
            req_voice,
            input_len,
            len(audio),
            round(elapsed_ms, 1),
            request_id,
        )
        return audio


class GeminiTTSService:
    media_type = "audio/wav"

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.8-flash-tts",
        voice: str = "Kore",
        base_url: str = "https://generativelanguage.googleapis.com",
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.voice = voice
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def health(self) -> None:
        """Raise if Gemini TTS endpoint is unreachable."""
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self.base_url}/v1beta/models/{self.model}",
                headers={"x-goog-api-key": self.api_key},
                timeout=5.0,
            )
            r.raise_for_status()

    def get_cache_key(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> str:
        identity = {
            "text": text,
            "locale": language,
            "provider": "gemini",
            "model": self.model,
            "voice": (voice or self.voice).strip(),
            "format": "wav",
        }
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    async def synthesize(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> bytes:
        """Call Google Gemini REST API and return audio bytes."""
        _ = language
        text = text.strip()
        if not text:
            logger.warning("[tts-gemini] Empty text received for synthesis")
            return b""

        req_voice = (voice or self.voice).strip()
        input_len = len(text)
        start_t = time.perf_counter()
        logger.info(
            "[tts-gemini] request_start model=%s voice=%s chars=%d",
            self.model,
            req_voice,
            input_len,
        )

        payload = {
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {
                            "voiceName": req_voice,
                        }
                    }
                },
            },
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/v1beta/models/{self.model}:generateContent",
                headers={
                    "x-goog-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()

        try:
            candidates = data.get("candidates", [])
            part = candidates[0]["content"]["parts"][0]
            inline_data = part.get("inlineData") or part.get("inline_data")
            audio_b64 = inline_data["data"]
            audio = base64.b64decode(audio_b64)
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected Gemini TTS response payload: {data}") from exc

        if not audio:
            raise RuntimeError("Gemini TTS returned empty audio payload")

        elapsed_ms = (time.perf_counter() - start_t) * 1000
        logger.info(
            "[tts-gemini] request_ok model=%s voice=%s chars=%d bytes=%d ms=%.1f",
            self.model,
            req_voice,
            input_len,
            len(audio),
            round(elapsed_ms, 1),
        )
        return audio
