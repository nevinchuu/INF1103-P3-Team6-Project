"""Tests for the resume tailors (src/io_layer/resume_tailor.py, word_resume_tailor.py, pdf_resume_tailor.py).

The managers and the Word editor are stand-ins that record what they are asked, so no AI, network or Word file is
needed. The full tailoring of a real Word and PDF resume is tested through the apps in test_web_app.py.
"""

import inspect
from types import SimpleNamespace

import pytest

from io_layer.pdf_resume_tailor import PdfResumeTailor
from io_layer import tailoring_service
from io_layer.resume_tailor import ResumeTailor
from io_layer.tailoring_service import TailoringService
from io_layer.word_resume_tailor import WordResumeTailor

RESULT_KEYS = {"resume", "paragraphs", "docx_bytes", "changes", "notes", "removed_skills", "skill_evidence", "parts",
               "avoid_keywords"}
JOB = {"title": "Data Analyst", "required_skills": ["SQL"]}


def stand_ins(ai_answer: dict | None):
    """(ai, logic, docx) stand-ins: the AI gives ai_answer, and every check lets the answer through."""
    paragraphs = [{"id": 0, "text": "Built reports", "kind": "bullet", "removed": False}]
    ai = SimpleNamespace(tailor_docx=lambda paragraphs, job, keywords, feedback=None: ai_answer,
                         tailor_resume=lambda text, job, feedback=None: None if ai_answer is None else dict(ai_answer))
    logic = SimpleNamespace(
        job_keywords=lambda job, text: (["SQL"], ["Tableau"]),
        ground_docx_edits=lambda edits, sent, text, avoid, use: ({0: "Built SQL reports"}, {0: "uses SQL"}, [], []),
        ground_tailored_resume=lambda tailored, text, avoid, use: (tailored, ["Tableau"], []),
        resume_parts=lambda resume: ["Built reports"])
    docx = SimpleNamespace(read_paragraphs=lambda path: paragraphs,
                           find_layout_fixes=lambda paragraphs, edits: {},
                           preview=lambda paragraphs, edits, fixes: paragraphs,
                           write_edited=lambda path, edits, fixes: b"edited",
                           write_new=lambda resume: b"new")
    return ai, logic, docx


