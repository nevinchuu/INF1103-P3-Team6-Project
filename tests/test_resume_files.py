"""Tests for ResumeFiles: listing, reading and saving resume files. A file that can't be read gives "" and never a crash."""

import os

import pytest
from docx import Document

from io_layer.resume_files import ResumeFiles

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_FOLDER = os.path.join(PROJECT_ROOT, "resumes")


@pytest.fixture
def folder(tmp_path):
    return str(tmp_path)


def write_docx(path: str, text: str) -> None:
    document = Document()
    document.add_paragraph(text)
    document.save(path)


def test_list_resumes_shows_only_pdf_and_docx_and_survives_a_missing_folder(folder):
    write_docx(os.path.join(folder, "b.docx"), "x" * 200)
    open(os.path.join(folder, "a.pdf"), "w").close()
    open(os.path.join(folder, "notes.txt"), "w").close()
    open(os.path.join(folder, "~$lock.docx"), "w").close()
    assert ResumeFiles(folder).list_resumes() == ["a.pdf", "b.docx"]
    assert ResumeFiles(os.path.join(folder, "nope")).list_resumes() == []


def test_a_word_resume_is_read_with_its_file_name(folder):
    write_docx(os.path.join(folder, "mine.docx"), "I built dashboards.")
    assert ResumeFiles(folder).load(os.path.join(folder, "mine.docx")) == ("mine.docx", "I built dashboards.")


def test_a_real_pdf_resume_is_read():
    files = ResumeFiles(SAMPLE_FOLDER)
    pdfs = [name for name in files.list_resumes() if name.endswith(".pdf")]
    if not pdfs:
        pytest.skip("no sample PDF resume in the resumes folder")
    name, text = files.load(files.path_of(pdfs[0]))
    assert name == pdfs[0] and text


def test_a_real_word_resume_is_read():
    name, text = ResumeFiles(SAMPLE_FOLDER).load(os.path.join(SAMPLE_FOLDER, "sample_resume_fintech.docx"))
    assert name == "sample_resume_fintech.docx" and len(text) > 100


def test_a_missing_file_and_a_broken_file_give_no_text_and_no_crash(folder):
    broken = os.path.join(folder, "broken.docx")
    with open(broken, "w", encoding="utf-8") as file:
        file.write("not really a Word file")
    broken_pdf = os.path.join(folder, "broken.pdf")
    with open(broken_pdf, "w", encoding="utf-8") as file:
        file.write("not really a PDF")
    files = ResumeFiles(folder)
    assert files.load(broken) == ("broken.docx", "")
    assert files.load(broken_pdf) == ("broken.pdf", "")
    assert files.load(os.path.join(folder, "missing.pdf")) == ("missing.pdf", "")


def test_a_chosen_name_becomes_a_path_in_the_folder(folder):
    assert ResumeFiles(folder).path_of("mine.docx") == os.path.join(folder, "mine.docx")


def test_an_upload_is_saved_and_never_replaces_a_saved_resume(folder):
    write_docx(os.path.join(folder, "cv.docx"), "x" * 200)
    files = ResumeFiles(folder)
    path = files.save_upload(b"new bytes", "cv.docx")
    assert os.path.basename(path) == "cv_2.docx"
    with open(path, "rb") as file:
        assert file.read() == b"new bytes"
    assert os.path.basename(files.save_upload(b"again", "cv.docx")) == "cv_3.docx"
    assert Document(os.path.join(folder, "cv.docx")).paragraphs[0].text == "x" * 200


def test_an_upload_into_a_folder_that_does_not_exist_yet_creates_it(folder):
    files = ResumeFiles(os.path.join(folder, "new"))
    assert os.path.exists(files.save_upload(b"x", "cv.pdf"))


def test_a_non_english_upload_name_keeps_its_extension(folder):
    assert os.path.basename(ResumeFiles(folder).save_upload(b"x", "简历.docx")) == "resume.docx"


def test_an_upload_that_cannot_be_written_raises_os_error(folder):
    blocker = os.path.join(folder, "blocked")
    open(blocker, "w").close()
    with pytest.raises(OSError):
        ResumeFiles(blocker).save_upload(b"x", "cv.docx")


def test_a_tailored_resume_goes_in_the_tailored_folder_and_never_replaces_one(folder):
    files = ResumeFiles(folder)
    first = files.save_tailored(b"one", "my cv_Data Analyst (KL).docx")
    second = files.save_tailored(b"two", "my cv_Data Analyst (KL).docx")
    assert os.path.dirname(first) == os.path.join(folder, "tailored")
    assert os.path.basename(first) == "my_cv_Data_Analyst_KL.docx"
    assert os.path.basename(second) == "my_cv_Data_Analyst_KL_2.docx"
    assert open(first, "rb").read() == b"one" and open(second, "rb").read() == b"two"
    assert os.path.basename(files.save_tailored(b"x", "!!!.docx")) == "tailored_resume.docx"
    assert files.list_resumes() == []  # the tailored folder is not part of the resume list


def test_a_tailored_resume_that_cannot_be_written_raises_os_error(folder):
    blocker = os.path.join(folder, "blocked")
    open(blocker, "w").close()
    with pytest.raises(OSError):
        ResumeFiles(blocker).save_tailored(b"x", "cv.docx")


def test_a_rejected_upload_can_be_removed_and_a_missing_one_is_only_logged(folder, caplog):
    files = ResumeFiles(folder)
    path = files.save_upload(b"x", "bad.docx")
    files.remove_upload(path)
    assert files.list_resumes() == []
    files.remove_upload(path)  # already gone: no crash
    assert "Could not delete the rejected upload" in caplog.text
