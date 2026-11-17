"""GeminiProvider: Google Gemini, through its OpenAI-compatible endpoint with the OpenAI SDK."""

import json
import logging
import os
import re
import time
from collections.abc import Callable
from typing import cast

import openai

from ai_layer.ai_provider import AIProvider

API_TIMEOUT_SECONDS = 90       # longest wait for one AI reply before it counts as a failed try
RATE_LIMIT_RETRIES = 3         # retries per AI call when the provider is busy
MAX_RATE_LIMIT_WAIT = 90       # longer suggested waits usually mean the daily quota is used up


# 429 = rate limit/quota, 5xx = provider overloaded, connection error/timeout:
# all usually temporary, so the provider waits and retries.
RETRYABLE_ERRORS = (openai.RateLimitError, openai.InternalServerError, openai.APIConnectionError)


# Any other HTTP error (e.g. 400 bad request, 401 wrong key) - not worth retrying
STATUS_ERRORS = (openai.APIStatusError,)


# A "sentinel": returned by one attempt to mean "Gemini rejected this request format" (HTTP 400),
# which is different from None ("it failed").
FORMAT_REJECTED = object()


LABEL = "Google Gemini"   # the name shown to users (privacy notice)
KEY_ENV = "GEMINI_API_KEY"   # the environment variable (.env) holding the API key
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"   # Gemini's OpenAI-compatible endpoint
# gemini-3.1-flash-lite reads "highest qualification" as "most recent", answering Secondary for a resume
# whose education section ends with an O-Level, even when the prompt gives that exact case as an example.
# That makes every Diploma+ job score education-not-met, so the default is a model that reads it right.
# 3.5-flash-lite is the cheapest that does: it matches 3.5-flash on the qualification while analysing a
# batch of 10 listings in about 5 seconds against 3.5-flash's 24, which a whole search is mostly made of.
DEFAULT_MODEL = "gemini-3.5-flash-lite"
TEMPERATURE = 0.0   # the same prompt gives the same answer as far as the model allows


class GeminiProvider(AIProvider):
    """Turns a prompt into the raw text of Gemini's reply. It tries two request formats in order (strict schema
    mode, then plain JSON mode), retries temporary failures (busy server, rate limit, timeout) and reports a
    missing or rejected API key. AIManager uses it only through the AIProvider interface."""

    def __init__(self, model: str | None = None, client: object | None = None) -> None:
        self.__model = model or DEFAULT_MODEL
        self.__client = client  # created on first use (or injected, e.g. by a test)
        self.__key_rejected = False  # set once Google refuses the key; it stays wrong until the app restarts

    @property
    def provider_name(self) -> str:
        """The service and model, e.g. "Google Gemini (gemini-3.5-flash-lite)" (for the privacy notice). The model is
        in the name so a cache keyed on it changes when the model does."""
        return f"{LABEL} ({self.__model})"

    @property
    def setup_problem(self) -> str:
        """ "GEMINI_API_KEY is not set" or "GEMINI_API_KEY was rejected by Google Gemini", else ""."""
        if self.__key_rejected:
            return f"{KEY_ENV} was rejected by {LABEL}"
        if self.__client is None and not os.getenv(KEY_ENV):
            return f"{KEY_ENV} is not set"
        return ""

    def discard_reply(self, prompt: str, task_name: str, schema: dict) -> None:
        """Told by AIManager when a reply couldn't be used. This provider keeps nothing, so there is nothing to do;
        CachingAIProvider overrides this to forget the saved reply."""

    def complete(self, prompt: str, task_name: str, schema: dict) -> str | None:
        """Sends the prompt and returns the reply text asked to match schema, or None on any failure
        (never raises for an API problem)."""
        try:
            client = self.__get_client()
        except RuntimeError as error:
            logging.error(f"[{task_name}] {error}")
            return None

        for attempt in self.__attempts(client, prompt, task_name, schema):
            result = self.__with_retries(attempt, task_name)
            if result is not FORMAT_REJECTED:
                return cast(str | None, result)  # either the reply text, or None if it failed for another reason
        logging.error(f"[{task_name}] All response formats were rejected.")
        return None

    def __get_client(self) -> object:
        """The client, created on first use so a missing key is reported, not crashed on at import."""
        if self.__client is None:
            api_key = os.getenv(KEY_ENV)
            if not api_key:
                raise RuntimeError(f"{KEY_ENV} is not set (add it to .env or the environment)")
            # max_retries=0 turns off the SDK's hidden retries: this class retries, so each try is visible
            self.__client = openai.OpenAI(api_key=api_key, base_url=BASE_URL, timeout=API_TIMEOUT_SECONDS,
                                          max_retries=0)
            logging.info(f"Using {LABEL} ({self.__model})")
        return self.__client

    def __attempts(self, client: object, prompt: str, task_name: str,
                   schema: dict) -> list[Callable[[], str | None]]:
        """The request formats to try in order; each is a function that makes one request."""
        # Attempt 1: strict mode - Gemini guarantees the reply matches the schema
        schema_mode = {"type": "json_schema", "json_schema": {"name": task_name, "strict": True, "schema": schema}}
        # Attempt 2 (only if attempt 1 is rejected): plain JSON mode, with the schema pasted into the prompt
        fallback_prompt = (
            prompt + f"\n\nReturn ONLY a single JSON object matching this structure:\n{json.dumps(schema, indent=2)}")
        return [lambda: self.__request(client, prompt, schema_mode),
                lambda: self.__request(client, fallback_prompt, {"type": "json_object"})]

    def __request(self, client: object, prompt: str, response_format: dict) -> str | None:
        """One request through the OpenAI client."""
        completion = client.chat.completions.create(  # type: ignore[attr-defined]
            model=self.__model, messages=[{"role": "user", "content": prompt}], response_format=response_format,
            temperature=TEMPERATURE)

        if not completion.choices:
            # Gemini can send no choices at all when it blocks a reply
            logging.error(f"Response from {LABEL} ({self.__model}) had no answer in it.")
            return None
        choice = completion.choices[0]
        if choice.finish_reason == "length":
            logging.error(f"Response from {LABEL} ({self.__model}) was cut off at its length limit.")
            return None
        if not choice.message.content:
            logging.error(f"Empty response from {LABEL} ({self.__model}); finish_reason={choice.finish_reason}")
        return choice.message.content

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
                # Permanent problem (e.g. wrong API key): don't retry. 401/403 mean the key was refused; Gemini
                # answers a bad key with 400 "API key not valid", so that one is recognised by its message
                if error.status_code in (401, 403) or "api key" in self.__short(error).lower():
                    self.__key_rejected = True
                    logging.error(f"[{task_name}] The API key was rejected (HTTP {error.status_code}): "
                                  f"{self.__short(error)}")
                    return None
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
