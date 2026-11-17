"""AIProvider: the interface AIManager works through, so any AI service (or a wrapper around one) can be plugged in."""

from abc import ABC, abstractmethod


class AIProvider(ABC):
    """Anything that turns a prompt into the raw text of an AI reply. No constructor and no shared code: each
    implementation (GeminiProvider, CachingAIProvider) does it its own way."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """The service as people show it, e.g. "Google Gemini (gemini-3.5-flash-lite)" (for the privacy notice)."""

    @property
    @abstractmethod
    def setup_problem(self) -> str:
        """Why no AI call can work until the setup is fixed, e.g. "GEMINI_API_KEY is not set", or "" when nothing is
        known to be wrong. Lets the pages tell a missing or rejected key apart from a busy or unreachable AI."""

    @abstractmethod
    def complete(self, prompt: str, task_name: str, schema: dict) -> str | None:
        """The reply text asked to match schema, or None when the AI could not give one."""

    @abstractmethod
    def discard_reply(self, prompt: str, task_name: str, schema: dict) -> None:
        """Told that a reply could not be used, so an implementation that keeps replies can forget it."""
