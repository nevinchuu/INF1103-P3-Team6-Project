"""Tests for CachingAIProvider (src/ai/caching_ai_provider.py): saved AI replies make runs repeatable and work offline."""

import json

from ai.ai_manager import AIManager
from ai.base_ai_provider import BaseAIProvider
from ai.caching_ai_provider import CachingAIProvider
from fakes import FakeAIProvider
from test_ai_manager import PROFILE


def test_the_same_input_gives_the_same_output_and_asks_the_ai_once(tmp_path):
    # The real AI might answer differently the second time; the cache replays the first answer
    inner = FakeAIProvider(PROFILE, {**PROFILE, "candidate_name": "Someone Else"})
    manager = AIManager(CachingAIProvider(inner, str(tmp_path / "cache.json")))
    first = manager.extract_candidate_profile("my resume")
    second = manager.extract_candidate_profile("my resume")
    assert first == second and first["candidate_name"] == PROFILE["candidate_name"] and len(inner.calls) == 1
    assert manager.extract_candidate_profile("another resume")["candidate_name"] == "Someone Else"  # new input


def test_saved_replies_are_replayed_by_a_new_run_with_no_ai_at_all(tmp_path):
    path = str(tmp_path / "cache.json")
    AIManager(CachingAIProvider(FakeAIProvider(PROFILE), path)).extract_candidate_profile("my resume")
    offline = FakeAIProvider(None)  # as if there were no network or API key
    replayed = AIManager(CachingAIProvider(offline, path)).extract_candidate_profile("my resume")
    assert replayed["candidate_name"] == PROFILE["candidate_name"] and offline.calls == []


def test_an_unusable_reply_is_forgotten_so_the_retry_reaches_the_ai(tmp_path):
    inner = FakeAIProvider("not json", PROFILE)
    cache = CachingAIProvider(inner, str(tmp_path / "cache.json"))
    assert AIManager(cache).extract_candidate_profile("my resume")["candidate_name"] == PROFILE["candidate_name"]
    assert len(inner.calls) == 2 and cache.size == 1  # only the good reply is kept
    saved = json.loads((tmp_path / "cache.json").read_text())
    assert "not json" not in saved.values()


def test_a_failed_call_is_not_saved(tmp_path):
    cache = CachingAIProvider(FakeAIProvider(None), str(tmp_path / "cache.json"))
    assert AIManager(cache).extract_candidate_profile("my resume") is None and cache.size == 0


def test_a_missing_corrupt_or_unwritable_cache_never_stops_the_ai(tmp_path):
    corrupt = tmp_path / "cache.json"
    corrupt.write_text("{not json")
    cache = CachingAIProvider(FakeAIProvider(PROFILE), str(corrupt))
    assert cache.size == 0 and AIManager(cache).extract_candidate_profile("r") is not None
    (tmp_path / "folder").mkdir()
    unwritable = CachingAIProvider(FakeAIProvider(PROFILE), str(tmp_path / "folder"))  # a folder, not a file
    assert AIManager(unwritable).extract_candidate_profile("r") is not None


def test_it_stands_in_for_any_provider(tmp_path):
    inner = FakeAIProvider(PROFILE, label="Google Gemini")
    cache = CachingAIProvider(inner, str(tmp_path / "cache.json"))
    assert isinstance(cache, BaseAIProvider) and cache.label == "Google Gemini" and cache.model == inner.model
    assert AIManager(cache).provider_name == "Google Gemini"


def test_live_ai_is_the_default_and_the_cache_is_turned_on_by_request(monkeypatch):
    monkeypatch.delenv("AI_CACHE", raising=False)
    assert not isinstance(AIManager.from_environment()._provider, CachingAIProvider)  # every record goes to the AI
    monkeypatch.setenv("AI_CACHE", "on")
    assert isinstance(AIManager.from_environment()._provider, CachingAIProvider)
