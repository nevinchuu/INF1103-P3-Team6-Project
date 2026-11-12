"""ResumeTailor: the steps every resume goes through when it is tailored to one job. Each subclass (WordResumeTailor,
PdfResumeTailor) fills in how its kind of resume is rewritten and checked."""

from abc import ABC, abstractmethod
from collections.abc import Callable

from ai.ai_manager import AIManager
from io_layer.docx_editor import DocxEditor
from io_layer.io_manager import IOManager
from logic.logic_manager import LogicManager


class ResumeTailor(ABC):
    """Abstract class. tailor() is a template method: it asks the AI, reports progress, then checks the answer, in
    the same order for every resume. A subclass says which resumes it handles and how it asks and checks.
    TailoringService picks a tailor with handles(), so it never checks the file type itself."""

    def __init__(self, ai: AIManager, logic: LogicManager, io: IOManager, docx: DocxEditor) -> None:
        # Aggregation: the managers and the Word editor are handed in by TailoringService and shared with it
        self._ai = ai
        self._logic = logic
        self._io = io
        self._docx = docx

    def tailor(self, resume_path: str, resume_text: str, job: dict, use_keywords: list[str],
               avoid_keywords: list[str], on_progress: Callable[[int, str], None]) -> dict | None:
        """The tailored resume, as TailoringService needs it: resume (or None), paragraphs (or None), docx_bytes,
        changes, notes, removed_skills, skill_evidence and parts. None if the AI could not tailor it."""
        draft = self._ask_ai(resume_path, resume_text, job, use_keywords, avoid_keywords)
        if draft is None:
            return None
        on_progress(92, "Checking the result...")
        return self._check(draft, resume_path, resume_text, avoid_keywords)

    @abstractmethod
    def handles(self, resume_path: str) -> bool:
        """True if this tailor can tailor the resume at resume_path."""

    @abstractmethod
    def _ask_ai(self, resume_path: str, resume_text: str, job: dict, use_keywords: list[str],
                avoid_keywords: list[str]) -> dict | None:
        """The AI's rewrite of the resume for the job, or None if the AI failed."""

    @abstractmethod
    def _check(self, draft: dict, resume_path: str, resume_text: str, avoid_keywords: list[str]) -> dict:
        """The AI's rewrite with the logic rules applied (nothing the resume doesn't show), and the Word file."""
