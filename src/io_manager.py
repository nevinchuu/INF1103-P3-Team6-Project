"""Input/output layer: every boundary between the program and the outside world, and the only file that prints.

Console prompts and display, resume files, job listings from MyCareersFuture, web form checks,
the wording of the tailoring notes, and the registry of background tasks.
"""

import html
import io
import logging
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from copy import deepcopy

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.shared import Cm, Pt, RGBColor
from docx.text.paragraph import Paragraph
import requests
from pypdf import PdfReader

RESUME_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resumes")
ALLOWED_EXTENSIONS = [".pdf", ".docx"]
MIN_RESUME_CHARS = 100
MIN_WORDS_TO_EDIT = 12  # a plain Word paragraph this long is body text (e.g. a profile), not a name or contact line
TYPED_BULLET = re.compile(r"^\s*[•\-–*·]\s+")  # a bullet typed as a character, e.g. "• Built dashboards"
DURATION = re.compile(r"\s*\(\s*\d+(?:\.\d+)?\s*(?:months?|mths?|mos?|years?|yrs?)\s*\)", re.IGNORECASE)  # " (6 months)"
YEAR_AT_END = re.compile(r"^(.*?\S)\s*(?:,|\(|–|-)\s*((?:19|20)\d{2})\)?\s*$")  # "Google Certificate, 2023"
DATED_LIST_HEADING = re.compile(r"certif|licen|award|honou?r|achievement|course", re.IGNORECASE)
AVERAGE_CHAR_WIDTH = 0.45  # average character width as a share of the font size (Calibri, Arial and similar)
BULLET_INDENT_PT = 36  # usual indent of a bulleted list when the file doesn't say (0.5 inch)
MAX_SALARY = 50000
MAX_EXPERIENCE_YEARS = 60
LINE_WIDTH = 78

# MyCareersFuture, where the job listings come from (fetch_jobs, fetch_job_details)
JOBS_API_URL = "https://api.mycareersfuture.gov.sg/v2/search"
JOB_DETAILS_API_URL = "https://api.mycareersfuture.gov.sg/v2/jobs/{job_id}"
MAX_DESCRIPTION_CHARS = 2000   # job descriptions are cut to this length to keep AI prompts small
JOB_FETCH_ATTEMPTS = 3         # tries per job-portal search before giving up
# The portal's own search filters (checked against its search API). Job type and minimum salary
# filter well; for work arrangement it only has "Telecommuting", the closest to Remote or Hybrid.
# Maximum salary and years of experience can't be filtered there, so logic_manager does those.
PORTAL_EMPLOYMENT_TYPES = {  # job type preference -> the portal's employment types (either one counts)
    "Full Time": ["Full Time", "Permanent"],  # permanent jobs are full-time too
    "Part Time": ["Part Time"],
    "Contract": ["Contract"],
    "Internship/Attachment": ["Internship/Attachment"],
}
PORTAL_TELECOMMUTING = 2  # id of "Telecommuting" in the portal's flexibleWorkArrangements filter

ANY = "Any"
JOB_TYPES = [ANY, "Full Time", "Part Time", "Contract", "Internship/Attachment"]
WORK_ARRANGEMENTS = [ANY, "Onsite", "Hybrid", "Remote"]
EDUCATION_LEVELS = ["None", "Secondary", "ITE/Nitec", "A-Level", "Diploma", "Bachelor's", "Master's", "Doctorate"]

MENU_SEARCH = "Find jobs for my resume"
MENU_RESULTS = "Show my saved jobs"
MENU_DETAILS = "View one job in detail"
MENU_EXIT = "Exit"
MENU_OPTIONS = [MENU_SEARCH, MENU_RESULTS, MENU_DETAILS, MENU_EXIT]
MENU_REFRESH = "Refresh this list"
MENU_CANCEL = "Cancel"

# Short words kept in capitals when an all-caps title is tidied, e.g. "IT SUPPORT" -> "IT Support"
ACRONYMS = {"IT", "HR", "AI", "UX", "UI", "QA", "PR", "F&B", "SQL", "AWS", "SAP", "CRM", "ERP", "IOS", "PHP"}


# ==========================================
# 1. CONSOLE OUTPUT
# Everything the console shows: messages, headers, a profile, one job, a list of jobs.
# ==========================================

def display_message(text: str) -> None:
    """Print a normal message."""
    print(text)


def display_error(text: str) -> None:
    """Print an error message."""
    print(f"[!] {text}")


def display_header(title: str) -> None:
    """Print a section title."""
    print()
    print("=" * LINE_WIDTH)
    print(title.center(LINE_WIDTH))
    print("=" * LINE_WIDTH)


