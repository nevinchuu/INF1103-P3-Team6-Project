"""Tests for the AI layer: prompts, response parsing and validation, provider failures, and AIManager.

No live AI is used: FakeAIProvider (tests/fakes.py) and stub SDK clients stand in for the API.
"""

import json
from types import SimpleNamespace

import anthropic
import httpx2 as httpx
import openai
import pytest

from ai import base_ai_provider
from ai.ai_manager import AIManager, TASKS
from ai.anthropic_provider import AnthropicProvider
from ai.base_ai_provider import BaseAIProvider, PROVIDER_CONFIGS
from ai.openai_compatible_provider import OpenAICompatibleProvider
from fakes import FakeAIProvider

PROFILE = {"candidate_name": "Sample Candidate", "highest_qualification": "Diploma",
           "qualification_detail": "Diploma in Financial Technology", "years_of_experience": 1,
           "internship_months": 6, "seniority_level": "Fresh/entry level", "core_skills": ["SQL", "Python"],
           "certifications": [], "search_keywords": ["Data Analyst", "Business Analyst"]}


def requirements(index: int = 0, **changes) -> dict:
    """One valid job-requirements entry."""
    entry = {"job_index": index, "required_skills": ["SQL", "Python", "Tableau"], "min_education": "Not specified",
             "certifications_required": [], "work_arrangement": "Onsite",
             "skill_matches": [{"job_skill": "SQL", "candidate_skill": "SQL"}],
             "suitability_reason": "Matches SQL."}
    entry.update(changes)
    return entry


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Retries must not really sleep."""
    monkeypatch.setattr(base_ai_provider.time, "sleep", lambda seconds: None)


# ---------- reading the reply: AIManager._parse ----------

@pytest.mark.parametrize("raw", [
    json.dumps(PROFILE),
    "```json\n" + json.dumps(PROFILE) + "\n```",
    "Here is the answer: " + json.dumps(PROFILE) + " Hope that helps!",
])
def test_parser_accepts_plain_fenced_and_surrounded_json(raw):
    assert AIManager._parse(raw) == PROFILE


@pytest.mark.parametrize("raw", [None, "", "   ", "no json here", '{"unfinished": ', "[1, 2, 3]", '"just a string"'])
def test_parser_rejects_empty_malformed_and_non_object_replies(raw):
    assert AIManager._parse(raw) is None


# ---------- checking the reply: AIManager._validate ----------

def validate(data, task_name: str = "candidate_profile"):
    return AIManager(FakeAIProvider())._validate(data, task_name, TASKS[task_name])


def test_validator_accepts_a_valid_profile_and_normalises_values():
    cleaned = validate({**PROFILE, "highest_qualification": " diploma ", "extra": "dropped"})
    assert cleaned["highest_qualification"] == "Diploma"  # trimmed, matched ignoring case
    assert "extra" not in cleaned  # keys the AI added are dropped


@pytest.mark.parametrize("missing", list(PROFILE))
def test_validator_rejects_every_missing_required_field(missing):
    data = {key: value for key, value in PROFILE.items() if key != missing}
    assert validate(data) is None


@pytest.mark.parametrize("field, value", [
    ("years_of_experience", "three"),      # wrong type
    ("years_of_experience", True),         # a bool is not a number
    ("years_of_experience", 61),           # outside the allowed range
    ("years_of_experience", -1),
    ("internship_months", 1.5),            # must be whole
    ("highest_qualification", "Wizard"),   # not an allowed option
    ("core_skills", "SQL"),                # should be a list
    ("core_skills", [1, 2]),               # list items of the wrong type
    ("search_keywords", []),               # must not be empty
    ("search_keywords", ["", "  "]),       # only empty strings
    ("candidate_name", None),
])
def test_validator_rejects_wrong_types_values_and_ranges(field, value):
    assert validate({**PROFILE, field: value}) is None


def test_validator_drops_one_bad_job_but_keeps_the_rest():
    batch = {"jobs": [requirements(0), requirements(1, min_education="PhD in Magic"), requirements(2)]}
    cleaned = validate(batch, "job_requirements")
    assert [job["job_index"] for job in cleaned["jobs"]] == [0, 2]


# ---------- the prompt for each kind of AI call ----------

def test_every_kind_of_call_has_a_schema_and_a_prompt_that_asks_for_json():
    job = {"title": "Data Analyst", "company": "Acme", "min_years_experience": 0, "listed_skills": ["SQL"],
           "flexible_work_arrangements": [], "description": "Use SQL", "required_skills": ["SQL"]}
    records = {
        "candidate_profile": {"resume_text": "My resume"},
        "job_requirements": {"jobs": [job], "candidate": PROFILE},
        "tailored_resume": {"resume_text": "My resume", "job": job},
        "docx_edits": {"paragraphs": [{"id": 0, "text": "Hello", "editable": True, "kind": "text"}], "job": job,
                       "use_keywords": ["SQL"], "avoid_keywords": ["Java"]},
    }
    assert set(records) == set(TASKS)
    for task_name, record in records.items():
        provider = FakeAIProvider(None)
        AIManager(provider).run_task(task_name, record)
        sent_name, prompt = provider.calls[0]
        assert sent_name == task_name and TASKS[task_name]["type"] == "object"
        assert "Return JSON only." in prompt


def test_the_profile_prompt_contains_the_resume_text():
    provider = FakeAIProvider(None)
    AIManager(provider).extract_candidate_profile("MY UNIQUE RESUME TEXT")
    assert "MY UNIQUE RESUME TEXT" in provider.calls[0][1]


def test_the_profile_prompt_asks_for_the_most_advanced_completed_qualification():
    """Without this, the AI reads "highest" as "most recent" and answers Secondary for a resume whose
    education section ends with a GCE O-Level, which makes every Diploma+ job score education-not-met."""
    provider = FakeAIProvider(None)
    AIManager(provider).extract_candidate_profile("resume text")
    prompt = provider.calls[0][1]
    # it must rank by level, not by position or date in the resume
    assert "MOST ADVANCED" in prompt
    assert "NOT the most recent one, the last one written in the resume" in prompt
    # and only a finished qualification may be picked, however advanced it is
    assert "Only a COMPLETED qualification may be picked" in prompt
    assert 'still in progress, "expected", or ending after' in prompt


# ---------- AIManager with a fake provider (no live AI) ----------

def test_a_valid_reply_is_returned_validated():
    manager = AIManager(FakeAIProvider(PROFILE))
    assert manager.extract_candidate_profile("resume text") == PROFILE


def test_provider_name_comes_from_the_provider():
    assert AIManager(FakeAIProvider(label="Pretend AI")).provider_name == "Pretend AI"


def test_a_failed_api_call_returns_none_without_asking_again():
    provider = FakeAIProvider(None)
    assert AIManager(provider).extract_candidate_profile("resume") is None
    assert len(provider.calls) == 1  # the provider already retried; the manager does not ask again


def test_a_malformed_reply_is_asked_for_again_once():
    provider = FakeAIProvider("not json at all", PROFILE)
    assert AIManager(provider).extract_candidate_profile("resume") == PROFILE
    assert len(provider.calls) == 2


def test_two_malformed_replies_give_none():
    provider = FakeAIProvider("not json at all")
    assert AIManager(provider).extract_candidate_profile("resume") is None
    assert len(provider.calls) == 2


def test_an_empty_reply_gives_none():
    assert AIManager(FakeAIProvider("")).extract_candidate_profile("resume") is None


def test_a_reply_with_the_wrong_schema_is_rejected_after_one_retry():
    wrong = {**PROFILE, "years_of_experience": "lots"}
    provider = FakeAIProvider(wrong)
    assert AIManager(provider).extract_candidate_profile("resume") is None
    assert len(provider.calls) == 2


def test_a_very_long_resume_is_cut_before_it_is_sent():
    provider = FakeAIProvider(PROFILE)
    AIManager(provider).extract_candidate_profile("x" * 50000)
    assert "x" * 20000 in provider.calls[0][1] and "x" * 20001 not in provider.calls[0][1]


def test_job_answers_line_up_with_their_listings_by_job_index():
    listings = [{"title": "A"}, {"title": "B"}, {"title": "C"}]
    reply = {"jobs": [requirements(2, suitability_reason="for C"), requirements(0, suitability_reason="for A")]}
    answers = AIManager(FakeAIProvider(reply)).extract_job_requirements(listings, PROFILE)
    assert [answer and answer["suitability_reason"] for answer in answers] == ["for A", None, "for C"]


def test_the_requirements_prompt_asks_for_named_skills_from_the_candidates_own_field():
    """The code filter names no tools and no industry, so this prompt is the only place the candidate's field
    reaches required_skills. It must ask for named, teachable things and refuse generic activities."""
    provider = FakeAIProvider(None)
    AIManager(provider).extract_job_requirements([{"title": "A"}], PROFILE)
    prompt = provider.calls[0][1]
    # a skill must be something nameable, not an activity or a behaviour
    assert "HAVE A NAME you could put on a course" in prompt
    assert "generic activities and concepts that name no particular tool, method or standard" in prompt
    # and the field is taken from the resume, so a lab or engineering resume gets its own field's tools
    assert "Whatever field this posting is in, the test is the same." in prompt
    assert "the field is whatever their skills show it to be" in prompt
    for other_field in ("HPLC", "AutoCAD", "ISO 13485"):  # the examples must not all be one industry's
        assert other_field in prompt


def test_a_failed_batch_leaves_its_listings_without_answers():
    answers = AIManager(FakeAIProvider(None)).extract_job_requirements([{"title": "A"}, {"title": "B"}], PROFILE)
    assert answers == [None, None]


def test_listings_are_analysed_in_batches_and_keep_their_order():
    listings = [{"title": f"Job {number}"} for number in range(25)]  # 3 batches of 10, 10, 5
    reply = {"jobs": [requirements(index) for index in range(10)]}
    summary = {}
    answers = AIManager(FakeAIProvider(reply)).extract_requirements_for_listings(listings, PROFILE, summary=summary)
    assert len(answers) == 25
    assert summary["batches_failed"] == 0
    assert summary["listings_analysed"] == 25


def test_tailor_docx_returns_the_paragraphs_it_sent():
    paragraphs = [{"id": 0, "text": "Hello there", "editable": True, "kind": "text"}]
    reply = {"edits": [{"paragraph": 0, "text": "Hi", "reason": "shorter"}], "skill_evidence": []}
    result = AIManager(FakeAIProvider(reply)).tailor_docx(paragraphs, {"title": "T"}, ["SQL"], ["Java"])
    assert result["paragraphs"] == paragraphs and result["edits"][0]["text"] == "Hi"


def test_tailor_docx_cuts_a_very_long_resume():
    paragraphs = [{"id": i, "text": "w" * 1000, "editable": True, "kind": "text"} for i in range(30)]
    provider = FakeAIProvider({"edits": [], "skill_evidence": []})
    result = AIManager(provider).tailor_docx(paragraphs, {"title": "T"}, [], [])
    assert len(result["paragraphs"]) < 30


# ---------- real providers with a stubbed SDK client: failures never crash ----------

class StubOpenAIClient:
    """An OpenAI client whose chat.completions.create plays a script: an exception is raised, anything else
    is returned."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **params):
        self.requests.append(params)
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(step, Exception):
            raise step
        return step


