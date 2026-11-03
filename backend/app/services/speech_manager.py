from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.app_logger import get_logger
from app.core.config import settings

if TYPE_CHECKING:
    from app.services.speech_base import STTService, TTSService

logger = get_logger(__name__)


class SpeechServiceManager:
    """Registry and dynamic dispatcher for TTS and STT providers."""

    def __init__(self) -> None:
        self._tts_providers: dict[str, TTSService] = {}
        self._stt_providers: dict[str, STTService] = {}

    def register_tts(self, name: str, service: TTSService) -> None:
        self._tts_providers[name] = service
        logger.info("[speech-manager] Registered TTS provider: %s", name)

    def register_stt(self, name: str, service: STTService) -> None:
        self._stt_providers[name] = service
        logger.info("[speech-manager] Registered STT provider: %s", name)

    def get_tts(self, provider_override: str | None = None) -> TTSService:
        provider = (provider_override or settings.TTS_PROVIDER).strip().lower()
        service = self._tts_providers.get(provider)
        if service is None:
            available = list(self._tts_providers.keys())
            raise RuntimeError(
                f"TTS provider '{provider}' is not available. Configured providers: {available}"
            )
        return service

    def get_stt(self, provider_override: str | None = None) -> STTService:
        provider = (provider_override or settings.STT_PROVIDER).strip().lower()
        service = self._stt_providers.get(provider)
        if service is None:
            available = list(self._stt_providers.keys())
            raise RuntimeError(
                f"STT provider '{provider}' is not available. Configured providers: {available}"
            )
        return service

    def get_active_tts_provider(self, provider_override: str | None = None) -> str:
        return (provider_override or settings.TTS_PROVIDER).strip().lower()

    def get_active_stt_provider(self, provider_override: str | None = None) -> str:
        return (provider_override or settings.STT_PROVIDER).strip().lower()

    def list_tts_providers(self) -> list[str]:
        return sorted(self._tts_providers.keys())

    def list_stt_providers(self) -> list[str]:
        return sorted(self._stt_providers.keys())