WORD_ANSWER = {"paragraphs": [{"id": 0, "text": "Built reports"}], "edits": [{"index": 0, "text": "x"}],
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
    result = tailor_class(*stand_ins(answer)).tailor(path, "Built reports", JOB,
                                                     lambda percent, message: progress.append(percent))
    assert set(result) == RESULT_KEYS
    assert progress == [92]


def test_the_word_tailor_edits_the_file_and_the_pdf_tailor_writes_a_new_one():
    word = WordResumeTailor(*stand_ins(WORD_ANSWER)).tailor("cv.docx", "", JOB, lambda p, m: None)
    pdf = PdfResumeTailor(*stand_ins(PDF_ANSWER)).tailor("cv.pdf", "", JOB, lambda p, m: None)
    assert (word["docx_bytes"], word["resume"], word["changes"]) == (
        b"edited", None, [{"kind": "reworded", "start": "Built reports", "reason": "uses SQL"}])
    assert (pdf["docx_bytes"], pdf["paragraphs"], pdf["changes"]) == (b"new", None, ["Aimed at data work"])
    assert pdf["removed_skills"] == ["Tableau"]


@pytest.mark.parametrize("tailor_class, path", [(WordResumeTailor, "cv.docx"), (PdfResumeTailor, "cv.pdf")])
def test_when_the_ai_fails_there_is_nothing_to_check(tailor_class, path):
    """The template method stops after the AI step: no progress past it and no checks."""
    progress = []
    result = tailor_class(*stand_ins(None)).tailor(path, "", JOB, lambda percent, message: progress.append(percent))
    assert result is None and progress == []


class RecordingTailor(ResumeTailor):
    """A tailor that records the order tailor() calls its steps in, and what its steps are given."""

    def __init__(self) -> None:
        super().__init__(*stand_ins(None))
        self.steps = []
        self.drafts = []

    def handles(self, resume_path: str) -> bool:
        return True

    def _ask_ai(self, resume_path, resume_text, job, feedback=None):
        self.steps.append("ask")
        return {"draft": True}

    def _check(self, draft, resume_text):
        self.steps.append("check")
        self.drafts.append(draft)
        return {"checked": True}


def test_the_template_method_asks_then_reports_then_checks():
    tailor = RecordingTailor()
    result = tailor.tailor("cv.docx", "my text", JOB, lambda percent, message: tailor.steps.append(percent))
    assert tailor.steps == ["ask", 92, "check"]
    # the path and both keyword lists travel in the draft, and the avoid list is added to the result
    assert tailor.drafts == [{"draft": True, "resume_path": "cv.docx", "avoid_keywords": ["Tableau"], "use_keywords": ["SQL"]}]
    assert result == {"checked": True, "avoid_keywords": ["Tableau"]}


class RetryingTailor(ResumeTailor):
    """A tailor whose checks are scripted: each _check returns the next result, so a test decides what the first
    and second tries look like."""

    def __init__(self, results: list[dict]) -> None:
        super().__init__(*stand_ins(None))
        self.results = results
        self.feedback = []  # what each _ask_ai was told

    def handles(self, resume_path: str) -> bool:
        return True

    def _ask_ai(self, resume_path, resume_text, job, feedback=None):
        self.feedback.append(feedback)
        return {"try": len(self.feedback)}

    def _check(self, draft, resume_text):
        return self.results[draft["try"] - 1]


TABLEAU_NOTE = {"kind": "edit_recombines", "start": "Built Power BI dashboards", "tools": ["Tableau"], "words": []}
SAFE_NOTE = {"kind": "bullet_group_kept", "heading": "Cafe"}  # not a rejected change


def tailor_with(results):
    tailor, progress = RetryingTailor(results), []
    result = tailor.tailor("cv.docx", "text", JOB, lambda percent, message: progress.append(percent))
    return tailor, result, progress


def test_nothing_thrown_out_means_no_second_try():
    tailor, result, progress = tailor_with([{"notes": [SAFE_NOTE], "changes": ["a"]}])
    assert tailor.feedback == [None] and progress == [92] and result["changes"] == ["a"]


def test_a_thrown_out_change_gets_one_second_try_told_why_and_the_better_result_is_kept():
    first = {"notes": [TABLEAU_NOTE], "changes": ["a"]}
    second = {"notes": [SAFE_NOTE], "changes": ["a", "b"]}
    tailor, result, progress = tailor_with([first, second])
    assert tailor.feedback == [None, ['"Built Power BI dashboards...": Tableau (not named in this paragraph)']]
    assert progress == [92, 95] and result["changes"] == ["a", "b"]


def test_a_worse_second_try_is_not_used():
    first = {"notes": [TABLEAU_NOTE], "changes": ["a", "b"]}
    worse = {"notes": [TABLEAU_NOTE, {**TABLEAU_NOTE, "start": "Wrote SQL", "tools": [], "words": ["complex"]}],
             "changes": ["a"]}
    _, result, _ = tailor_with([first, worse])
    assert result["changes"] == ["a", "b"] and len(result["notes"]) == 1


# Seen in a real run: the second try had fewer changes thrown out but none that passed. What passed is what the user
# gets, so the first try's one good change wins
def test_a_second_try_with_fewer_problems_but_fewer_good_changes_is_not_used():
    first = {"notes": [TABLEAU_NOTE, {**TABLEAU_NOTE, "start": "Wrote SQL", "tools": [], "words": ["complex"]}],
             "changes": ["a"]}
    fewer_problems = {"notes": [TABLEAU_NOTE], "changes": []}
    _, result, _ = tailor_with([first, fewer_problems])
    assert result["changes"] == ["a"]


@pytest.mark.parametrize("tailor", [WordResumeTailor(*stand_ins(WORD_ANSWER)), PdfResumeTailor(*stand_ins(PDF_ANSWER)),
                                    RecordingTailor()])
def test_every_tailor_works_where_a_resume_tailor_is_expected(tailor):
    """LSP: whoever holds a ResumeTailor calls tailor() the same way and gets a dict with the same keys."""
    assert isinstance(tailor, ResumeTailor)
    path = "cv.docx" if tailor.handles("cv.docx") else "cv.pdf"
    result = tailor.tailor(path, "Built reports", JOB, lambda percent, message: None)
    assert isinstance(result, dict) and "avoid_keywords" in result
    if not isinstance(tailor, RecordingTailor):
        assert set(result) == RESULT_KEYS


def service_with(ai_answer_word: dict | None, ai_answer_pdf: dict | None):
    """A real TailoringService over stand-in managers: the data manager gives no job description."""
    ai, logic, docx = stand_ins(ai_answer_word)
    ai.tailor_resume = lambda text, job, feedback=None: None if ai_answer_pdf is None else dict(ai_answer_pdf)
    logic.resume_view = lambda job, name: (job, True)
    logic.tailoring_skills = lambda job, same, avoid, tailored: {"matched": [], "not_added": [], "shown": []}
    data = SimpleNamespace(portal_job_details=lambda url: None)
    return TailoringService(ai, logic, data, docx)


def test_the_service_picks_a_tailor_without_type_checks():
    """A Word file goes to the Word tailor and anything else to the PDF tailor, only through handles()."""
    service = service_with(WORD_ANSWER, PDF_ANSWER)
    job = {**JOB, "job_url": "https://x/1"}
    word = service.tailor(job, "cv.docx", "Built reports")
    pdf = service.tailor(job, "cv.pdf", "Built reports")
    other = service.tailor(job, "cv.txt", "Built reports")
    assert word["resume"] is None and word["paragraphs"] is not None
    assert pdf["resume"] is not None and pdf["paragraphs"] is None and other["resume"] is not None
    source = inspect.getsource(tailoring_service)
    assert "isinstance" not in source and ".endswith" not in source and "type(" not in source


def test_the_service_gives_back_the_records_unworded():
    result = service_with(WORD_ANSWER, PDF_ANSWER).tailor({**JOB, "job_url": "https://x/1"}, "cv.docx", "Built reports")
    assert result["changes"] == [{"kind": "reworded", "start": "Built reports", "reason": "uses SQL"}]
    assert result["notes"] == [] and result["had_description"] is False
    assert service_with(None, None).tailor({**JOB, "job_url": "https://x/1"}, "cv.pdf", "Built reports") is None


def test_the_word_tailor_reports_each_kind_of_layout_fix_as_a_record():
    ai, logic, docx = stand_ins(WORD_ANSWER)
    docx.read_paragraphs = lambda path: [{"id": 0, "text": "Built reports", "kind": "bullet", "removed": False},
                                         {"id": 1, "text": "Analyst\tSep 2024 (6 months)", "kind": "entry", "removed": False},
                                         {"id": 2, "text": "Google Certificate, 2023", "kind": "bullet", "removed": False},
                                         {"id": 3, "text": "Another Award, 2022", "kind": "bullet", "removed": False}]
    docx.find_layout_fixes = lambda paragraphs, edits: {1: "Analyst\tSep 2024", 2: "Google Certificate\t2023",
                                                        3: "Another Award\t2022"}
    ai.tailor_docx = lambda paragraphs, job, keywords, feedback=None: {**WORD_ANSWER, "paragraphs": paragraphs}
    result = WordResumeTailor(ai, logic, docx).tailor("cv.docx", "Built reports", JOB, lambda p, m: None)
    assert result["changes"][1:] == [{"kind": "layout_durations"}, {"kind": "layout_dated", "count": 2}]
