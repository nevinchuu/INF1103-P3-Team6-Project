"""BaseAIProvider: the abstract class every AI service inherits (OpenAICompatibleProvider, AnthropicProvider)."""

import logging
import os
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import cast

import anthropic
import openai

API_TIMEOUT_SECONDS = 90       # longest wait for one AI reply before it counts as a failed try
RATE_LIMIT_RETRIES = 3         # retries per AI call when the provider is busy
MAX_RATE_LIMIT_WAIT = 90       # longer suggested waits usually mean the daily quota is used up


# 429 = rate limit/quota, 5xx/529 = provider overloaded, connection error/timeout:
# all usually temporary, so a provider waits and retries.
RETRYABLE_ERRORS = (
    openai.RateLimitError, openai.InternalServerError, openai.APIConnectionError,
    anthropic.RateLimitError, anthropic.InternalServerError, anthropic.OverloadedError,
    anthropic.APIConnectionError,
)


# Any other HTTP error (e.g. 400 bad request, 401 wrong key) - not worth retrying
STATUS_ERRORS = (openai.APIStatusError, anthropic.APIStatusError)


# A "sentinel": returned by one attempt to mean "the provider rejected this request format" (HTTP 400),
# which is different from None ("it failed").
FORMAT_REJECTED = object()


# Settings for each AI provider (AI_PROVIDER and AI_MODEL in .env pick one):
# key = name used in AI_PROVIDER, label = name shown to users (privacy notice), key_env = environment variable
# holding the API key, base_url = None for the SDK's own servers, temperature = None to leave it out
PROVIDER_CONFIGS = {
    "qwen": {"key": "qwen", "label": "Alibaba Qwen", "key_env": "QWEN_API_KEY", "default_model": "qwen-flash",
             "base_url": "https://ws-jqxl73dgs75w9q92.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
             "temperature": 0.0},
    # gemini-3.1-flash-lite reads "highest qualification" as "most recent", answering Secondary for a resume
    # whose education section ends with an O-Level, even when the prompt gives that exact case as an example.
    # That makes every Diploma+ job score education-not-met, so the default is a model that reads it right.
    # 3.5-flash-lite is the cheapest that does: it matches 3.5-flash on the qualification while analysing a
    # batch of 10 listings in about 5 seconds against 3.5-flash's 24, which a whole search is mostly made of.
    "gemini": {"key": "gemini", "label": "Google Gemini", "key_env": "GEMINI_API_KEY",
               "default_model": "gemini-3.5-flash-lite",
               "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "temperature": 0.0},
    "openai": {"key": "openai", "label": "OpenAI", "key_env": "OPENAI_API_KEY", "default_model": "gpt-5.5"},
    "anthropic": {"key": "anthropic", "label": "Anthropic Claude", "key_env": "ANTHROPIC_API_KEY",
                  "default_model": "claude-opus-5-5"},
}


class BaseAIProvider(ABC):
    """Abstract class: anything that turns a prompt into the raw text of an AI reply, plus the retry rules and
    lazy client every real provider shares.

    complete is the shared behaviour: it asks the subclass for its request formats (_attempts) and decides which
    failures are retried and which are final. A subclass supplies _create_client and _attempts.
    """

    def __init__(self, config: dict, model: str | None = None, client: object | None = None) -> None:
        self._config = config
        self._model = model or config["default_model"]
        self.__client = client  # created on first use (or injected, e.g. by a test)

    @property
    def label(self) -> str:
        """The service as people know it, e.g. "Google Gemini" (for the privacy notice)."""
        return self._config["label"]

    @property
    def model(self) -> str:
        """The model in use, e.g. "gemini-3.1-flash-lite"."""
        return self._model

    @abstractmethod
    def _create_client(self, api_key: str) -> object:
        """The SDK client for this provider."""

    @abstractmethod
    def _attempts(self, client: object, prompt: str, task_name: str,
                  schema: dict) -> list[Callable[[], str | None]]:
        """The request formats to try in order; each is a function that makes one request."""

    def discard_reply(self, prompt: str, task_name: str, schema: dict) -> None:
        """Told by AIManager when a reply couldn't be used. A real provider keeps nothing, so there is nothing to do;
        CachingAIProvider overrides this to forget the saved reply."""

    def __get_client(self) -> object:
        """The client, created on first use so a missing key is reported, not crashed on at import."""
        if self.__client is None:
            api_key = os.getenv(self._config["key_env"])
            if not api_key:
                raise RuntimeError(f"{self._config['key_env']} is not set (add it to .env or the environment)")
            self.__client = self._create_client(api_key)
            logging.info(f"Using {self._config['key']} ({self._model})")
        return self.__client

    def complete(self, prompt: str, task_name: str, schema: dict) -> str | None:
        """Sends the prompt and returns the reply text asked to match schema, or None on any failure
        (never raises for an API problem)."""
        try:
            client = self.__get_client()
        except RuntimeError as error:
            logging.error(f"[{task_name}] {error}")
            return None

        for attempt in self._attempts(client, prompt, task_name, schema):
            result = self.__with_retries(attempt, task_name)
            if result is not FORMAT_REJECTED:
                return cast(str | None, result)  # either the reply text, or None if it failed for another reason
        logging.error(f"[{task_name}] All response formats were rejected.")
        return None

    def __with_retries(self, request: Callable[[], str | None], task_name: str) -> str | None | object:
        """Runs one request, retrying temporary failures (busy server, rate limit, connection error, timeout).

        Returns the response text, None on failure, or FORMAT_REJECTED on HTTP 400 so the next format is tried.
        """
        for retry in range(RATE_LIMIT_RETRIES + 1):  # 1 try + up to 3 retries
            try:
                return request()
            except RETRYABLE_ERRORS as error:
                reason = f"HTTP {error.status_code}" if hasattr(error, "status_code") else "connection error/timeout"
                wait = self.__retry_delay(error)
                if retry == RATE_LIMIT_RETRIES or wait > MAX_RATE_LIMIT_WAIT:
                    logging.error(f"[{task_name}] AI API still unavailable ({reason}); giving up. {self.__short(error)}")
                    return None
                logging.warning(f"[{task_name}] AI API unavailable ({reason}); retrying in {wait:.0f}s...")
                time.sleep(wait)
            except STATUS_ERRORS as error:
                # Permanent problem (e.g. wrong API key): don't retry
                if error.status_code != 400:
                    logging.error(f"[{task_name}] AI API returned HTTP {error.status_code}: {self.__short(error)}")
                    return None
                # 400 often means "I don't support this request format" - let the next one be tried
                logging.warning(f"[{task_name}] Request rejected (HTTP 400: {self.__short(error)}); "
                                "trying fallback if any.")
                return FORMAT_REJECTED
            except Exception as error:
                # Anything unexpected fails this call only, so the caller can offer to try again
                logging.error(f"[{task_name}] Unexpected error calling the AI API ({type(error).__name__}: {error}).")
                logging.info(f"[{task_name}] Traceback of the unexpected error:", exc_info=True)
                return None
        return None

    @staticmethod
    def __short(error: Exception) -> str:
        """First line of an API error message, so logs stay readable."""
        message = getattr(error, "message", None) or str(error)
        match = re.search(r"'message': '([^'\\]+)", message)
        return (match.group(1) if match else message.splitlines()[0])[:200]

    @staticmethod
    def __retry_delay(error: Exception) -> float:
        """Reads the suggested wait from a retry-after header or the error message
        ("retry in 29.8s"), defaulting to 30s."""
        response = getattr(error, "response", None)
        header = response.headers.get("retry-after") if response is not None else None
        if header:
            try:
                return float(header) + 1
            except ValueError:
                pass
        match = re.search(r"retry in ([\d.]+)s", str(error))
        return float(match.group(1)) + 1 if match else 30.0