def display_profile(profile: dict) -> None:
    """Show what the AI found in the resume."""
    display_header("YOUR PROFILE")
    print(f"Name          : {profile.get('candidate_name', 'N/A')}")
    print(f"Qualification : {profile.get('qualification_detail', 'N/A')} "
          f"({profile.get('highest_qualification', 'N/A')})")
    print(f"Experience    : {profile.get('years_of_experience', 'N/A')} years full-time, "
          f"{profile.get('internship_months', 'N/A')} months internship")
    print(f"Level         : {profile.get('seniority_level', 'N/A')}")
    print(f"Skills        : {join_list(profile.get('core_skills'))}")
    print(f"Certifications: {join_list(profile.get('certifications'))}")


def display_record(job: dict) -> None:
    """Show every detail of one job."""
    display_header(shorten(job.get("title", "N/A"), LINE_WIDTH))
    print(f"Company         : {job.get('company', 'N/A')}")
    print(f"Location        : {job.get('location', 'N/A')}")
    print(f"Salary          : {format_salary(job)}")
    print(f"Job type        : {join_list(job.get('employment_types'))}")
    print(f"Work arrangement: {job.get('work_arrangement', 'N/A')}")
    print(f"Experience      : {job.get('min_years_experience', 'N/A')} years minimum")
    print(f"Education       : {job.get('min_education', 'N/A')}")
    print(f"Skills you have : {join_list(job.get('matched_skills'))}")
    print(f"Skills missing  : {join_list(job.get('missing_skills'))}")
    if job.get("match_outcome"):  # jobs saved before outcomes existed don't have one
        print(f"Match           : {job['match_outcome']}. {job.get('outcome_reason', '')}")
    print(f"Why             : {job.get('suitability_reason', 'N/A')}")
    print(f"Link            : {job.get('job_url', 'N/A')}")


def display_list(jobs: list[dict]) -> None:
    """Show a numbered table of jobs."""
    print(f"{'No.':<4} {'Title':<25} {'Company':<16} {'Match':<12} {'Salary'}")
    print("-" * LINE_WIDTH)
    for number, job in enumerate(jobs, start=1):
        print(f"{number:<4} {shorten(job.get('title', 'N/A'), 25):<25} "
              f"{shorten(job.get('company', 'N/A'), 16):<16} {job.get('match_outcome', '-'):<12} {format_salary(job)}")


def display_result(profile: dict, jobs: list[dict]) -> None:
    """Show the profile and the matching jobs."""
    display_profile(profile)
    display_header(f"MATCHING JOBS ({len(jobs)})")
    if not jobs:
        display_message("No jobs matched. Try a new search with wider filters.")
        return
    display_list(jobs)


# ==========================================
# 2. CONSOLE INPUT
# Every question the console asks; bad answers are rejected and asked again.
# ==========================================

def prompt_text(prompt: str, allow_blank: bool = False) -> str:
    """Ask for text until something is typed (or once, if allow_blank)."""
    while True:
        answer = input(f"{prompt}: ").strip()
        if answer or allow_blank:
            return answer
        display_error("This cannot be left blank.")


def prompt_number(prompt: str, minimum: int, maximum: int, default: int | None = None) -> int:
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


def prompt_choice(prompt: str, options: list[str]) -> str:
    """Show numbered options and return the one picked."""
    print()
    print(prompt)
    for number, option in enumerate(options, start=1):
        print(f"  {number}. {option}")
    choice = prompt_number("Choose", 1, len(options))
    return options[choice - 1]


def prompt_yes_no(prompt: str) -> bool:
    """Ask a yes/no question."""
    while True:
        answer = input(f"{prompt} (y/n): ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        display_error("Please type y or n.")


def prompt_resume() -> dict | None:
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
        text, problem = load_resume(path)
        if problem:
            display_error(problem)
            continue

        display_message(f"Resume loaded ({len(text)} characters).")
        return {"resume_path": path, "resume_text": text}


def prompt_filters() -> dict:
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


def collect_input(ai_name: str = "an AI service") -> dict | None:
    """Collect the resume and filters for one search. ai_name is shown in the privacy notice."""
    display_header("NEW SEARCH")
    display_message(f"Note: your resume's text is sent to {ai_name} to analyse it. Remove anything you'd "
                    "rather not share (e.g. your home address or NRIC) first.")
    record = prompt_resume()
    if record is None:
        return None
    record["filters"] = prompt_filters()
    return record


def prompt_missing_data(profile: dict) -> dict:
    """Ask for details the AI could not find in the resume."""
    if profile.get("highest_qualification", "None") == "None":
        display_message("\nThe AI could not find a completed qualification in your resume.")
        level = prompt_choice("Your highest completed qualification", EDUCATION_LEVELS)
        profile["highest_qualification"] = level
        if not profile.get("qualification_detail"):
            profile["qualification_detail"] = level

    if not profile.get("core_skills"):
        display_message("\nThe AI could not find any skills in your resume.")
        skills = []
        while not skills:
            answer = prompt_text("Your skills, separated by commas (e.g. Python, Excel)")
            for skill in answer.split(","):
                if skill.strip():
                    skills.append(skill.strip())
            if not skills:
                display_error("Please enter at least one skill.")
        profile["core_skills"] = skills

    return profile


# ==========================================
# 3. TEXT FOR THE CONSOLE AND WEB PAGES
# Short wording helpers, and the messages and notes the user reads.
# ==========================================

def shorten(text: str, width: int) -> str:
    """Cut text to fit a column."""
    text = str(text)
    if len(text) <= width:
        return text
    return text[:width - 3] + "..."


def join_list(value: list | None) -> str:
    """Join a list into comma-separated text."""
    if not isinstance(value, list) or not value:
        return "None"
    names = []
    for item in value:
        if isinstance(item, dict):
            item = item.get("job_skill", "")
        names.append(str(item))
    return ", ".join(names)


def format_salary(job: dict) -> str:
    """Return a job's salary range as text."""
    low = job.get("min_salary", 0)
    high = job.get("max_salary", 0)
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)) or (low == 0 and high == 0):
        return "Not stated"
    return f"${low:,.0f} - ${high:,.0f} {job.get('salary_period', 'monthly')}"


