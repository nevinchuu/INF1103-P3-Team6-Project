"""WordResumeTailor: a Word resume reworded in place, so its layout and page count stay the same."""

from tailoring.resume_tailor import ResumeTailor


class WordResumeTailor(ResumeTailor):
    """Tailors .docx resumes: the AI rewords the resume's own paragraphs, and the Word editor writes the edits back
    into a copy of the file."""

    def handles(self, resume_path: str) -> bool:
        return resume_path.lower().endswith(".docx")

    def _ask_ai(self, resume_path: str, resume_text: str, job: dict, use_keywords: list[str],
                avoid_keywords: list[str]) -> dict | None:
        paragraphs = self._docx.read_docx_paragraphs(resume_path)
        tailored = self._ai.tailor_docx(paragraphs, job, use_keywords, avoid_keywords)
        # The file's own paragraphs are kept with the answer: the layout fixes and the preview start from them
        return None if tailored is None else {**tailored, "original": paragraphs}

    def _check(self, draft: dict, resume_path: str, resume_text: str, avoid_keywords: list[str]) -> dict:
        # The AI's edits may not add anything the resume doesn't show, never leave a heading with no points,
        # and keep the number of pages
        sent, paragraphs = draft["paragraphs"], draft["original"]
        edits, reasons, removed_skills, check_notes = self._logic.ground_docx_edits(
            draft["edits"], sent, resume_text, avoid_keywords)
        edits, group_notes = self._logic.keep_bullet_groups(edits, sent)
        edits, page_notes = self._logic.fit_edits_to_page_count(edits, sent)
        # Small layout tidy-ups (e.g. a certification's year moved to the right), shown in the preview too
        fixes, fix_notes = self._docx.find_layout_fixes(paragraphs, edits)
        preview = self._docx.word_preview(paragraphs, edits, fixes)
        return {
            "resume": None,
            "paragraphs": preview,
            "docx_bytes": self._docx.write_edited_docx(resume_path, edits, fixes),
            "changes": self._io.describe_docx_changes(sent, edits, reasons) + fix_notes,
            "notes": check_notes + group_notes + page_notes,
            "removed_skills": removed_skills,
            "skill_evidence": draft["skill_evidence"],
            "parts": [paragraph["text"] for paragraph in preview if not paragraph["removed"]],
        }