def completion(text, finish="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=text))])


REQUEST = httpx.Request("POST", "https://example.invalid/v1/chat/completions")


def http_error(status):
    response = httpx.Response(status, request=REQUEST, headers={"retry-after": "0"})
    return openai.APIStatusError("boom", response=response, body=None)


def openai_provider(client, key="gemini"):
    return OpenAICompatibleProvider(PROVIDER_CONFIGS[key], client=client)


def test_a_provider_returns_the_reply_text():
    client = StubOpenAIClient(completion('{"ok": true}'))
    assert openai_provider(client).complete("prompt", "candidate_profile", {"type": "object"}) == '{"ok": true}'


def test_connection_failure_is_retried_then_gives_none():
    client = StubOpenAIClient(openai.APIConnectionError(request=REQUEST))
    assert openai_provider(client).complete("prompt", "task", {}) is None
    assert len(client.requests) == base_ai_provider.RATE_LIMIT_RETRIES + 1


def test_a_timeout_is_retried_then_gives_none():
    client = StubOpenAIClient(openai.APITimeoutError(request=REQUEST))
    assert openai_provider(client).complete("prompt", "task", {}) is None
    assert len(client.requests) == base_ai_provider.RATE_LIMIT_RETRIES + 1


def test_a_connection_failure_that_clears_on_retry_succeeds():
    client = StubOpenAIClient(openai.APIConnectionError(request=REQUEST), completion("{}"))
    assert openai_provider(client).complete("prompt", "task", {}) == "{}"
    assert len(client.requests) == 2


def test_rate_limit_is_retried():
    response = httpx.Response(429, request=REQUEST, headers={"retry-after": "0"})
    client = StubOpenAIClient(openai.RateLimitError("slow down", response=response, body=None), completion("{}"))
    assert openai_provider(client).complete("prompt", "task", {}) == "{}"


def test_a_long_rate_limit_wait_gives_up_at_once():
    response = httpx.Response(429, request=REQUEST, headers={"retry-after": "500"})
    client = StubOpenAIClient(openai.RateLimitError("quota", response=response, body=None))
    assert openai_provider(client).complete("prompt", "task", {}) is None
    assert len(client.requests) == 1


def test_a_wrong_key_is_not_retried():
    client = StubOpenAIClient(http_error(401))
    assert openai_provider(client).complete("prompt", "task", {}) is None
    assert len(client.requests) == 1


def test_a_rejected_format_falls_back_to_json_object_mode_with_the_schema_in_the_prompt():
    client = StubOpenAIClient(http_error(400), completion('{"a": 1}'))
    assert openai_provider(client).complete("prompt", "task", {"type": "object"}) == '{"a": 1}'
    assert client.requests[0]["response_format"]["type"] == "json_schema"
    assert client.requests[1]["response_format"] == {"type": "json_object"}
    assert '"type": "object"' in client.requests[1]["messages"][0]["content"]


def test_every_format_rejected_gives_none():
    assert openai_provider(StubOpenAIClient(http_error(400))).complete("prompt", "task", {}) is None


@pytest.mark.parametrize("reply", [
    SimpleNamespace(choices=[]),                                  # no answer at all (a blocked reply)
    completion("partial", finish="length"),                       # cut off
    completion(None),                                             # empty
])
def test_odd_replies_give_none_or_empty_without_crashing(reply):
    result = openai_provider(StubOpenAIClient(reply)).complete("prompt", "task", {})
    assert not result


def test_an_unexpected_error_gives_none():
    assert openai_provider(StubOpenAIClient(ValueError("odd"))).complete("prompt", "task", {}) is None


def test_a_missing_api_key_gives_none_instead_of_crashing():
    provider = OpenAICompatibleProvider(PROVIDER_CONFIGS["gemini"])  # no client, and conftest removed the keys
    assert provider.complete("prompt", "task", {}) is None


def test_the_anthropic_provider_returns_text_and_reports_refusals():
    text_block = SimpleNamespace(type="text", text='{"ok": 1}')
    answered = SimpleNamespace(stop_reason="end_turn", content=[text_block])
    refused = SimpleNamespace(stop_reason="refusal", content=[])
    beta = SimpleNamespace(messages=SimpleNamespace(create=lambda **params: answered))
    client = SimpleNamespace(beta=beta)
    assert AnthropicProvider(PROVIDER_CONFIGS["anthropic"], client=client).complete("p", "t", {}) == '{"ok": 1}'
    client.beta = SimpleNamespace(messages=SimpleNamespace(create=lambda **params: refused))
    assert AnthropicProvider(PROVIDER_CONFIGS["anthropic"], client=client).complete("p", "t", {}) is None


def test_the_anthropic_provider_retries_a_connection_failure():
    calls = []

    def create(**params):
        calls.append(params)
        raise anthropic.APIConnectionError(request=REQUEST)

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    assert AnthropicProvider(PROVIDER_CONFIGS["anthropic"], client=client).complete("p", "t", {}) is None
    assert len(calls) == base_ai_provider.RATE_LIMIT_RETRIES + 1


# ---------- substitutability: AIManager works the same with any BaseAIProvider ----------

def test_the_manager_gives_the_same_answer_with_a_fake_and_a_real_provider():
    real = openai_provider(StubOpenAIClient(completion(json.dumps(PROFILE))))
    fake = FakeAIProvider(PROFILE)
    for provider in (real, fake):
        assert isinstance(provider, BaseAIProvider)
        assert AIManager(provider).extract_candidate_profile("resume") == PROFILE


# ---------- choosing the provider: AIManager.create_provider ----------

@pytest.mark.parametrize("name, expected", [("qwen", OpenAICompatibleProvider), ("gemini", OpenAICompatibleProvider),
                                            ("openai", OpenAICompatibleProvider), ("anthropic", AnthropicProvider)])
def test_the_factory_builds_each_provider(name, expected):
    provider = AIManager.create_provider(name)
    assert type(provider) is expected and isinstance(provider, BaseAIProvider)
    assert provider.label == PROVIDER_CONFIGS[name]["label"] and provider.model == PROVIDER_CONFIGS[name]["default_model"]


def test_the_factory_uses_the_requested_model_and_falls_back_on_an_unknown_provider():
    assert AIManager.create_provider("openai", "my-model").model == "my-model"
    assert AIManager.create_provider(" NoSuchAI ").label == PROVIDER_CONFIGS["gemini"]["label"]


def test_the_factory_reads_the_environment(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "qwen")
    monkeypatch.setenv("AI_MODEL", "qwen-test")
    provider = AIManager.from_environment()._provider
    assert provider.label == "Alibaba Qwen" and provider.model == "qwen-test"
