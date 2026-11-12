"""TailoringService: tailors a resume to one job, the steps both apps share (the web app's Tailor button, and the
console's job details)."""

import os
from collections.abc import Callable

from ai.ai_manager import AIManager
from io_layer.docx_editor import DocxEditor
from io_layer.io_manager import IOManager
from logic.logic_manager import LogicManager
from tailoring.pdf_resume_tailor import PdfResumeTailor
from tailoring.resume_tailor import ResumeTailor
from services.search_service import SearchService
from tailoring.word_resume_tailor import WordResumeTailor


class TailoringService:
    """One resume tailored for one job, through the managers: the full job description (portal, via SearchService),
    the AI's rewrite and the checks that nothing new was added (a ResumeTailor for the kind of resume), the
    skills the result shows (LogicManager) and the wording of the notes (IOManager). Returns what the apps show; it shows nothing itself."""

    def __init__(self, io: IOManager, ai: AIManager, logic: LogicManager, search: SearchService) -> None:
        # Aggregation: the managers and the search service are handed in and shared with the rest of the app
        self._io = io
        self._ai = ai
        self._logic = logic
        self._search = search
        # Composition: the Word editor and the tailors are created here and used by nothing else. Each tailor
        # handles one kind of resume; the PDF one takes any resume, so it goes last
        self.__docx = DocxEditor()
        self.__tailors: list[ResumeTailor] = [WordResumeTailor(ai, logic, io, self.__docx),
                                              PdfResumeTailor(ai, logic, io, self.__docx)]

    def tailor(self, job: dict, resume_path: str, resume_text: str,
               on_progress: Callable[[int, str], None] | None = None) -> dict | None:
        """The tailored resume for one job, as the page shows it, or None if the AI could not tailor it.
        on_progress(percent, message) reports each stage (the web app's progress bar; it may raise InterruptedError
        to cancel)."""
        on_progress = on_progress or (lambda percent, message: None)
        on_progress(5, "Getting the full job description...")
        details = self._search.job_details(job["job_url"])
        # Without the description the AI still has the job's title and skills
        full_job = {**job, **details} if details else job

        # A job's skill card was worked out for the resume that found it, so for another resume it doesn't
        # apply: the AI is not told those skills, and none of them count as shown by this resume
        resume_name = os.path.basename(resume_path)
        job_for_resume, same_resume = self._logic.resume_view(full_job, resume_name)
        # Which of the job's words the AI may use (the ones this resume shows)
        use_keywords, avoid_keywords = self._logic.job_keywords(job_for_resume, resume_text)

        on_progress(25, "Tailoring your resume with AI (about 20-40 seconds)...")
        # Polymorphism: every tailor is used the same way, whatever kind of resume it handles
        tailor = next(tailor for tailor in self.__tailors if tailor.handles(resume_path))
        tailored = tailor.tailor(resume_path, resume_text, job_for_resume, use_keywords, avoid_keywords, on_progress)
        if tailored is None:
            return None
        return self.__result(job, resume_name, same_resume, avoid_keywords, tailored, details)

    def __result(self, job: dict, resume_name: str, same_resume: bool, avoid_keywords: list[str], tailored: dict,
                details: dict | None) -> dict:
        """What the tailored page shows: the new resume, the changes and notes, and which of the job's skills it shows."""
        # Which of the card's skills apply to this resume, and which the job wants but it doesn't show
        skills = self._logic.tailoring_skills(job, same_resume, avoid_keywords, tailored["removed_skills"])
        matched = skills["matched"]
        return {
            "job": job,
            "resume_name": resume_name,
            "matched_with": None if same_resume else job.get("resume"),
            "resume": tailored["resume"],
            "paragraphs": tailored["paragraphs"],
            "docx_bytes": tailored["docx_bytes"],
            "changes": tailored["changes"],
            "not_added": skills["not_added"],
            "notes": self._io.describe_tailoring_notes(tailored["notes"]),
            "had_description": details is not None,
            "is_open": details["is_open"] if details else True,
            "required": job.get("required_skills", []),
            "matched": matched,
            # which of the job card's matched skills the tailored resume still shows
            "shown": self._logic.skills_shown(matched, tailored["skill_evidence"], tailored["parts"]),
        }