def tidy_title(title: str | None) -> str:
    """Capitalised titles, e.g. "SERVICE CREW" -> "Service Crew"; others stay as they are, and a missing one is N/A."""
    if not title:
        return "N/A"
    if not title.isupper():
        return title
    return " ".join(word if word in ACRONYMS else word.capitalize() for word in title.split())


def short_location(location: str) -> str:
    """First place in a MyCareersFuture district, e.g. "D19 Hougang, Sengkang, ..." -> "Hougang"."""
    place = re.sub(r"^D\d+\s+", "", location or "").split(",")[0].strip()
    return place or location


def experience_text(years: int) -> str:
    """A job's minimum experience as text, e.g. 0 -> "No experience needed", 2 -> "2+ years"."""
    if not years:
        return "No experience needed"
    return f"{years}+ year{'s' if years != 1 else ''}"


def same_skill(job_skill: str, candidate_skill: str) -> bool:
    """True if two skill names are the same apart from case and spaces, e.g. "SQL" and "sql",
    so a match under another name ("Stakeholder Management" by "Stakeholder Communication") can be shown."""
    return job_skill.strip().lower() == candidate_skill.strip().lower()


def search_step_message(step: str, filters: dict) -> str:
    """What to tell the user when the job search had to drop some preferences, or "" if it didn't."""
    if step == "job type":
        return f"No jobs matched all your preferences, so these match your job type ({filters['job_type']}) only."
    if step == "general":
        return "No jobs matched your preferences, so these are general results for your resume."
    return ""


def ai_unavailable_message() -> str:
    """What to tell the user when the AI could not read their resume (console and web)."""
    return ("The AI could not analyse your resume. Check your internet connection and API key, "
            "or wait a minute if the AI service is busy.")


def search_problem_message(listings_found: int, jobs_analysed: int) -> str:
    """Why a job search has nothing to show (console and web), or "" if it found and analysed some listings."""
    if listings_found == 0:
        return ("Could not get any job listings from MyCareersFuture. Check your internet "
                "connection, or try again in a few minutes.")
    if jobs_analysed == 0:
        return (f"The AI could not analyse any of the {listings_found} listings "
                "found. Check your API key, or wait a minute if the AI service is busy.")
    return ""


def search_summary(checked: int, jobs_per_search: int, keywords: list[str], removed: dict[str, int], shown: int) -> str:
    """Where a search's jobs came from and what the filters removed, e.g. "Checked 26 listings: ..."."""
    source = (f"Checked {checked} listings: up to {jobs_per_search} from each of {len(keywords)} "
              f"MyCareersFuture searches ({', '.join(keywords)}).")
    total_removed = sum(removed.values())
    if total_removed == 0:
        summary_text = "None removed by your filters."
    else:
        details = ", ".join(f"{count} {reason}" for reason, count in removed.items())
        summary_text = f"{total_removed} removed by your filters ({details})."
    return f"{source} {summary_text} Showing the best {shown}."


def describe_tailoring_notes(notes: list[dict]) -> list[str]:
    """The tailoring fact checks' notes (ai_manager and logic_manager), as sentences for the user."""
    sentences = []
    for note in notes:
        kind = note["kind"]
        if kind == "section_number_not_in_resume":
            sentences.append(f'The "{note["heading"]}" section was left out because it had a number '
                             f"that isn't in your resume.")
        elif kind == "entry_not_in_resume":
            sentences.append(f'Left out "{note["title"]}": it isn\'t in your resume.')
        elif kind == "section_adds_missing_skills":
            sentences.append(f'The "{note["heading"]}" section was left out because it added '
                             f"{', '.join(note['skills'])}, which isn't in your resume.")
        elif kind == "bullet_adds_missing_skills":
            sentences.append(f'Left out a point under "{note["title"]}" that added '
                             f"{', '.join(note['skills'])}, which isn't in your resume.")
        elif kind == "bullets_number_not_in_resume":
            sentences.append(f'Left out {note["count"]} point(s) under "{note["title"]}" with numbers that '
                             f"aren't in your resume.")
        elif kind == "edit_number_not_in_resume":
            sentences.append(f'Kept "{note["start"]}..." as it was: the new wording had a number that '
                             f"isn't in your resume.")
        elif kind == "edit_adds_missing_skills":
            sentences.append(f'Kept "{note["start"]}..." as it was: the new wording added '
                             f"{', '.join(note['skills'])}, which isn't in your resume.")
        elif kind == "bullet_group_kept":
            sentences.append(f'Kept every point under "{note["heading"]}": removing them all would leave it empty.')
        elif kind == "edit_too_long":
            sentences.append(f'Kept "{note["start"]}..." as it was: the new wording was longer and '
                             f"could push your resume onto another page.")
    return sentences


