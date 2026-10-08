import os

from docx import Document
from pypdf import PdfReader

RESUME_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resumes")
ALLOWED_EXTENSIONS = [".pdf", ".docx"]
MIN_RESUME_CHARS = 100
MAX_SALARY = 50000
MAX_EXPERIENCE_YEARS = 60
LINE_WIDTH = 78

ANY = "Any"
JOB_TYPES = [ANY, "Full Time", "Part Time", "Contract", "Internship/Attachment"]
WORK_ARRANGEMENTS = [ANY, "Onsite", "Hybrid", "Remote"]
EDUCATION_LEVELS = ["None", "Secondary", "ITE/Nitec", "A-Level", "Diploma", "Bachelor's", "Master's", "Doctorate"]

MENU_SEARCH = "Find jobs for my resume"
MENU_RESULTS = "Show my last results"
MENU_DETAILS = "View one job in detail"
MENU_EXIT = "Exit"
MENU_OPTIONS = [MENU_SEARCH, MENU_RESULTS, MENU_DETAILS, MENU_EXIT]
MENU_REFRESH = "Refresh this list"
MENU_CANCEL = "Cancel"


def display_message(text):
    """Print a normal message."""
    print(text)


def display_error(text):
    """Print an error message."""
    print(f"[!] {text}")


def display_header(title):
    """Print a section title."""
    print()
    print("=" * LINE_WIDTH)
    print(title.center(LINE_WIDTH))
    print("=" * LINE_WIDTH)


def prompt_text(prompt):
    """Ask for text until something is typed."""
    while True:
        answer = input(f"{prompt}: ").strip()
        if answer:
            return answer
        display_error("This cannot be left blank.")


def prompt_number(prompt, minimum, maximum, default=None):
    """Ask for a whole number within a range."""
    hint = f"{minimum}-{maximum}"
    if default is not None:
        hint += f", Enter for {default}"
    while True:
        answer = input(f"{prompt} ({hint}): ").strip()
        if answer == "" and default is not None:
            return default
        answer = answer.replace(",", "").replace("$", "")
        try:
            value = int(answer)
        except ValueError:
            display_error(f"Please enter a whole number between {minimum} and {maximum}.")
            continue
        if value < minimum or value > maximum:
            display_error(f"Please enter a number between {minimum} and {maximum}.")
            continue
        return value


def prompt_choice(prompt, options):
    """Show numbered options and return the one picked."""
    print()
    print(prompt)
    for number, option in enumerate(options, start=1):
        print(f"  {number}. {option}")
    choice = prompt_number("Choose", 1, len(options))
    return options[choice - 1]


def prompt_yes_no(prompt):
    """Ask a yes/no question."""
    while True:
        answer = input(f"{prompt} (y/n): ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        display_error("Please type y or n.")


def check_resume_file(path):
    """Return what is wrong with a file, or an empty string."""
    if not os.path.exists(path):
        return f"File not found: {path}"
    if not os.path.isfile(path):
        return "That is a folder, not a file."
    extension = os.path.splitext(path)[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        return f"'{extension}' files are not supported. Please use a PDF or Word (.docx) file."
    if os.path.getsize(path) == 0:
        return "That file is empty."
    return ""


def read_pdf(path):
    """Return the text of a PDF file."""
    reader = PdfReader(path)
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n".join(pages)


def read_docx(path):
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


def read_resume(path):
    """Return the resume text, or an empty string if unreadable."""
    try:
        if path.lower().endswith(".pdf"):
            text = read_pdf(path)
        else:
            text = read_docx(path)
    except Exception as error:
        display_error(f"Could not open the file ({type(error).__name__}).")
        return ""
    return text.strip()


def list_resumes():
    """Return the PDF and Word files in the resumes folder."""
    try:
        os.makedirs(RESUME_FOLDER, exist_ok=True)
        all_names = sorted(os.listdir(RESUME_FOLDER))
    except OSError:
        return []
    names = []
    for name in all_names:
        extension = os.path.splitext(name)[1].lower()
        if extension in ALLOWED_EXTENSIONS and not name.startswith("~$"):
            names.append(name)
    return names


def prompt_resume():
    """Let the user pick a resume and return its path and text."""
    while True:
        names = list_resumes()
        if not names:
            display_error("No PDF or Word (.docx) files found. Put your resume in this folder:")
            display_message(f"    {RESUME_FOLDER}")
            answer = input("Press Enter to check again, or q to cancel: ").strip().lower()
            if answer == "q":
                return None
            continue

        choice = prompt_choice("Choose your resume", names + [MENU_REFRESH, MENU_CANCEL])
        if choice == MENU_CANCEL:
            return None
        if choice == MENU_REFRESH:
            continue

        path = os.path.join(RESUME_FOLDER, choice)
        problem = check_resume_file(path)
        if problem:
            display_error(problem)
            continue

        text = read_resume(path)
        if len(text) < MIN_RESUME_CHARS:
            display_error("Could not read enough text from that file. It may be a scanned image, "
                          "password-protected or corrupted. Please try another file.")
            continue

        display_message(f"Resume loaded ({len(text)} characters).")
        return {"resume_path": path, "resume_text": text}


def prompt_filters():
    """Ask for job preferences and return them as a dict."""
    display_message("\nYour job preferences (press Enter to accept the default)")

    while True:
        min_salary = prompt_number("Minimum monthly salary", 0, MAX_SALARY, default=0)
        max_salary = prompt_number("Maximum monthly salary", 0, MAX_SALARY, default=MAX_SALARY)
        if max_salary >= min_salary:
            break
        display_error("Maximum salary cannot be lower than minimum salary. Please enter both again.")

    max_years = prompt_number("Hide jobs that need more than how many years of experience",
                              0, MAX_EXPERIENCE_YEARS, default=MAX_EXPERIENCE_YEARS)
    job_type = prompt_choice("Job type", JOB_TYPES)
    work_arrangement = prompt_choice("Work arrangement", WORK_ARRANGEMENTS)

    return {
        "min_salary": min_salary,
        "max_salary": max_salary,
        "max_years_experience": max_years,
        "job_type": job_type,
        "work_arrangement": work_arrangement,
    }


def collect_input():
    """Collect the resume and filters for one search."""
    display_header("NEW SEARCH")
    record = prompt_resume()
    if record is None:
        return None
    record["filters"] = prompt_filters()
    return record
