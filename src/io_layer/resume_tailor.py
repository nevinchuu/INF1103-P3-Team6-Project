"""ResumeTailor: the steps every resume goes through when it is tailored to one job. Each subclass (WordResumeTailor,
PdfResumeTailor) fills in how its kind of resume is rewritten and checked."""

from abc import ABC, abstractmethod
from collections.abc import Callable

from ai_layer.ai_manager import AIManager
from io_layer.docx_editor import DocxEditor
from logic_layer.logic_manager import LogicManager


class ResumeTailor(ABC):
    """Abstract class. tailor() is a template method: it asks the AI, reports progress, then checks the answer, in
    the same order for every resume. A subclass says which resumes it handles and how it asks and checks.
    TailoringService picks a tailor with handles(), so it never checks the file type itself."""

    def __init__(self, ai: AIManager, logic: LogicManager, docx: DocxEditor) -> None:
        # Association: the managers and the Word editor are handed in by TailoringService; the tailor
        # works with them but is not their owner
        self._ai = ai
        self._logic = logic
        self._docx = docx

    def tailor(self, resume_path: str, resume_text: str, job: dict,
               on_progress: Callable[[int, str], None]) -> dict | None:
        """The tailored resume, as TailoringService needs it: resume (or None), paragraphs (or None), docx_bytes,
        changes and notes (records, not sentences), removed_skills, skill_evidence, parts and avoid_keywords.
        None if the AI could not tailor it."""
        # The job's words this resume shows (a reworded point may name one only where its original does) and the
        # ones it doesn't show (never added)
        use_keywords, avoid_keywords = self._logic.job_keywords(job, resume_text)
        # The path and both keyword lists ride along in the draft, so _check needs only the draft and the text
        extra = {"resume_path": resume_path, "avoid_keywords": avoid_keywords, "use_keywords": use_keywords}
        draft = self._ask_ai(resume_path, resume_text, job)
        if draft is None:
            return None
        on_progress(92, "Checking the result...")
        checked = self._check({**draft, **extra}, resume_text)

        def feedback(notes: list[dict]) -> list[str]:
            """Why the checks threw changes out, for the AI (the page words the notes for the user, in IOManager), e.g.
            ['"Built Power BI dashboards...": Tableau (not named in this paragraph)']. Described, not ordered: in
            tests, "keep the original words instead of 'complex'" led the AI to new words like "strong"."""
            lines = []
            for note in notes:
                problems = [f"{skill} (not in the resume)" for skill in note.get("skills", [])]
                problems += [f"{name} (not the resume's qualification)" for name in note.get("qualifications", [])]
                problems += [f"{tool} (not named in this paragraph)" for tool in note.get("tools", [])]
                problems += [f'"{word}" (stronger than the resume says)' for word in note.get("words", [])]
                if note.get("number") or note["kind"].endswith("number_not_in_resume"):
                    problems.append("a number the resume doesn't have")
                where = note.get("start") or note.get("where") or note.get("title") or note.get("heading") or ""
                if problems and where:
                    lines.append(f'"{where}...": {", ".join(problems)}')
            return lines

        # A second try, only when the checks threw something out: the AI is told what and why, and the better
        # of the two checked results is kept: the one with more changes that passed (a thrown-out change never
        # reaches the file, so it costs nothing), then the one with fewer thrown out; a tie keeps the first
        first_problems = feedback(checked.get("notes", []))
        if first_problems:
            on_progress(95, "Fixing a few changes...")
            retry = self._ask_ai(resume_path, resume_text, job, first_problems)
            if retry is not None:
                second = self._check({**retry, **extra}, resume_text)
                second_problems = feedback(second.get("notes", []))
                if ((-len(second.get("changes", [])), len(second_problems))
                        < (-len(checked.get("changes", [])), len(first_problems))):
                    checked = second
        return {**checked, "avoid_keywords": avoid_keywords}

    @abstractmethod
    def handles(self, resume_path: str) -> bool:
        """True if this tailor can tailor the resume at resume_path."""

    @abstractmethod
    def _ask_ai(self, resume_path: str, resume_text: str, job: dict, feedback: list[str] | None = None) -> dict | None:
        """The AI's rewrite of the resume for the job, or None if the AI failed. feedback: on the second try, why
        changes from the first were thrown out."""

    @abstractmethod
    def _check(self, draft: dict, resume_text: str) -> dict:
        """The AI's rewrite with the logic rules applied (nothing the resume doesn't show), and the Word file.
        The draft also holds "resume_path", "avoid_keywords" and "use_keywords"."""
