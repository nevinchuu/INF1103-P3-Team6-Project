"""WordResumeTailor: a Word resume reworded in place, so its layout and page count stay the same."""

from io_layer.resume_tailor import ResumeTailor


class WordResumeTailor(ResumeTailor):
    """Tailors .docx resumes: the AI rewords the resume's own paragraphs, and the Word editor writes the edits back
    into a copy of the file."""

    def handles(self, resume_path: str) -> bool:
        return resume_path.lower().endswith(".docx")

    def _ask_ai(self, resume_path: str, resume_text: str, job: dict, feedback: list[str] | None = None) -> dict | None:
        paragraphs = self._docx.read_paragraphs(resume_path)
        use_keywords, avoid_keywords = self._logic.job_keywords(job, resume_text)
        tailored = self._ai.tailor_docx(paragraphs, job, (use_keywords, avoid_keywords), feedback)
        # The file's own paragraphs are kept with the answer: the layout fixes and the preview start from them
        return None if tailored is None else {**tailored, "original": paragraphs}

    def _check(self, draft: dict, resume_text: str) -> dict:
        # The AI's edits may not add anything the resume doesn't show, never leave a heading with no points,
        # and keep the number of pages
        sent, paragraphs = draft["paragraphs"], draft["original"]
        edits, reasons, removed_skills, notes = self._logic.ground_docx_edits(
            draft["edits"], sent, resume_text, draft["avoid_keywords"], draft["use_keywords"])
        # Small layout tidy-ups (e.g. a certification's year moved to the right), shown in the preview too
        fixes = self._docx.find_layout_fixes(paragraphs, edits)
        preview = self._docx.preview(paragraphs, edits, fixes)
        changes = self.__changes(sent, paragraphs, edits, reasons, fixes)
        return {
            "resume": None,
            "paragraphs": preview,
            "docx_bytes": self._docx.write_edited(draft["resume_path"], edits, fixes),
            "changes": changes,
            "notes": notes,
            "removed_skills": removed_skills,
            "skill_evidence": draft["skill_evidence"],
            "parts": [paragraph["text"] for paragraph in preview if not paragraph["removed"]],
        }

    @staticmethod
    def __changes(sent: list[dict], paragraphs: list[dict], edits: dict[int, str], reasons: dict[int, str],
                  fixes: dict[int, str]) -> list[dict]:
        """What changed, as records (IOManager words them): one per applied edit, then the layout tidy-ups."""
        original = {paragraph["id"]: paragraph["text"] for paragraph in sent}
        changes = []
        for paragraph_id, new_text in edits.items():
            start = original[paragraph_id][:50] + ("..." if len(original[paragraph_id]) > 50 else "")
            changes.append({"kind": "removed" if new_text == "" else "reworded", "start": start,
                            "reason": reasons.get(paragraph_id, "")})
        kinds = {paragraph["id"]: paragraph["kind"] for paragraph in paragraphs}
        if any(kinds[paragraph_id] == "entry" for paragraph_id in fixes):
            changes.append({"kind": "layout_durations"})
        dated = sum(1 for paragraph_id in fixes if kinds[paragraph_id] == "bullet")
        if dated:
            changes.append({"kind": "layout_dated", "count": dated})
        return changes