def describe_docx_changes(paragraphs: list[dict], edits: dict[int, str], reasons: dict[int, str]) -> list[str]:
    """What changed in a tailored Word resume: one line per edit that was applied, with the AI's
    reason, e.g. 'Reworded "Built dashboards for the sales team...": leads with SQL'."""
    original = {paragraph["id"]: paragraph["text"] for paragraph in paragraphs}
    changes = []
    for paragraph_id, new_text in edits.items():
        what = "Removed" if new_text == "" else "Reworded"
        start = original[paragraph_id][:50] + ("..." if len(original[paragraph_id]) > 50 else "")
        reason = reasons.get(paragraph_id, "")
        changes.append(f'{what} "{start}"' + (f": {reason}" if reason else ""))
    return changes


# ==========================================
# 4. RESUME FILES
# Finding, checking and reading a resume (PDF or Word).
# ==========================================

def list_resumes() -> list[str]:
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


def check_resume_file(path: str) -> str:
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


def read_pdf(path: str) -> str:
    """Return the text of a PDF file."""
    reader = PdfReader(path)
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n".join(pages)


def read_docx(path: str) -> str:
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


def read_resume(path: str) -> str:
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


def load_resume(path: str) -> tuple[str, str]:
    """Check a resume file and read it. Return (text, problem); problem is empty if it worked."""
    problem = check_resume_file(path)
    if problem:
        return "", problem
    text = read_resume(path)
    if len(text) < MIN_RESUME_CHARS:
        return "", ("Could not read enough text from that file. It may be a scanned image, "
                    "password-protected or corrupted. Please try another file.")
    return text, ""


# ==========================================
# 5. TAILORED WORD FILES
# Writing a tailored resume: a new Word file (from a PDF), or a Word file edited in place.
# ==========================================

def write_resume_docx(resume: dict) -> bytes:
    """Return a tailored resume as a Word file (bytes), in a plain one-column layout that hiring systems read well."""
    document = Document()
    for section in document.sections:
        section.top_margin = section.bottom_margin = Cm(1.8)
        section.left_margin = section.right_margin = Cm(2)
    style = document.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)
    style.paragraph_format.space_after = Pt(2)

    def heading(text: str) -> None:
        """Adds a bold, coloured section heading to the Word resume."""
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.space_before = Pt(10)
        paragraph.paragraph_format.space_after = Pt(4)
        run = paragraph.add_run(text)
        run.bold = True
        run.font.size = Pt(11)
        run.font.color.rgb = RGBColor(0x1F, 0x3A, 0x8A)

    if resume["name"]:
        paragraph = document.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = paragraph.add_run(resume["name"])
        run.bold = True
        run.font.size = Pt(18)
    if resume["contact"]:
        paragraph = document.add_paragraph(resume["contact"])
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER

    for section in resume["sections"]:
        heading(section["heading"])
        if section["text"]:
            document.add_paragraph(section["text"])
        for group in section["skill_groups"]:
            # "Technical: Python, SQL", with the label in bold
            paragraph = document.add_paragraph()
            if group["label"]:
                paragraph.add_run(f"{group['label']}: ").bold = True
            paragraph.add_run(", ".join(group["skills"]))
        for entry in section["entries"]:
            # "Title, Organisation" on the left, dates on the right (a right-aligned tab stop)
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.space_before = Pt(4)
            paragraph.paragraph_format.tab_stops.add_tab_stop(Cm(17), WD_TAB_ALIGNMENT.RIGHT)
            paragraph.add_run(entry["title"]).bold = True
            if entry["organisation"] and entry["organisation"] != entry["title"]:
                paragraph.add_run(f", {entry['organisation']}")
            if entry["dates"]:
                paragraph.add_run(f"\t{entry['dates']}").italic = True
            if entry["subtitle"]:
                document.add_paragraph().add_run(entry["subtitle"]).italic = True
            for bullet in entry["bullets"]:
                document.add_paragraph(bullet, style="List Bullet")

    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def tidy_resume_dates(resume: dict) -> dict:
    """Drop a length of time after the dates in a tailored resume (ai_manager.tailor_resume),
    e.g. "Sep 2024 - Feb 2025 (6 months)" -> "Sep 2024 - Feb 2025". Returns the same resume."""
    for section in resume["sections"]:
        for entry in section["entries"]:
            entry["dates"] = DURATION.sub("", entry["dates"])
    return resume


