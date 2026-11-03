from unittest.mock import patch

from app.core.config import settings
from app.services.llm_adapter import LLMAdapter, MAX_CONTEXT_TOKENS


def test_gemini_llm_max_context():
    assert "gemini" in MAX_CONTEXT_TOKENS
    assert MAX_CONTEXT_TOKENS["gemini"] >= 1000000


def test_gemini_llm_adapter_init(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setattr(settings, "GEMINI_MODEL", "gemini-2.5-flash")
    monkeypatch.setattr(settings, "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com")

    with patch("app.services.llm_adapter.AsyncOpenAI") as mock_openai:
        adapter = LLMAdapter()
        assert adapter.provider == "gemini"
        assert adapter.model == "gemini-2.5-flash"
        mock_openai.assert_called_once_with(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            api_key="test-gemini-key",
        )
