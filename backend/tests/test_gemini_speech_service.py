import base64
from unittest.mock import AsyncMock, patch

import pytest
import httpx

from app.services.speech_manager import SpeechServiceManager
from app.services.stt_service import GeminiSTTService, OpenAISTTService
from app.services.tts_service import GeminiTTSService, OpenAITTSService


@pytest.mark.asyncio
async def test_gemini_tts_synthesize_success():
    fake_audio = b"RIFF....WAVEfmt "
    b64_audio = base64.b64encode(fake_audio).decode("utf-8")
    mock_response = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "audio/wav",
                                "data": b64_audio,
                            }
                        }
                    ]
                }
            }
        ]
    }

    service = GeminiTTSService(api_key="test-key", voice="Kore")

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_response
    mock_resp.raise_for_status = AsyncMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        audio = await service.synthesize("Hello world", voice="Puck")
        assert audio == fake_audio

        mock_post.assert_awaited_once()
        kwargs = mock_post.await_args.kwargs
        assert kwargs["headers"]["x-goog-api-key"] == "test-key"
        payload = kwargs["json"]
        assert payload["contents"][0]["parts"][0]["text"] == "Hello world"
        assert (
            payload["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"][
                "voiceName"
            ]
            == "Puck"
        )


@pytest.mark.asyncio
async def test_gemini_tts_empty_text():
    service = GeminiTTSService(api_key="test-key")
    audio = await service.synthesize("   ")
    assert audio == b""


@pytest.mark.asyncio
async def test_gemini_tts_cache_key():
    service = GeminiTTSService(api_key="test-key", model="gemini-3.8-flash-tts", voice="Kore")
    k1 = service.get_cache_key("Bonjour", voice="Kore", language="fr-FR")
    k2 = service.get_cache_key("Bonjour", voice="Kore", language="fr-FR")
    k3 = service.get_cache_key("Bonjour", voice="Puck", language="fr-FR")
    assert k1 == k2
    assert k1 != k3


@pytest.mark.asyncio
async def test_gemini_stt_transcribe_success():
    mock_response = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": "Hello, how are you today?"
                        }
                    ]
                }
            }
        ]
    }

    service = GeminiSTTService(api_key="test-key")

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_response
    mock_resp.raise_for_status = AsyncMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        result = await service.transcribe(b"audio-bytes", language="en")
        assert result == "Hello, how are you today?"

        mock_post.assert_awaited_once()
        kwargs = mock_post.await_args.kwargs
        assert kwargs["headers"]["x-goog-api-key"] == "test-key"
        payload = kwargs["json"]
        assert "language 'en'" in payload["contents"][0]["parts"][0]["text"]
        assert payload["contents"][0]["parts"][1]["inlineData"]["data"] == base64.b64encode(
            b"audio-bytes"
        ).decode("utf-8")


@pytest.mark.asyncio
async def test_openai_base_url_passed():
    with patch("openai.AsyncOpenAI") as mock_openai:
        tts = OpenAITTSService(
            api_key="sk-test",
            model="tts-1",
            voice="nova",
            base_url="https://custom.gateway.internal/v1",
        )
        assert tts.base_url == "https://custom.gateway.internal/v1"
        mock_openai.assert_called_once_with(
            api_key="sk-test", base_url="https://custom.gateway.internal/v1"
        )

    with patch("openai.AsyncOpenAI") as mock_openai:
        stt = OpenAISTTService(
            api_key="sk-test",
            model="whisper-1",
            base_url="https://custom.gateway.internal/v1",
        )
        assert stt.base_url == "https://custom.gateway.internal/v1"
        mock_openai.assert_called_once_with(
            api_key="sk-test", base_url="https://custom.gateway.internal/v1"
        )


def test_speech_service_manager():
    manager = SpeechServiceManager()

    mock_tts_local = AsyncMock()
    mock_tts_openai = AsyncMock()
    mock_tts_gemini = AsyncMock()

    manager.register_tts("local", mock_tts_local)
    manager.register_tts("openai", mock_tts_openai)
    manager.register_tts("gemini", mock_tts_gemini)

    assert manager.get_tts("gemini") is mock_tts_gemini
    assert manager.get_tts("openai") is mock_tts_openai
    assert manager.get_tts("local") is mock_tts_local

    with pytest.raises(RuntimeError, match="TTS provider 'unknown' is not available"):
        manager.get_tts("unknown")