def read_docx_paragraphs(path: str) -> list[dict]:
    """Return a Word resume's paragraphs (id, text, kind, editable, chars_per_line, ...) for tailoring it in place."""
    document = Document(path)
    section = document.sections[0]
    page_text_width = (section.page_width - section.left_margin - section.right_margin) / 12700  # EMU -> points
    paragraphs = []
    for position, paragraph in enumerate(document.paragraphs):
        text = paragraph.text
        if not text.strip():
            continue
        runs = [run for run in paragraph.runs if run.text]
        formats = {(bool(run.bold), bool(run.italic), bool(run.underline)) for run in runs}
        all_bold = bool(runs) and all(run.bold for run in runs)
        is_bullet = "list" in paragraph.style.name.lower() or TYPED_BULLET.match(text) is not None
        # A link or field is not one of paragraph.runs, so its text would be lost when the runs are replaced
        only_runs = "".join(run.text for run in runs) == text

        if not paragraphs:
            kind = "name"
        elif "\t" in text:
            kind = "entry"
        elif is_bullet:
            kind = "bullet"
        elif all_bold or paragraph.style.name.lower().startswith("heading"):
            kind = "heading"
        else:
            kind = "text"
        editable = (only_runs and len(formats) == 1 and kind in ("bullet", "text")
                    and (kind == "bullet" or len(text.split()) >= MIN_WORDS_TO_EDIT))

        # Line width: the page's text width minus the paragraph's indents (a list's indent is usually
        # not in the paragraph itself, so a bullet without one gets BULLET_INDENT_PT)
        indent = paragraph.paragraph_format.left_indent or paragraph.style.paragraph_format.left_indent
        indent_pt = indent.pt if indent is not None else (BULLET_INDENT_PT if is_bullet else 0)
        right_indent = paragraph.paragraph_format.right_indent
        width_pt = page_text_width - indent_pt - (right_indent.pt if right_indent is not None else 0)
        chars_per_line = max(20, int(width_pt / (font_size_pt(paragraph) * AVERAGE_CHAR_WIDTH)))

        paragraphs.append({
            "id": position,
            "text": TYPED_BULLET.sub("", text).strip(),
            "kind": kind,
            "centered": paragraph.alignment == WD_ALIGN_PARAGRAPH.CENTER,
            "editable": editable,
            "chars_per_line": chars_per_line,
        })
    return paragraphs


def font_size_pt(paragraph: Paragraph, default: float = 11) -> float:
    """A Word paragraph's font size in points: its first run's, else its style's (or a style it is based on)."""
    for run in paragraph.runs:
        if run.font.size is not None:
            return run.font.size.pt
    style = paragraph.style
    while style is not None:
        if style.font.size is not None:
            return style.font.size.pt
        style = style.base_style
    return default


def find_layout_fixes(paragraphs: list[dict], edits: dict[int, str]) -> tuple[dict[int, str], list[str]]:
    """Small layout clean-ups for a Word resume, e.g. dropping "(6 months)" after dates. Returns (fixes, notes)."""
    fixes, durations, dated = {}, 0, 0
    has_entries = any(paragraph["kind"] == "entry" for paragraph in paragraphs)
    heading = ""
    for paragraph in paragraphs:
        if paragraph["id"] in edits:
            continue
        if paragraph["kind"] == "heading":
            heading = paragraph["text"]
        elif paragraph["kind"] == "entry" and DURATION.search(paragraph["text"]):
            fixes[paragraph["id"]] = DURATION.sub("", paragraph["text"])
            durations += 1
        elif paragraph["kind"] == "bullet" and has_entries and DATED_LIST_HEADING.search(heading):
            match = YEAR_AT_END.match(paragraph["text"])
            if match:
                fixes[paragraph["id"]] = f"{match.group(1)}\t{match.group(2)}"
                dated += 1
    notes = []
    if durations:
        notes.append("Tidied the layout: removed the length of time after the dates, e.g. \"(6 months)\".")
    if dated:
        notes.append(f"Tidied the layout: put the year of {dated} certification(s) or award(s) on the right, "
                     f"like your other entries.")
    return fixes, notes


