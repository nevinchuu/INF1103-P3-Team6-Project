"""Test doubles: stand-ins for the live AI, so every test runs without an API key or a network.

FakeAIProvider is a BaseAIProvider like the real providers (base_ai_provider.py), so AIManager
cannot tell the difference (Liskov substitution). ScriptedAIManager skips the prompt/JSON pipeline for the
end-to-end app tests, which care about what the app does with an answer, not how it was parsed.
"""

import json
from types import SimpleNamespace

import requests

from ai.ai_manager import AIManager
from ai.base_ai_provider import BaseAIProvider
from services.job_portal import JobPortal
from persistence.data_manager import Storage


class FakeAIProvider(BaseAIProvider):
    """Answers from a script instead of an API. replies are tried in order, then the last one repeats; a reply
    is raw text (what an AI would send back) or None (as if the API call had failed)."""

    def __init__(self, *replies: str | dict | None, label: str = "Fake AI") -> None:
        super().__init__({"key": "fake", "label": label, "key_env": "FAKE_API_KEY", "default_model": "fake-model"})
        self._replies = list(replies)
        self.calls: list[tuple[str, str]] = []  # (task name, prompt) for each call, newest last

    def _create_client(self, api_key: str) -> object:  # never called: complete is replaced below
        raise NotImplementedError

    def _attempts(self, client, prompt, task_name, schema):  # never called: complete is replaced below
        raise NotImplementedError

    def complete(self, prompt: str, task_name: str, schema: dict) -> str | None:
        self.calls.append((task_name, prompt))
        reply = self._replies.pop(0) if len(self._replies) > 1 else (self._replies[0] if self._replies else None)
        return json.dumps(reply) if isinstance(reply, dict) else reply


class ScriptedAIManager(AIManager):
    """An AIManager whose run_task is answered by responder(record, task_name) -> dict | None."""

    def __init__(self, responder) -> None:
        super().__init__(FakeAIProvider())
        self._responder = responder

    def run_task(self, task_name: str, record: dict) -> dict | None:
        return self._responder(record, task_name)


class FlakyStorage(Storage):
    """A Storage that wraps another and can be told to fail, to test how the apps cope with a store that can't
    be read or written (a locked or full disk). Like every Storage, it is a drop-in for the real ones."""

    def __init__(self, inner: Storage) -> None:
        self.inner = inner
        self.fail_reads = False
        self.fail_writes = False

    def _check(self, failing: bool) -> None:
        if failing:
            raise OSError("simulated: the saved jobs file is locked")

    def load_all(self) -> list[dict]:
        self._check(self.fail_reads)
        return self.inner.load_all()

    def get(self, job_url: str) -> dict | None:
        self._check(self.fail_reads)
        return self.inner.get(job_url)

    def insert(self, job: dict) -> bool:
        self._check(self.fail_writes)
        return self.inner.insert(job)

    def insert_many(self, jobs) -> int:
        self._check(self.fail_writes)
        return self.inner.insert_many(jobs)

    def update(self, job_url: str, changes: dict) -> bool:
        self._check(self.fail_writes)
        return self.inner.update(job_url, changes)

    def delete(self, job_url: str) -> bool:
        self._check(self.fail_writes)
        return self.inner.delete(job_url)

    def clear(self) -> None:
        self._check(self.fail_writes)
        self.inner.clear()


class FakePortal(JobPortal):
    """A job portal that lists what it is told to, instead of going online. search() gives the same listings for
    every job title, or the list for that title when given a dict; None means "the portal is down". It keeps
    JobPortal.fetch_listings, so the apps' search runs exactly as it would on the real portal (LSP)."""

    def __init__(self, listings: list[dict] | dict[str, list[dict] | None] | None, details: dict | None = None) -> None:
        self._listings = listings
        self._details = details
        self.searches: list[tuple[str, dict]] = []  # (job title, portal filters) for each search, newest last

    def search(self, search_query: str, limit: int = 10, portal_filters: dict | None = None) -> list[dict] | None:
        self.searches.append((search_query, portal_filters or {}))
        listings = self._listings.get(search_query) if isinstance(self._listings, dict) else self._listings
        return None if listings is None else [dict(job) for job in listings]

    def details(self, job_url: str) -> dict | None:
        return dict(self._details) if self._details is not None else None

    def filters_for(self, preferences: dict | None, step: str) -> dict:
        return {"step": step}


class StubHttp:
    """Stands in for the requests module in JobPortal: post/get answer from a script. Each reply is a
    JSON payload, or (payload, status) for an HTTP error status, or an exception to raise (e.g. a timeout)."""

    def __init__(self, post: list | None = None, get: list | None = None) -> None:
        self._replies = {"post": list(post or []), "get": list(get or [])}
        self.calls: list[tuple[str, str, dict]] = []  # (method, url, options)

    def _answer(self, method: str, url: str, options: dict) -> SimpleNamespace:
        self.calls.append((method, url, options))
        replies = self._replies[method]
        reply = replies.pop(0) if len(replies) > 1 else replies[0]
        if isinstance(reply, Exception):
            raise reply
        payload, status = reply if isinstance(reply, tuple) else (reply, 200)

        def raise_for_status() -> None:
            if status >= 400:
                raise requests.HTTPError(f"HTTP {status}")
        return SimpleNamespace(raise_for_status=raise_for_status, json=lambda: payload)

    def post(self, url: str, **options: object) -> SimpleNamespace:
        return self._answer("post", url, options)

    def get(self, url: str, **options: object) -> SimpleNamespace:
        return self._answer("get", url, options)
