"""Tests for CachingAIProvider (src/ai_layer/caching_ai_provider.py): saved AI replies make runs repeatable and work offline."""

import json

from ai_layer.ai_manager import AIManager
from ai_layer.ai_provider import AIProvider
from ai_layer.caching_ai_provider import CachingAIProvider
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
    inner = FakeAIProvider(PROFILE, name="Google Gemini (gemini-x)")
    cache = CachingAIProvider(inner, str(tmp_path / "cache.json"))
    assert isinstance(cache, AIProvider) and cache.provider_name == inner.provider_name
    assert AIManager(cache).provider_name == "Google Gemini (gemini-x)"


def test_a_cache_can_wrap_another_cache(tmp_path):
    inner = FakeAIProvider(PROFILE)
    first = CachingAIProvider(inner, str(tmp_path / "first.json"))
    second = CachingAIProvider(first, str(tmp_path / "second.json"))
    assert isinstance(second, AIProvider) and second.provider_name == inner.provider_name
    manager = AIManager(second)
    assert manager.extract_candidate_profile("my resume") == PROFILE
    assert manager.extract_candidate_profile("my resume") == PROFILE  # replayed by the outer cache
    assert len(inner.calls) == 1 and first.size == 1 and second.size == 1


def test_live_ai_is_the_default_and_the_cache_is_turned_on_by_request(monkeypatch, tmp_path):
    inner = FakeAIProvider(PROFILE)
    monkeypatch.setattr(AIManager, "create_provider", classmethod(lambda cls, name, model=None: inner))
    monkeypatch.setenv("AI_CACHE_PATH", str(tmp_path / "cache.json"))
    monkeypatch.delenv("AI_CACHE", raising=False)
    live = AIManager.from_environment()
    live.extract_candidate_profile("my resume")
    live.extract_candidate_profile("my resume")
    assert len(inner.calls) == 2  # every record goes to the AI
    monkeypatch.setenv("AI_CACHE", "on")
    cached = AIManager.from_environment()
    cached.extract_candidate_profile("my resume")
    cached.extract_candidate_profile("my resume")
    assert len(inner.calls) == 3  # the second one was replayed from the cache