def write_edited_docx(path: str, edits: dict[int, str], fixes: dict[int, str] | None = None) -> bytes:
    """Return a copy of a Word resume (bytes) with the edits and layout fixes applied; nothing else changes."""
    document = Document(path)
    paragraphs = document.paragraphs
    # An existing title-and-dates line, whose look a dated certification copies
    template = next((paragraph for paragraph in paragraphs if "\t" in paragraph.text), None)

    for position, new_text in (fixes or {}).items():
        paragraph = paragraphs[position]
        if "\t" in paragraph.text:
            # A title-and-dates line: drop the length of time, keeping each run's formatting
            for run in paragraph.runs:
                run.text = DURATION.sub("", run.text)
        elif template is not None:
            make_entry_line(paragraph, template, *new_text.split("\t", 1))

    for position, new_text in edits.items():
        paragraph = paragraphs[position]
        if new_text == "":
            paragraph._element.getparent().remove(paragraph._element)
            continue
        # Keep a typed bullet character, and the first run's formatting for the whole paragraph
        typed_bullet = TYPED_BULLET.match(paragraph.text)
        runs = [run for run in paragraph.runs if run.text]
        runs[0].text = (typed_bullet.group(0) if typed_bullet else "") + new_text
        for run in runs[1:]:
            run.text = ""

    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def word_preview(paragraphs: list[dict], edits: dict[int, str], fixes: dict[int, str]) -> list[dict]:
    """Each paragraph of a tailored Word resume for the web preview: new wording, and if changed or removed."""
    return [{**paragraph, "text": edits.get(paragraph["id"]) or fixes.get(paragraph["id"], paragraph["text"]),
             "kind": "entry" if paragraph["id"] in fixes else paragraph["kind"],
             "changed": paragraph["id"] in edits, "removed": edits.get(paragraph["id"]) == ""}
            for paragraph in paragraphs]

def make_entry_line(paragraph: Paragraph, template: Paragraph, name: str, year: str) -> None:
    """Turn a bullet like "Google Certificate, 2023" into a title-and-year line that looks like template."""
    space_before = paragraph.paragraph_format.space_before
    space_after = paragraph.paragraph_format.space_after
    element = paragraph._p
    if element.pPr is not None:
        element.remove(element.pPr)  # drops the bullet style and numbering
    if template._p.pPr is not None:
        element.insert(0, deepcopy(template._p.pPr))
    paragraph.paragraph_format.space_before = space_before
    paragraph.paragraph_format.space_after = space_after

    for run in paragraph.runs:
        element.remove(run._r)
    template_runs = [run for run in template.runs if run.text]
    for source, text in ((template_runs[0], name), (template_runs[-1], f"\t{year}")):
        run = paragraph.add_run(text)
        if source._r.rPr is not None:
            run._r.insert(0, deepcopy(source._r.rPr))


# ==========================================
# 6. WEB FORMS
# The web versions of the console's checks: the uploaded or chosen resume, filters, missing details.
# ==========================================

def unused_resume_path(filename: str) -> str:
    """Path in the resumes folder that won't replace a saved resume:
    resume.pdf, or resume_2.pdf, resume_3.pdf... if that name is taken."""
    stem, extension = os.path.splitext(filename)
    path = os.path.join(RESUME_FOLDER, filename)
    number = 2
    while os.path.exists(path):
        path = os.path.join(RESUME_FOLDER, f"{stem}_{number}{extension}")
        number += 1
    return path


def resume_path_from_form(uploaded_name: str, chosen_name: str) -> tuple[str, str]:
    """Where the web form's resume is: the upload's new path or the chosen saved one. Returns (path, problem)."""
    if uploaded_name:
        if os.path.splitext(uploaded_name)[1].lower() not in ALLOWED_EXTENSIONS:
            return "", "Please upload a PDF or Word (.docx) file."
        os.makedirs(RESUME_FOLDER, exist_ok=True)
        return unused_resume_path(uploaded_name), ""
    if chosen_name not in list_resumes():
        return "", "Please choose a resume or upload one."
    return os.path.join(RESUME_FOLDER, chosen_name), ""


def check_filters(values: Mapping[str, str]) -> tuple[dict, str]:
    """The web version of prompt_filters: check filters typed into a form (any dict-like of text).
    Return (filters, problem), with filters in the same shape as prompt_filters."""
    numbers = {}
    limits = {
        "min_salary": (0, MAX_SALARY),
        "max_salary": (MAX_SALARY, MAX_SALARY),
        "max_years_experience": (MAX_EXPERIENCE_YEARS, MAX_EXPERIENCE_YEARS),
    }
    for name, (default, maximum) in limits.items():
        text = values.get(name, "").strip().replace(",", "").replace("$", "")
        try:
            numbers[name] = int(text) if text else default
        except ValueError:
            return {}, "Salary and experience must be whole numbers."
        if not 0 <= numbers[name] <= maximum:
            return {}, f"Please keep {name.replace('_', ' ')} between 0 and {maximum}."
    if numbers["max_salary"] < numbers["min_salary"]:
        return {}, "Maximum salary cannot be lower than minimum salary."

    job_type = values.get("job_type", ANY)
    work_arrangement = values.get("work_arrangement", ANY)
    if job_type not in JOB_TYPES or work_arrangement not in WORK_ARRANGEMENTS:
        return {}, "Please pick a job type and work arrangement from the lists."
    return {**numbers, "job_type": job_type, "work_arrangement": work_arrangement}, ""


