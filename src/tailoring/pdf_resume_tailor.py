"""PdfResumeTailor: a resume rewritten from its text as a new Word file."""

from tailoring.resume_tailor import ResumeTailor


class PdfResumeTailor(ResumeTailor):
    """Tailors from the resume's text alone, so it takes any resume: PDFs, which can't be edited in place, and any
    other resume no other tailor handles. TailoringService tries it last."""

    def handles(self, resume_path: str) -> bool:
        return True

    def _ask_ai(self, resume_path: str, resume_text: str, job: dict, use_keywords: list[str],
                avoid_keywords: list[str]) -> dict | None:
        return self._ai.tailor_resume(resume_text, job)

    def _check(self, draft: dict, resume_path: str, resume_text: str, avoid_keywords: list[str]) -> dict:
        skill_evidence = draft.pop("skill_evidence")
        # Remove anything the original resume doesn't contain
        resume, removed_skills, notes = self._logic.ground_tailored_resume(draft, resume_text, avoid_keywords)
        resume = self._docx.tidy_resume_dates(resume)
        changes = resume.pop("changes")
        return {
            "resume": resume,
            "paragraphs": None,
            "docx_bytes": self._docx.write_resume_docx(resume),
            "changes": changes,
            "notes": notes,
            "removed_skills": removed_skills,
            "skill_evidence": skill_evidence,
            "parts": self._logic.resume_parts(resume),
        }
