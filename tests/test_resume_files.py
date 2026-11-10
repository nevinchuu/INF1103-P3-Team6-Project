"""Tests for IOManager's resume-file methods: every problem with a resume file is a message, never a crash."""

import os

import pytest
from docx import Document

from io_layer.io_manager import IOManager, MIN_RESUME_CHARS

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_FOLDER = os.path.join(PROJECT_ROOT, "resumes")


@pytest.fixture
def folder(tmp_path):
    return str(tmp_path)


def write_docx(path: str, text: str) -> None:
    document = Document()
    document.add_paragraph(text)
    document.save(path)


def test_a_missing_file_a_folder_and_a_wrong_type_are_each_a_message(folder):
    files = IOManager(folder)
    assert "File not found" in files.check_resume_file(os.path.join(folder, "missing.pdf"))
    assert "folder" in files.check_resume_file(folder)
    text_file = os.path.join(folder, "resume.txt")
    with open(text_file, "w", encoding="utf-8") as file:
        file.write("hello")
    assert "not supported" in files.check_resume_file(text_file)


def test_an_empty_file_is_reported(folder):
    empty_pdf = os.path.join(folder, "empty.pdf")
    open(empty_pdf, "w").close()
    assert IOManager(folder).check_resume_file(empty_pdf) == "That file is empty."


def test_a_resume_with_too_little_text_is_rejected(folder):
    short = os.path.join(folder, "short.docx")
    write_docx(short, "Too short to be a resume.")
    text, problem = IOManager(folder).load_resume(short)
    assert text == "" and "Could not read enough text" in problem


def test_a_file_that_is_not_really_a_word_file_gives_no_text_and_no_crash(folder):
    broken = os.path.join(folder, "broken.docx")
    with open(broken, "w", encoding="utf-8") as file:
        file.write("not really a Word file")
    assert IOManager(folder).read_resume(broken) == ""
    assert IOManager(folder).load_resume(broken)[0] == ""


def test_a_real_resume_is_read():
    text, problem = IOManager(SAMPLE_FOLDER).load_resume(os.path.join(SAMPLE_FOLDER, "sample_resume_fintech.docx"))
    assert problem == "" and len(text) >= MIN_RESUME_CHARS


def test_list_resumes_shows_only_pdf_and_docx_and_survives_a_missing_folder(folder):
    write_docx(os.path.join(folder, "b.docx"), "x" * 200)
    open(os.path.join(folder, "a.pdf"), "w").close()
    open(os.path.join(folder, "notes.txt"), "w").close()
    assert IOManager(folder).list_resumes() == ["a.pdf", "b.docx"]
    assert IOManager(os.path.join(folder, "nope")).list_resumes() == []


def test_the_web_form_only_uses_a_listed_resume_or_a_new_upload(folder):
    write_docx(os.path.join(folder, "mine.docx"), "x" * 200)
    files = IOManager(folder)
    assert files.resume_path_from_form("", "../.env")[1] == "Please choose a resume or upload one."
    assert files.resume_path_from_form("notes.txt", "")[1] == "Please upload a PDF or Word (.docx) file."
    path, problem = files.resume_path_from_form("", "mine.docx")
    assert problem == "" and path == os.path.join(folder, "mine.docx")


def test_an_upload_never_replaces_a_saved_resume(folder):
    write_docx(os.path.join(folder, "cv.docx"), "x" * 200)
    path, problem = IOManager(folder).resume_path_from_form("cv.docx", "")
    assert problem == "" and os.path.basename(path) == "cv_2.docx"
