"""ResumeFiles: the resume folder. Lists the resumes, reads them, and saves uploaded and tailored files."""

import logging
import os
import re

from docx import Document
from pypdf import PdfReader
from werkzeug.utils import secure_filename

# The resumes: where they are and which files count
DEFAULT_RESUME_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                     "resumes")  # <project root>/resumes/ (this file is in src/io_layer/)
ALLOWED_EXTENSIONS = [".pdf", ".docx"]
MAX_UPLOAD_STEM = 80  # longest uploaded file name kept (before the extension), well inside what disks allow


class ResumeFiles:
    """The files in one resume folder. It reads and writes files only; it words no messages."""

    def __init__(self, folder: str) -> None:
        self.__folder = folder  # where the resumes are (a temporary folder in tests)

    def list_resumes(self) -> list[str]:
        """Return the PDF and Word files in the resumes folder."""
        try:
            os.makedirs(self.__folder, exist_ok=True)
            all_names = sorted(os.listdir(self.__folder))
        except OSError:
            return []
        names = []
        for name in all_names:
            extension = os.path.splitext(name)[1].lower()
            if extension in ALLOWED_EXTENSIONS and not name.startswith("~$"):
                names.append(name)
        return names

    def path_of(self, name: str) -> str:
        """The path of a file in the resume folder, e.g. the resume the user chose."""
        return os.path.join(self.__folder, name)

    def load(self, path: str) -> tuple[str, str]:
        """Return (file name, text). The text is empty if the file is missing or unreadable."""
        return os.path.basename(path), self.__read(path)

    def find_copy(self, data: bytes, name: str) -> str | None:
        """The path of a saved resume that an upload called name would be saved as (resume.pdf, resume_2.pdf...) and
        that has exactly these bytes, or None. So uploading the same file again reuses it instead of adding another
        numbered copy. A file that can't be read is skipped."""
        stem, extension = os.path.splitext(self.__safe_name(name))
        same_family = re.compile(rf"{re.escape(stem)}(_\d+)?{re.escape(extension)}")
        for saved in self.list_resumes():
            if not same_family.fullmatch(saved):
                continue
            path = self.path_of(saved)
            try:
                if os.path.getsize(path) == len(data):
                    with open(path, "rb") as file:
                        if file.read() == data:
                            return path
            except OSError:
                continue
        return None

    def save_upload(self, data: bytes, name: str) -> str:
        """Save an uploaded resume under a safe name that does not replace a saved one, and return its path.
        Raises OSError if it can't be written."""
        os.makedirs(self.__folder, exist_ok=True)
        path = self.__unused_path(self.__folder, self.__safe_name(name))
        with open(path, "wb") as file:
            file.write(data)
        return path

    def save_tailored(self, docx_bytes: bytes, name: str) -> str:
        """Writes a tailored resume next to the resumes, in a "tailored" folder, without replacing an earlier one,
        e.g. resumes/tailored/my_cv_Data_Analyst.docx. Returns its path; raises OSError if it can't be written."""
        folder = os.path.join(self.__folder, "tailored")
        os.makedirs(folder, exist_ok=True)
        stem = re.sub(r"[^A-Za-z0-9-]+", "_", os.path.splitext(name)[0]).strip("_")[:80]
        path = self.__unused_path(folder, f"{stem or 'tailored_resume'}.docx")
        with open(path, "wb") as file:
            file.write(docx_bytes)
        return path

    def remove_upload(self, path: str) -> None:
        """Delete an upload that can't be used, or it would be offered in the resume list from then on."""
        try:
            os.remove(path)
        except OSError as error:
            logging.warning(f"Could not delete the rejected upload {path} ({type(error).__name__})")

    @staticmethod
    def __safe_name(filename: str) -> str:
        """A safe file name that keeps its extension: secure_filename drops letters outside English,
        so "简历.docx" would lose its ".docx"; the name is also shortened so the disk accepts it."""
        stem, extension = os.path.splitext(filename)
        extension = secure_filename(extension).lower()
        stem = (secure_filename(stem) or "resume")[:MAX_UPLOAD_STEM]
        return f"{stem}.{extension}" if extension else stem

    @staticmethod
    def __unused_path(folder: str, filename: str) -> str:
        """Path in a folder that won't replace a saved file:
        resume.pdf, or resume_2.pdf, resume_3.pdf... if that name is taken."""
        stem, extension = os.path.splitext(filename)
        path = os.path.join(folder, filename)
        number = 2
        while os.path.exists(path):
            path = os.path.join(folder, f"{stem}_{number}{extension}")
            number += 1
        return path

    def __read(self, path: str) -> str:
        """Return the resume text, or an empty string if unreadable."""
        try:
            if path.lower().endswith(".pdf"):
                text = self.__read_pdf(path)
            else:
                text = self.__read_docx(path)
        except Exception as error:
            logging.warning(f"Could not open the file ({type(error).__name__}).")
            return ""
        return text.strip()

    @staticmethod
    def __read_pdf(path: str) -> str:
        """Return the text of a PDF file."""
        reader = PdfReader(path)
        pages = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
        return "\n".join(pages)

    @staticmethod
    def __read_docx(path: str) -> str:
        """Return the text of a Word file."""
        document = Document(path)
        lines = []
        for paragraph in document.paragraphs:
            lines.append(paragraph.text)
        for table in document.tables:
            for row in table.rows:
                for cell in row.cells:
                    lines.append(cell.text)
        return "\n".join(lines)
