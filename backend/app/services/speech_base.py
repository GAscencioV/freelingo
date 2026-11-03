from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TTSService(Protocol):
    voice: str
    media_type: str

    async def health(self) -> None: ...

    async def synthesize(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> bytes: ...

    def get_cache_key(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> str: ...


@runtime_checkable
class STTService(Protocol):
    async def health(self) -> None: ...

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        mime_type: str = "audio/wav",
        *,
        language: str,
    ) -> str: ...
