"""Test doubles: stand-ins for the live AI, so every test runs without an API key or a network.

FakeAIProvider is an AIProvider like the real providers (ai_provider.py), so AIManager
cannot tell the difference (Liskov substitution). ScriptedAIManager skips the prompt/JSON pipeline for the
end-to-end app tests, which care about what the app does with an answer, not how it was parsed.
"""

import copy
import json
import re
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from types import SimpleNamespace

import psycopg
import requests

from ai_layer.ai_manager import AIManager
from ai_layer.ai_provider import AIProvider
from data_layer.job_portal import JobPortal
from data_layer.data_manager import Storage
from data_layer.postgres_storage import (CLEAR_SQL, COUNT_SQL, DELETE_SQL, GET_SQL, INSERT_SQL, LOAD_ALL_SQL, SCHEMA_SQL,
                              SEARCH_SQL, SELECT_FOR_UPDATE_SQL, UPDATE_SQL)


class FakeAIProvider(AIProvider):
    """Answers from a script instead of an API. replies are tried in order, then the last one repeats; a reply
    is raw text (what an AI would send back) or None (as if the API call had failed)."""

    def __init__(self, *replies: str | dict | None, name: str = "Fake AI", setup_problem: str = "") -> None:
        self._name = name
        self._replies = list(replies)
        self._setup_problem = setup_problem  # e.g. "GEMINI_API_KEY is not set", to play a missing key
        self.calls: list[tuple[str, str]] = []  # (task name, prompt) for each call, newest last
        self.discarded: list[tuple[str, str]] = []  # (task name, prompt) of each reply AIManager could not use

    @property
    def provider_name(self) -> str:
        return self._name

    @property
    def setup_problem(self) -> str:
        return self._setup_problem

    def complete(self, prompt: str, task_name: str, schema: dict) -> str | None:
        self.calls.append((task_name, prompt))
        reply = self._replies.pop(0) if len(self._replies) > 1 else (self._replies[0] if self._replies else None)
        return json.dumps(reply) if isinstance(reply, dict) else reply

    def discard_reply(self, prompt: str, task_name: str, schema: dict) -> None:
        self.discarded.append((task_name, prompt))


class ScriptedAIManager(AIManager):
    """An AIManager whose run_task is answered by responder(record, task_name) -> dict | None."""

    def __init__(self, responder) -> None:
        super().__init__(FakeAIProvider())
        self._responder = responder

    def run_task(self, task_name: str, record: dict) -> dict | None:
        return self._responder(record, task_name)


class InMemoryStorage(Storage):
    """A Storage that keeps the jobs in a list, so tests need no file or database. It fills in only the six
    abstract methods, so the shared insert_many, search and count_by_status_and_outcome of Storage are the ones
    that run (and stay covered). Jobs are copied going in and coming out, as a real store would."""

    def __init__(self, jobs: Iterable[dict] = ()) -> None:
        self.__jobs: list[dict] = []
        self.__lock = threading.Lock()  # web searches run in background threads
        for job in jobs:
            self.insert(job)

    def __numbered(self) -> list[dict]:
        """Copies of the jobs, each with its "ID" (its position, from 0). Called with the lock held."""
        return [{**copy.deepcopy(job), "ID": position} for position, job in enumerate(self.__jobs)]

    def load_all(self) -> list[dict]:
        with self.__lock:
            return self.__numbered()

    def get(self, job_url: str) -> dict | None:
        with self.__lock:
            return next((job for job in self.__numbered() if job["job_url"] == job_url), None)

    def insert(self, job: dict) -> bool:
        self._check_new_job(job)
        with self.__lock:
            if any(saved["job_url"] == job["job_url"] for saved in self.__jobs):
                return False
            self.__jobs.append({key: value for key, value in copy.deepcopy(job).items() if key != "ID"})
            return True

    def update(self, job_url: str, changes: dict) -> bool:
        self._check_changes(changes)
        with self.__lock:
            saved = next((job for job in self.__jobs if job["job_url"] == job_url), None)
            if saved is None:
                return False
            saved.update({key: copy.deepcopy(value) for key, value in changes.items() if key != "ID"})
            return True

    def delete(self, job_url: str) -> bool:
        with self.__lock:
            remaining = [job for job in self.__jobs if job["job_url"] != job_url]
            changed = len(remaining) != len(self.__jobs)
            self.__jobs = remaining
            return changed

    def clear(self) -> None:
        with self.__lock:
            self.__jobs = []


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


