"""PdfResumeTailor: a resume rewritten from its text as a new Word file."""

from io_layer.resume_tailor import ResumeTailor


class PdfResumeTailor(ResumeTailor):
    """Tailors from the resume's text alone, so it takes any resume: PDFs, which can't be edited in place, and any
    other resume no other tailor handles. TailoringService tries it last."""

    def handles(self, resume_path: str) -> bool:
        return True

    def _ask_ai(self, resume_path: str, resume_text: str, job: dict, feedback: list[str] | None = None) -> dict | None:
        return self._ai.tailor_resume(resume_text, job, feedback)

    def _check(self, draft: dict, resume_text: str) -> dict:
        skill_evidence = draft.pop("skill_evidence")
        draft.pop("resume_path")  # only the Word tailor needs the file; the resume is rebuilt from its text
        avoid_keywords, use_keywords = draft.pop("avoid_keywords"), draft.pop("use_keywords")
        # Remove anything the original resume doesn't contain, and undo rewordings that recombine it
        resume, removed_skills, notes = self._logic.ground_tailored_resume(draft, resume_text, avoid_keywords,
                                                                           use_keywords)
        changes = resume.pop("changes")
        return {
            "resume": resume,
            "paragraphs": None,
            "docx_bytes": self._docx.write_new(resume),
            "changes": changes,
            "notes": notes,
            "removed_skills": removed_skills,
            "skill_evidence": skill_evidence,
            "parts": self._logic.resume_parts(resume),
        }
