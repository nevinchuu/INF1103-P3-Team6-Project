"""TailoringService: tailors a resume to one job, the steps behind the web app's Tailor button."""

import os
from collections.abc import Callable

from ai_layer.ai_manager import AIManager
from io_layer.docx_editor import DocxEditor
from data_layer.data_manager import DataManager
from logic_layer.logic_manager import LogicManager
from io_layer.pdf_resume_tailor import PdfResumeTailor
from io_layer.resume_tailor import ResumeTailor
from io_layer.word_resume_tailor import WordResumeTailor


class TailoringService:
    """One resume tailored for one job, through the managers: the full job description (portal, via DataManager),
    the AI's rewrite and the checks that nothing new was added (a ResumeTailor for the kind of resume) and the
    skills the result shows (LogicManager). Returns the records (changes, notes) the IO manager words; it shows
    and words nothing itself."""

    def __init__(self, ai: AIManager, logic: LogicManager, data: DataManager, docx: DocxEditor) -> None:
        # Association: the logic and data managers are handed in and shared with the rest of the app. The AI manager
        # and the Word editor are only passed on to the tailors, so they are not stored here
        self.__logic = logic
        self.__data = data
        # Composition: the tailors are created here and used by nothing else. Each tailor handles one kind of
        # resume; the PDF one takes any resume, so it goes last
        self.__tailors: list[ResumeTailor] = [WordResumeTailor(ai, logic, docx), PdfResumeTailor(ai, logic, docx)]

    def tailor(self, job: dict, resume_path: str, resume_text: str,
               on_progress: Callable[[int, str], None] | None = None) -> dict | None:
        """The tailored resume for one job, as the page shows it, or None if the AI could not tailor it.
        on_progress(percent, message) reports each stage (the web app's progress bar; it may raise InterruptedError
        to cancel)."""
        on_progress = on_progress or (lambda percent, message: None)
        on_progress(5, "Getting the full job description...")
        details = self.__data.portal_job_details(job["job_url"])
        # Without the description the AI still has the job's title and skills
        full_job = {**job, **details} if details else job

        # A job's skill card was worked out for the resume that found it, so for another resume it doesn't
        # apply: the AI is not told those skills, and none of them count as shown by this resume
        resume_name = os.path.basename(resume_path)
        job_for_resume, same_resume = self.__logic.resume_view(full_job, resume_name)

        on_progress(25, "Tailoring your resume with AI (about 20-40 seconds)...")
        # Polymorphism: every tailor is used the same way, whatever kind of resume it handles
        tailored = self.__pick_tailor(resume_path).tailor(resume_path, resume_text, job_for_resume, on_progress)
        if tailored is None:
            return None
        return self.__result(job, resume_name, same_resume, tailored, details)

    def __pick_tailor(self, resume_path: str) -> ResumeTailor:
        """The first tailor that handles this resume (the PDF one handles any)."""
        return next(tailor for tailor in self.__tailors if tailor.handles(resume_path))

    def __result(self, job: dict, resume_name: str, same_resume: bool, tailored: dict, details: dict | None) -> dict:
        """What the tailored page shows: the new resume, the changes and notes (records), and which of the job's
        skills it shows."""
        # Which of the card's skills apply to this resume, and which the job wants but it doesn't show
        skills = self.__logic.tailoring_skills(job, same_resume, tailored["avoid_keywords"], tailored)
        return {
            "job": job,
            "resume_name": resume_name,
            "matched_with": None if same_resume else job.get("resume"),
            "resume": tailored["resume"],
            "paragraphs": tailored["paragraphs"],
            "docx_bytes": tailored["docx_bytes"],
            "changes": tailored["changes"],
            "not_added": skills["not_added"],
            "notes": tailored["notes"],
            "had_description": details is not None,
            "is_open": details["is_open"] if details else True,
            "required": job.get("required_skills", []),
            "matched": skills["matched"],
            # which of the job card's matched skills the tailored resume still shows
            "shown": skills["shown"],
        }