def fill_missing_data(profile: dict, qualification: str, skills_text: str) -> dict:
    """The web version of prompt_missing_data: fill in what the AI could not find in the resume
    from the search form's optional fields. skills_text is comma-separated, e.g. "Python, Excel"."""
    if profile["highest_qualification"] == "None" and qualification in EDUCATION_LEVELS:
        profile["highest_qualification"] = qualification
        profile["qualification_detail"] = profile["qualification_detail"] or qualification
    skills = [skill.strip() for skill in skills_text.split(",") if skill.strip()]
    if not profile["core_skills"] and skills:
        profile["core_skills"] = skills
    return profile


# ==========================================
# 7. JOB PORTAL (MyCareersFuture)
# Job listings coming into the system, for the AI layer to analyse.
# ==========================================

def clean_html(text: str) -> str:
    """Strips HTML tags and collapses whitespace."""
    # Job descriptions arrive as HTML, e.g. "<p>We are <strong>hiring</strong></p>" -> "We are hiring"
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def fetch_jobs(search_query: str, limit: int = 10, portal_filters: dict | None = None) -> list[dict] | None:
    """Fetches job listings from MyCareersFuture, with retries. Returns [] if none are listed, or None if
    every attempt failed."""
    raw_jobs = None
    for attempt in range(1, JOB_FETCH_ATTEMPTS + 1):
        try:
            # The portal reads the search words and filters from the JSON body; in the URL they are ignored
            response = requests.post(JOBS_API_URL, params={"limit": limit},
                                     json={"search": search_query, **(portal_filters or {})}, timeout=20)
            response.raise_for_status()  # turns HTTP errors (e.g. 500) into exceptions
            reply = response.json()
            raw_jobs = reply.get("results") if isinstance(reply, dict) else None
            if not isinstance(raw_jobs, list):
                raise ValueError(f"unexpected reply format ({type(reply).__name__})")
            break  # success - stop retrying
        except (requests.RequestException, ValueError) as error:
            if attempt == JOB_FETCH_ATTEMPTS:
                logging.error(f"Job search for '{search_query}' failed after {attempt} attempts: {error}")
                return None
            logging.warning(f"Job search for '{search_query}' failed ({type(error).__name__}); retrying...")
            time.sleep(2 * attempt)  # wait a little longer each time: 2s, then 4s

    jobs = []
    for job in raw_jobs:
        try:
            jobs.append(listing_from_portal(job))
        except (AttributeError, KeyError, TypeError) as error:
            logging.warning(f"Skipped a '{search_query}' listing in an unexpected format "
                            f"({type(error).__name__}: {error})")
    return jobs


def listing_from_portal(job: dict) -> dict:
    """One portal listing as a job record with simple names. Raises AttributeError, KeyError
    or TypeError if the listing isn't in the expected format."""
    # Keep only the fields we use, with simple names; "or {}" stops missing data from crashing it
    company = (job.get("hiringCompany") or job.get("postedCompany") or {}).get("name")
    salary = job.get("salary") or {}
    districts = (job.get("address") or {}).get("districts") or []
    return {
        "title": job.get("title", "N/A"),
        "company": company or "N/A",
        "location": districts[0]["location"] if districts else "Singapore",
        "min_salary": salary.get("minimum", 0),
        "max_salary": salary.get("maximum", 0),
        "salary_period": ((salary.get("type") or {}).get("salaryType") or "Monthly").lower(),
        "employment_types": [employment["employmentType"] for employment in job.get("employmentTypes", [])],
        "position_levels": [level["position"] for level in job.get("positionLevels", [])],
        "min_years_experience": job.get("minimumYearsExperience") or 0,
        "flexible_work_arrangements": [
            arrangement["flexibleWorkArrangement"] for arrangement in job.get("flexibleWorkArrangements") or []
        ],
        "listed_skills": [skill["skill"] for skill in job.get("skills", [])],
        "description": clean_html(job.get("description", ""))[:MAX_DESCRIPTION_CHARS],
        "job_url": (job.get("metadata") or {}).get("jobDetailsUrl", "N/A"),
    }


def fetch_job_details(job_url: str) -> dict | None:
    """Gets one job's full description from MyCareersFuture, using the id at the end of its link.
    Returns {"description", "listed_skills", "is_open"}, or None if it can't be fetched."""
    # e.g. ".../job/it/data-analyst-acme-5365191b6ddd018b70f7b8149c0a20ca" -> "5365191b...20ca"
    match = re.search(r"([0-9a-f]{32})/?(?:\?.*)?$", job_url or "")
    if not match:
        return None
    try:
        response = requests.get(JOB_DETAILS_API_URL.format(job_id=match.group(1)), timeout=20)
        response.raise_for_status()
        job = response.json()
        return {
            "description": clean_html(job.get("description", ""))[:MAX_DESCRIPTION_CHARS],
            "listed_skills": [skill["skill"] for skill in job.get("skills", [])],
            "is_open": ((job.get("status") or {}).get("jobStatus") or "Open") == "Open",
        }
    # A reply in an unexpected format (AttributeError, KeyError, TypeError) is treated like no reply
    except (requests.RequestException, ValueError, AttributeError, KeyError, TypeError) as error:
        logging.warning(f"Could not fetch job details for {job_url} ({type(error).__name__})")
        return None


