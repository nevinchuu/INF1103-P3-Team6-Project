"""Tests for the resume tailors (src/resume_tailor.py, word_resume_tailor.py, pdf_resume_tailor.py).

The managers and the Word editor are stand-ins that record what they are asked, so no AI, network or Word file is
needed. The full tailoring of a real Word and PDF resume is tested through the apps in test_web_app.py.
"""

from types import SimpleNamespace

import pytest

from tailoring.pdf_resume_tailor import PdfResumeTailor
from tailoring.resume_tailor import ResumeTailor
from tailoring.word_resume_tailor import WordResumeTailor

RESULT_KEYS = {"resume", "paragraphs", "docx_bytes", "changes", "notes", "removed_skills", "skill_evidence", "parts"}
JOB = {"title": "Data Analyst", "required_skills": ["SQL"]}


def stand_ins(ai_answer: dict | None):
    """(ai, logic, io, docx) stand-ins: the AI gives ai_answer, and every check lets the answer through."""
    paragraphs = [{"index": 0, "text": "Built reports", "removed": False}]
    ai = SimpleNamespace(tailor_docx=lambda paragraphs, job, use, avoid: ai_answer,
                         tailor_resume=lambda text, job: None if ai_answer is None else dict(ai_answer))
    logic = SimpleNamespace(
        ground_docx_edits=lambda edits, sent, text, avoid: ({0: "Built SQL reports"}, {0: "uses SQL"}, [], []),
        keep_bullet_groups=lambda edits, sent: (edits, []),
        fit_edits_to_page_count=lambda edits, sent: (edits, []),
        ground_tailored_resume=lambda tailored, text, avoid: (tailored, ["Tableau"], []),
        resume_parts=lambda resume: ["Built reports"])
    io = SimpleNamespace(describe_docx_changes=lambda sent, edits, reasons: ["Reworded a point"])
    docx = SimpleNamespace(read_docx_paragraphs=lambda path: paragraphs,
                           find_layout_fixes=lambda paragraphs, edits: ({}, []),
                           word_preview=lambda paragraphs, edits, fixes: paragraphs,
                           write_edited_docx=lambda path, edits, fixes: b"edited",
                           tidy_resume_dates=lambda resume: resume,
                           write_resume_docx=lambda resume: b"new")
    return ai, logic, io, docx


WORD_ANSWER = {"paragraphs": [{"index": 0, "text": "Built reports"}], "edits": [{"index": 0, "text": "x"}],
               "skill_evidence": []}
PDF_ANSWER = {"name": "Alex", "changes": ["Aimed at data work"], "skill_evidence": [], "sections": []}


def test_the_abstract_tailor_cannot_be_used_on_its_own():
    with pytest.raises(TypeError):
        ResumeTailor(*stand_ins(None))


@pytest.mark.parametrize("path, word, pdf", [("cv.docx", True, True), ("CV.DOCX", True, True),
                                             ("cv.pdf", False, True), ("cv.txt", False, True)])
def test_each_tailor_says_which_resumes_it_handles(path, word, pdf):
    parts = stand_ins(None)
    assert WordResumeTailor(*parts).handles(path) is word
    assert PdfResumeTailor(*parts).handles(path) is pdf


@pytest.mark.parametrize("tailor_class, answer, path", [(WordResumeTailor, WORD_ANSWER, "cv.docx"),
                                                        (PdfResumeTailor, PDF_ANSWER, "cv.pdf")])
def test_both_tailors_give_the_service_the_same_shape_of_result(tailor_class, answer, path):
    """LSP: TailoringService uses whichever tailor it gets the same way, so both must answer in the same shape."""
    progress = []
    result = tailor_class(*stand_ins(answer)).tailor(path, "Built reports", JOB, ["SQL"], [],
                                                     lambda percent, message: progress.append(percent))
    assert set(result) == RESULT_KEYS
    assert progress == [92]


def test_the_word_tailor_edits_the_file_and_the_pdf_tailor_writes_a_new_one():
    word = WordResumeTailor(*stand_ins(WORD_ANSWER)).tailor("cv.docx", "", JOB, [], [], lambda p, m: None)
    pdf = PdfResumeTailor(*stand_ins(PDF_ANSWER)).tailor("cv.pdf", "", JOB, [], [], lambda p, m: None)
    assert (word["docx_bytes"], word["resume"], word["changes"]) == (b"edited", None, ["Reworded a point"])
    assert (pdf["docx_bytes"], pdf["paragraphs"], pdf["changes"]) == (b"new", None, ["Aimed at data work"])
    assert pdf["removed_skills"] == ["Tableau"]


@pytest.mark.parametrize("tailor_class, path", [(WordResumeTailor, "cv.docx"), (PdfResumeTailor, "cv.pdf")])
def test_when_the_ai_fails_there_is_nothing_to_check(tailor_class, path):
    """The template method stops after the AI step: no progress past it and no checks."""
    progress = []
    result = tailor_class(*stand_ins(None)).tailor(path, "", JOB, [], [],
                                                   lambda percent, message: progress.append(percent))
    assert result is None and progress == []


class RecordingTailor(ResumeTailor):
    """A tailor that records the order tailor() calls its steps in."""

    def __init__(self) -> None:
        super().__init__(*stand_ins(None))
        self.steps = []

    def handles(self, resume_path: str) -> bool:
        return True

    def _ask_ai(self, resume_path, resume_text, job, use_keywords, avoid_keywords):
        self.steps.append("ask")
        return {"draft": True}

    def _check(self, draft, resume_path, resume_text, avoid_keywords):
        self.steps.append("check")
        return draft


def test_the_template_method_asks_then_reports_then_checks():
    tailor = RecordingTailor()
    result = tailor.tailor("cv", "", JOB, [], [], lambda percent, message: tailor.steps.append(percent))
    assert tailor.steps == ["ask", 92, "check"] and result == {"draft": True}
