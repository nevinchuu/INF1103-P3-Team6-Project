"""AnthropicProvider: Claude, through the Anthropic SDK."""

import logging
from collections.abc import Callable

import anthropic

from ai.base_ai_provider import API_TIMEOUT_SECONDS, BaseAIProvider


class AnthropicProvider(BaseAIProvider):
    """Claude, through the Anthropic SDK with structured outputs."""

    def _create_client(self, api_key: str) -> object:
        return anthropic.Anthropic(api_key=api_key, timeout=API_TIMEOUT_SECONDS, max_retries=0)

    def _attempts(self, client: object, prompt: str, task_name: str,
                  schema: dict) -> list[Callable[[], str | None]]:
        return [lambda: self._request(client, prompt, task_name, schema)]

    def _request(self, client: object, prompt: str, task_name: str, schema: dict) -> str | None:
        """One request through the Anthropic SDK. Claude 5-generation models also get an explicit effort level
        and server-side refusal fallback; Haiku 4.5 supports neither, so it gets a plain structured request."""
        params = {
            "model": self._model,
            "max_tokens": 16000,  # upper limit on reply length
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
            "messages": [{"role": "user", "content": prompt}],
        }
        if self._model.startswith("claude-haiku"):
            response = client.messages.create(**params)  # type: ignore[attr-defined]
        else:
            params["output_config"]["effort"] = "medium"  # how much the model thinks before answering
            response = client.beta.messages.create(  # type: ignore[attr-defined]
                **params, betas=["server-side-fallback-2026-07-01"], fallbacks="default")

        # stop_reason says why Claude stopped writing; only "end_turn" means a complete answer
        if response.stop_reason == "refusal":
            logging.error(f"[{task_name}] Claude declined the request (refusal).")
            return None
        if response.stop_reason == "max_tokens":
            logging.error(f"[{task_name}] Claude's response was cut off at max_tokens.")
            return None
        return next((block.text for block in response.content if block.type == "text"), None)
