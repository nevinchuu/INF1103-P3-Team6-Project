"""Tests for DocxEditor (src/io_layer/docx_editor.py): reading a Word resume, editing it in place, and writing a new one."""

import io
import os

from docx import Document

from io_layer.docx_editor import DocxEditor

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(PROJECT_ROOT, "resumes", "sample_resume_fintech.docx")
editor = DocxEditor()


def test_paragraphs_are_read_with_ids_and_text():
    paragraphs = editor.read_paragraphs(SAMPLE)
    assert paragraphs and all({"id", "text"} <= set(p) for p in paragraphs)
    assert len({p["id"] for p in paragraphs}) == len(paragraphs)


def test_an_edit_changes_only_its_paragraph(tmp_path):
    paragraphs = editor.read_paragraphs(SAMPLE)
    target = next(p for p in paragraphs if len(p["text"]) > 20)
    output = editor.write_edited(SAMPLE, {target["id"]: "A brand new line of text"})
    edited = Document(io.BytesIO(output))
    texts = [paragraph.text for paragraph in edited.paragraphs]
    assert "A brand new line of text" in texts and target["text"] not in texts
    assert len(texts) <= len(paragraphs)


def test_an_empty_edit_removes_the_paragraph():
    paragraphs = editor.read_paragraphs(SAMPLE)
    target = next(p for p in paragraphs if len(p["text"]) > 20)
    edited = Document(io.BytesIO(editor.write_edited(SAMPLE, {target["id"]: ""})))
    assert target["text"] not in [paragraph.text for paragraph in edited.paragraphs]


def test_the_preview_marks_removed_paragraphs_and_uses_the_new_text():
    paragraphs = [{"id": 1, "text": "old", "kind": "bullet"}, {"id": 2, "text": "gone", "kind": "bullet"}]
    preview = editor.preview(paragraphs, {1: "new", 2: ""}, {})
    assert preview[0]["text"] == "new" and preview[1]["removed"] is True


def test_a_resume_written_from_data_is_a_readable_word_file():
    resume = {"name": "Sample Person", "contact": "sample@example.com", "sections": [
        {"heading": "Skills", "text": "", "skill_groups": [{"label": "Technical", "skills": ["SQL", "Python"]}],
         "entries": []},
        {"heading": "Experience", "text": "", "skill_groups": [], "entries": [
            {"title": "Analyst", "organisation": "Acme", "dates": "2024", "subtitle": "", "bullets": ["Built reports"]}]}]}
    output = editor.write_new(resume)
    text = "\n".join(p.text for p in Document(io.BytesIO(output)).paragraphs)
    assert output[:2] == b"PK" and "Sample Person" in text and "Technical: SQL, Python" in text and "Built reports" in text


def test_layout_fixes_are_returned_as_a_dict_of_new_text_and_say_nothing_else():
    paragraphs = [
        {"id": 0, "text": "Sample Person", "kind": "name"},
        {"id": 1, "text": "Experience", "kind": "heading"},
        {"id": 2, "text": "Analyst\tSep 2024 - Feb 2025 (6 months)", "kind": "entry"},
        {"id": 3, "text": "Certifications", "kind": "heading"},
        {"id": 4, "text": "Google Certificate, 2023", "kind": "bullet"},
        {"id": 5, "text": "Built reports", "kind": "bullet"},
    ]
    fixes = editor.find_layout_fixes(paragraphs, {5: "Built SQL reports"})
    assert fixes == {2: "Analyst\tSep 2024 - Feb 2025", 4: "Google Certificate\t2023"}
    assert editor.find_layout_fixes(paragraphs, {2: "new", 4: "new"}) == {}  # an edited paragraph is left to its edit
