import os

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
