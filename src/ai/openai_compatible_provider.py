"""OpenAICompatibleProvider: Qwen, Gemini and OpenAI, which share the OpenAI request format."""

import json
import logging
from collections.abc import Callable

import openai

from ai.base_ai_provider import API_TIMEOUT_SECONDS, BaseAIProvider


class OpenAICompatibleProvider(BaseAIProvider):
    """Qwen, Gemini and OpenAI, all through the OpenAI SDK (they share its request format)."""

    def _create_client(self, api_key: str) -> object:
        # max_retries=0 turns off the SDK's hidden retries: BaseAIProvider retries, so each try is visible
        return openai.OpenAI(api_key=api_key, base_url=self._config.get("base_url"),
                             timeout=API_TIMEOUT_SECONDS, max_retries=0)

    def _attempts(self, client: object, prompt: str, task_name: str,
                  schema: dict) -> list[Callable[[], str | None]]:
        # Attempt 1: strict mode - the provider guarantees the reply matches the schema
        schema_mode = {"type": "json_schema", "json_schema": {"name": task_name, "strict": True, "schema": schema}}
        # Attempt 2 (only if attempt 1 is rejected): plain JSON mode, with the schema pasted into the prompt
        fallback_prompt = (
            prompt + f"\n\nReturn ONLY a single JSON object matching this structure:\n{json.dumps(schema, indent=2)}")
        return [lambda: self._request(client, prompt, schema_mode),
                lambda: self._request(client, fallback_prompt, {"type": "json_object"})]

    def _request(self, client: object, prompt: str, response_format: dict) -> str | None:
        """One request through the OpenAI client."""
        params = {"model": self._model, "messages": [{"role": "user", "content": prompt}],
                  "response_format": response_format}
        if self._config.get("temperature") is not None:
            params["temperature"] = self._config.get("temperature")
        completion = client.chat.completions.create(**params)  # type: ignore[attr-defined]

        if not completion.choices:
            # e.g. Gemini can send no choices at all when it blocks a reply
            logging.error(f"Response from {self._config['key']} ({self._model}) had no answer in it.")
            return None
        choice = completion.choices[0]
        if choice.finish_reason == "length":
            logging.error(f"Response from {self._config['key']} ({self._model}) was cut off at its length limit.")
            return None
        if not choice.message.content:
            logging.error(f"Empty response from {self._config['key']} ({self._model}); "
                          f"finish_reason={choice.finish_reason}")
        return choice.message.content