class FakePostgres:
    """A tiny stand-in for a psycopg connection pool: it keeps the rows in a list and recognises each statement of
    PostgresStorage by comparing its text with the constants in postgres_storage (an unknown one is an
    AssertionError). It also plays the connection, as connection() hands out itself. Every (sql, params) is kept
    in statements, and setting fail_with makes the next connection() raise that error, as a database that is down."""

    class Result:
        def __init__(self, rows: list[dict] | None = None, rowcount: int = 0) -> None:
            self.rows = rows or []
            self.rowcount = rowcount

        def fetchall(self) -> list[dict]:
            return self.rows

        def fetchone(self) -> dict | None:
            return self.rows[0] if self.rows else None

    def __init__(self) -> None:
        self.rows: list[dict] = []  # one dict per job: job_url, the copied columns, and data (the job as a dict)
        self.statements: list[tuple[str, tuple | None]] = []
        self.fail_with: Exception | None = None
        self.opened = False

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.opened = False

    @contextmanager
    def connection(self) -> Iterator["FakePostgres"]:
        if self.fail_with is not None:
            error, self.fail_with = self.fail_with, None
            raise error
        yield self

    def execute(self, sql: str, params: tuple | None = None) -> "FakePostgres.Result":
        self.statements.append((sql, params))
        columns = ("title", "company", "location", "resume", "note", "status", "match_outcome")
        if sql == SCHEMA_SQL:
            return self.Result()
        if sql == LOAD_ALL_SQL:
            return self.Result([{"data": copy.deepcopy(row["data"])} for row in self.rows])
        if sql == GET_SQL:
            matches = [{"data": copy.deepcopy(row["data"]), "position": position}
                       for position, row in enumerate(self.rows) if row["job_url"] == params[0]]
            return self.Result(matches)
        if sql == INSERT_SQL:
            if any(row["job_url"] == params[0] for row in self.rows):
                return self.Result(rowcount=0)
            self.rows.append({"job_url": params[0], **dict(zip(columns, params[1:8])), "data": json.loads(params[8])})
            return self.Result(rowcount=1)
        if sql == SELECT_FOR_UPDATE_SQL:
            return self.Result([{"data": copy.deepcopy(row["data"])} for row in self.rows if row["job_url"] == params[0]])
        if sql == UPDATE_SQL:
            row = next(row for row in self.rows if row["job_url"] == params[8])
            row.update(dict(zip(columns, params[:7])), data=json.loads(params[7]))
            return self.Result(rowcount=1)
        if sql == DELETE_SQL:
            remaining = [row for row in self.rows if row["job_url"] != params[0]]
            removed, self.rows = len(self.rows) - len(remaining), remaining
            return self.Result(rowcount=removed)
        if sql == CLEAR_SQL:
            removed, self.rows = len(self.rows), []
            return self.Result(rowcount=removed)
        if sql == COUNT_SQL:
            counts: dict[tuple[str, str], int] = {}
            for row in self.rows:
                key = (row["status"] or "", row["match_outcome"] or "")
                counts[key] = counts.get(key, 0) + 1
            return self.Result([{"status": status, "outcome": outcome, "jobs": jobs}
                                for (status, outcome), jobs in counts.items()])
        if sql.startswith(SEARCH_SQL):
            # each parameter is %word% with \, % and _ escaped by a backslash: turn it back into a plain word
            words = [re.sub(r"\\(.)", r"\1", word[1:-1]).lower() for word in params or ()]
            found = []
            for position, row in enumerate(self.rows):
                text = " ".join(row[key] or "" for key in ("title", "company", "location")).lower()
                if all(word in text for word in words):
                    found.append({"data": copy.deepcopy(row["data"]), "position": position})
            return self.Result(found)
        raise AssertionError(f"FakePostgres does not know this statement: {sql}")


class FakePortal(JobPortal):
    """A job portal that lists what it is told to, instead of going online. search() gives the same listings for
    every job title, or the list for that title when given a dict; None means "the portal is down". It keeps
    JobPortal.fetch_listings, so the apps' search runs exactly as it would on the real portal (LSP)."""

    def __init__(self, listings: list[dict] | dict[str, list[dict] | None] | None, details: dict | None = None) -> None:
        self._listings = listings
        self._details = details
        self.searches: list[tuple[str, dict]] = []  # (job title, portal filters) for each search, newest last

    def search(self, query: str, limit: int = 10, filters: dict | None = None) -> list[dict] | None:
        self.searches.append((query, filters or {}))
        listings = self._listings.get(query) if isinstance(self._listings, dict) else self._listings
        return None if listings is None else [dict(job) for job in listings]

    def details(self, job_url: str) -> dict | None:
        return dict(self._details) if self._details is not None else None

    def filters_for(self, preferences: dict | None, step: str) -> dict:
        levels = (preferences or {}).get("position_levels")
        return {"step": step, **({"levels": levels} if levels else {})}


class StubHttp:
    """Stands in for the requests module in JobPortal: post/get answer from a script. Each reply is a
    JSON payload, or (payload, status) or (payload, status, headers) for an HTTP error status (e.g. 429 with
    {"Retry-After": "3"}), or an exception to raise (e.g. a timeout)."""

    def __init__(self, post: list | None = None, get: list | None = None) -> None:
        self._replies = {"post": list(post or []), "get": list(get or [])}
        self.calls: list[tuple[str, str, dict]] = []  # (method, url, options)

    def _answer(self, method: str, url: str, options: dict) -> SimpleNamespace:
        self.calls.append((method, url, options))
        replies = self._replies[method]
        reply = replies.pop(0) if len(replies) > 1 else replies[0]
        if isinstance(reply, Exception):
            raise reply
        payload, status, headers = (reply + ({},))[:3] if isinstance(reply, tuple) else (reply, 200, {})

        def raise_for_status() -> None:
            if status >= 400:
                raise requests.HTTPError(f"HTTP {status}")
        return SimpleNamespace(raise_for_status=raise_for_status, json=lambda: payload, status_code=status,
                               headers=headers)

    def post(self, url: str, **options: object) -> SimpleNamespace:
        return self._answer("post", url, options)

    def get(self, url: str, **options: object) -> SimpleNamespace:
        return self._answer("get", url, options)