def portal_filters_for(preferences: dict | None, step: str) -> dict:
    """MyCareersFuture's own search filters for one search step: every preference it can filter, the job type,
    or none."""
    preferences = preferences or {}
    job_type = {}
    if preferences.get("job_type") in PORTAL_EMPLOYMENT_TYPES:
        job_type = {"employmentTypes": PORTAL_EMPLOYMENT_TYPES[preferences["job_type"]]}
    if step == "job type":
        return job_type
    if step != "all":
        return {}
    everything = dict(job_type)
    if preferences.get("min_salary"):
        everything["salary"] = preferences["min_salary"]
    if preferences.get("work_arrangement") in ("Remote", "Hybrid"):
        everything["flexibleWorkArrangements"] = [PORTAL_TELECOMMUTING]
    return everything


def fetch_listings(keywords: list[str], steps: list[str], preferences: dict | None, limit_per_search: int = 10,
                   fits: Callable[[dict, str], bool] | None = None,
                   on_progress: Callable[[str, int, int], None] | None = None,
                   summary: dict | None = None) -> list[dict]:
    """Searches MyCareersFuture for each job title, trying the search steps in order. Returns the listings
    that fit, each link once."""
    if summary is None:
        summary = {}
    fetched = {}  # (keyword, portal filters) -> listings, so a step that searches the same way doesn't search again
    unique_jobs = {}
    for step in steps:
        portal_filters = portal_filters_for(preferences, step)
        # A dict keyed by URL removes duplicates: the same job found by two searches is kept once
        unique_jobs = {}
        portal_failed = False
        summary["search_step"] = step
        summary["searches_failed"] = 0
        for done, keyword in enumerate(keywords, start=1):
            key = (keyword, repr(sorted(portal_filters.items())))
            if key not in fetched:
                logging.info(f"Searching portal for '{keyword}' ({step} search filters: {portal_filters})...")
                fetched[key] = fetch_jobs(keyword, limit=limit_per_search, portal_filters=portal_filters)
            found = fetched[key]
            if not found:
                summary["searches_failed"] += 1  # failed, or nothing listed under that title
            portal_failed = portal_failed or found is None
            for job in found or []:
                if job["job_url"] != "N/A" and (fits is None or fits(job, step)):
                    unique_jobs.setdefault(job["job_url"], job)
            if on_progress:
                on_progress("search", done, len(keywords))
        if unique_jobs or portal_failed:
            break
        logging.info(f"No listings fit the '{step}' preferences; trying with fewer preferences.")
    summary["listings_found"] = len(unique_jobs)
    return list(unique_jobs.values())


# ==========================================
# 8. WEB BACKGROUND TASKS
# Progress of a search or tailoring run, shown as the web app's progress bar.
# ==========================================


# Progress of the web app's background tasks by id: {"status", "percent", "message", "result", "error"}.
# Kept in memory only; background threads update it, so every change goes through the lock.
tasks = {}
tasks_lock = threading.Lock()


def start_task(**extra: object) -> str:
    """Add a running task and return its id. extra: e.g. job_url for tailoring."""
    task_id = uuid.uuid4().hex
    with tasks_lock:
        tasks[task_id] = {"status": "running", "percent": 0, "message": "Starting...",
                          "result": None, "error": None, **extra}
    return task_id


def update_task(task_id: str, **changes: object) -> None:
    """Update a task's progress. Raises InterruptedError (built into Python) if the user
    cancelled it, so the background thread stops at its next update."""
    with tasks_lock:
        if tasks[task_id]["status"] == "cancelled":
            raise InterruptedError("cancelled by the user")
        tasks[task_id].update(changes)


def get_task(task_id: str) -> dict | None:
    """A copy of one task, or None if there is no task with that id."""
    with tasks_lock:
        task = tasks.get(task_id)
        return dict(task) if task is not None else None


def cancel_task(task_id: str) -> None:
    """Mark a running task as cancelled; its thread stops at its next update_task."""
    with tasks_lock:
        task = tasks.get(task_id)
        if task is not None and task["status"] == "running":
            task["status"] = "cancelled"


def find_recent_job(job_url: str) -> dict | None:
    """A job by its link from a finished search still in memory, with the resume that found it, or None."""
    with tasks_lock:
        results = [task["result"] for task in tasks.values()
                   if task["status"] == "done" and "all_jobs" in task["result"]]
    for result in results:
        for job in result["all_jobs"]:
            if job["job_url"] == job_url:
                return {**job, "resume": result["resume"]}
    return None
