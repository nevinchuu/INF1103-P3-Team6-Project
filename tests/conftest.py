"""Shared pytest set-up: every test runs without a live AI, a real job portal, a real data folder or a real database."""

import pytest


@pytest.fixture(autouse=True)
def _no_real_ai_keys(monkeypatch, tmp_path):
    """No test may reach a live AI: the API keys are removed from the environment for every test, and saved AI
    replies (CachingAIProvider) go to a temporary file, never the real data folder."""
    for name in ("GEMINI_API_KEY",):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AI_CACHE_PATH", str(tmp_path / "ai_cache.json"))
    monkeypatch.setenv("PORTAL_LISTINGS_PATH", str(tmp_path / "portal_listings.json"))
    # Nothing can reach a real database: this address refuses connections at once
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused@127.0.0.1:1/unused")
