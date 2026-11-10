"""CachingAIProvider: wraps any AI provider and remembers its replies, so the same input gives the same output."""

import hashlib
import json
import logging
import os
import threading
from collections.abc import Callable

from ai.base_ai_provider import BaseAIProvider


class CachingAIProvider(BaseAIProvider):
    """Optional (AIManager.from_environment uses it only when AI_CACHE=on; by default every record goes to the live
    AI). A provider that stands in for another one (it is a BaseAIProvider too, so AIManager can't tell the difference):
    a prompt it has answered before gets the saved reply; a new one goes to the wrapped provider and its reply is
    saved. That makes runs repeatable (the same resume and listings give the same matches every time) and lets a
    demo replay earlier answers with no network or API key.

    A reply AIManager found unusable is forgotten (discard_reply), so a bad answer is never replayed. Replies are
    kept in a JSON file; a missing or corrupt file just starts an empty cache.
    """

    def __init__(self, inner: BaseAIProvider, path: str) -> None:
        # The wrapped provider is handed in (aggregation) and passed to the base class as the client, so the base
        # class never looks for an API key of its own: only a cache miss needs the wrapped provider's key
        super().__init__({"key": "cache", "label": inner.label, "key_env": "", "default_model": inner.model},
                         inner.model, client=inner)
        self.__inner = inner
        self.__path = path
        self.__lock = threading.Lock()  # web searches run in background threads
        self.__replies = self.__load()

    @property
    def size(self) -> int:
        """How many replies are saved."""
        with self.__lock:
            return len(self.__replies)

    def _create_client(self, api_key: str) -> object:
        return self.__inner  # never needed: the wrapped provider is given as the client in __init__

    def _attempts(self, client: object, prompt: str, task_name: str,
                  schema: dict) -> list[Callable[[], str | None]]:
        """One attempt: the saved reply, else the wrapped provider's (which does its own retries)."""
        return [lambda: self.__reply(prompt, task_name, schema)]

    def discard_reply(self, prompt: str, task_name: str, schema: dict) -> None:
        """Forgets a saved reply that turned out unusable, so asking again reaches the real AI."""
        with self.__lock:
            if self.__replies.pop(self.__key(prompt, task_name, schema), None) is not None:
                self.__save()

    def __reply(self, prompt: str, task_name: str, schema: dict) -> str | None:
        key = self.__key(prompt, task_name, schema)
        with self.__lock:
            saved = self.__replies.get(key)
        if saved is not None:
            logging.info(f"[{task_name}] Using the saved AI reply")
            return saved
        reply = self.__inner.complete(prompt, task_name, schema)
        if reply is not None:  # a failure is not saved: the next run tries the AI again
            with self.__lock:
                self.__replies[key] = reply
                self.__save()
        return reply

    def __key(self, prompt: str, task_name: str, schema: dict) -> str:
        """The same task, model, prompt and schema always give the same key; any change gives a new one."""
        text = json.dumps([task_name, self.__inner.model, prompt, schema], sort_keys=True)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def __load(self) -> dict[str, str]:
        try:
            with open(self.__path, encoding="utf-8") as file:
                replies = json.load(file)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            logging.warning(f"Could not read the AI reply cache {self.__path} ({type(error).__name__}); starting empty")
            return {}
        if not isinstance(replies, dict):
            logging.warning(f"The AI reply cache {self.__path} is not in the expected format; starting empty")
            return {}
        return {key: value for key, value in replies.items() if isinstance(value, str)}

    def __save(self) -> None:
        """Writes the cache to a temporary file, then swaps it in, so a crash mid-write never leaves half a file.
        A cache that can't be written only costs speed, so the error is logged, not raised."""
        temporary = f"{self.__path}.tmp"
        try:
            os.makedirs(os.path.dirname(self.__path) or ".", exist_ok=True)
            with open(temporary, "w", encoding="utf-8") as file:
                json.dump(self.__replies, file)
            os.replace(temporary, self.__path)
        except OSError as error:
            logging.warning(f"Could not save the AI reply cache ({type(error).__name__}: {error})")
