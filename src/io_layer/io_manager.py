"""IO Manager: every boundary between the program and the person using it, and the only place that prints.

IOManager asks the questions (and rejects bad answers), shows jobs and results, words every message, reads the
resume files and checks what the web forms send. Fetching listings (JobPortal) and writing Word files
(DocxEditor) are separate classes, so this one stays about talking to the user.
"""

import logging
import os
import re
from collections.abc import Callable, Mapping

from docx import Document
from pypdf import PdfReader

# Short words kept in capitals when an all-caps title is tidied, e.g. "IT SUPPORT" -> "IT Support"
ACRONYMS = {"IT", "HR", "AI", "UX", "UI", "QA", "PR", "F&B", "SQL", "AWS", "SAP", "CRM", "ERP", "IOS", "PHP"}

# The resumes: where they are, which files count, and the least text a readable resume has
DEFAULT_RESUME_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "resumes")
ALLOWED_EXTENSIONS = [".pdf", ".docx"]
MIN_RESUME_CHARS = 100

# The choices and limits the user's filters can take ("Any" = no preference)
ANY = "Any"
MAX_SALARY = 50000
MAX_EXPERIENCE_YEARS = 60
JOB_TYPES = [ANY, "Full Time", "Part Time", "Contract", "Internship/Attachment"]
WORK_ARRANGEMENTS = [ANY, "Onsite", "Hybrid", "Remote"]
EDUCATION_LEVELS = ["None", "Secondary", "ITE/Nitec", "A-Level", "Diploma", "Bachelor's", "Master's", "Doctorate"]

LINE_WIDTH = 78  # widest line the console prints

# The log file both apps write to: the terminal stays readable, while INFO and up (every search's
# listings_found vs listings_analysed, and every handled failure) is kept for looking at afterwards
LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "logs", "job_matcher.log")
LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(module)s | %(message)s"

# The console menus
MENU_SEARCH = "Find jobs for my resume"
MENU_RESULTS = "Show my saved jobs"
MENU_DETAILS = "View one job in detail (and tailor your resume)"
MENU_WHATIF = "Plan skills to learn (what-if)"
MENU_EXIT = "Exit"
MENU_OPTIONS = [MENU_SEARCH, MENU_RESULTS, MENU_DETAILS, MENU_WHATIF, MENU_EXIT]
WHATIF_TEST = "Test skills I pick"
WHATIF_BACK = "Back to the main menu"
MENU_REFRESH = "Refresh this list"
MENU_CANCEL = "Cancel"


class IOManager:
    """Console prompts and display, and the checks on what the web forms send."""

    @staticmethod
    def set_up_logging(terminal_level: int = logging.WARNING) -> None:
        """Keeps the terminal to warnings and errors, and writes everything (INFO and up) to the log file.
        Both apps call this, so a web search leaves the same record as a console one. Called twice, it does
        not add the file handler again."""
        logging.basicConfig(level=logging.INFO)  # the terminal handler, turned down just below
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        # The log file keeps INFO, so only the other handlers (the terminal) are turned down. Turning them
        # all down would silence the log file on the second call, which is what a web run used to do.
        already_open = [handler for handler in root_logger.handlers
                        if isinstance(handler, logging.FileHandler)
                        and getattr(handler, "baseFilename", "") == os.path.abspath(LOG_PATH)]
        for terminal_handler in root_logger.handlers:
            if terminal_handler not in already_open:
                terminal_handler.setLevel(terminal_level)
        if already_open:
            return
        try:
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            log_file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
            log_file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
            root_logger.addHandler(log_file_handler)
        except OSError as error:
            # No log file (e.g. a read-only folder): the program still runs, with terminal logging only
            logging.warning(f"Could not open the log file {LOG_PATH} ({type(error).__name__}); "
                            "logging to the terminal only")

    def __init__(self, resume_folder: str = DEFAULT_RESUME_FOLDER, read: Callable[[str], str] = input,
                 write: Callable[[str], None] = print) -> None:
        self.__resume_folder = resume_folder  # where the resumes are (a temporary folder in tests)
        self.__read = read    # where typed answers come from (the keyboard, or a script in tests)
        self.__write = write  # where messages go (the terminal, or a buffer in tests)

    # ---------- wording: how jobs, notes and changes are written for the console and the pages ----------

    def shorten(self, text: str, width: int) -> str:
        """Cut text to fit a column."""
        text = str(text)
        if len(text) <= width:
            return text
        return text[:width - 3] + "..."

    def join_list(self, value: list | None) -> str:
        """Join a list into comma-separated text."""
        if not isinstance(value, list) or not value:
            return "None"
        names = []
        for item in value:
            if isinstance(item, dict):
                item = item.get("job_skill", "")
            names.append(str(item))
        return ", ".join(names)

    def format_salary(self, job: dict) -> str:
        """Return a job's salary range as text."""
        low = job.get("min_salary", 0)
        high = job.get("max_salary", 0)
        if not isinstance(low, (int, float)) or not isinstance(high, (int, float)) or (low == 0 and high == 0):
            return "Not stated"
        return f"${low:,.0f} - ${high:,.0f} {job.get('salary_period', 'monthly')}"

    def tidy_title(self, title: str | None) -> str:
        """Capitalised titles, e.g. "SERVICE CREW" -> "Service Crew"; others stay as they are, and a missing one is N/A."""
        if not title or title == "N/A":  # "N/A" is a placeholder, not a title to capitalise ("N/a")
            return "N/A"
        if not title.isupper():
            return title
        return " ".join(word if word in ACRONYMS else word.capitalize() for word in title.split())

    def short_location(self, location: str) -> str:
        """First place in a MyCareersFuture district, e.g. "D19 Hougang, Sengkang, ..." -> "Hougang"."""
        place = re.sub(r"^D\d+\s+", "", location or "").split(",")[0].strip()
        return place or location

    def experience_text(self, years: int) -> str:
        """A job's minimum experience as text, e.g. 0 -> "No experience needed", 2 -> "2+ years"."""
        if not years:
            return "No experience needed"
        return f"{years}+ year{'s' if years != 1 else ''}"

    def same_skill(self, job_skill: str, candidate_skill: str) -> bool:
        """True if two skill names are the same apart from case and spaces, e.g. "SQL" and "sql",
        so a match under another name ("Stakeholder Management" by "Stakeholder Communication") can be shown."""
        return job_skill.strip().lower() == candidate_skill.strip().lower()

    def search_step_message(self, step: str, filters: dict) -> str:
        """What to tell the user when the job search had to drop some preferences, or "" if it didn't."""
        if step == "job type":
            return f"No jobs matched all your preferences, so these match your job type ({filters['job_type']}) only."
        if step == "general":
            return "No jobs matched your preferences, so these are general results for your resume."
        return ""

    def ai_unavailable_message(self) -> str:
        """What to tell the user when the AI could not read their resume (console and web)."""
        return ("The AI could not analyse your resume: it either couldn't be reached or gave an answer that couldn't "
                "be used. Try again in a minute; if it keeps happening, check your internet connection and API key.")

    def search_problem_message(self, listings_found: int, jobs_analysed: int, portal_failed: bool = True) -> str:
        """Why a job search has nothing to show (console and web), or "" if it found and analysed some listings.
        portal_failed: False when MyCareersFuture answered but listed nothing, so it isn't a connection problem."""
        if listings_found == 0 and not portal_failed:
            return ("MyCareersFuture has no listings for your resume's job titles and filters right now. "
                    "Try loosening your filters, or another resume.")
        if listings_found == 0:
            return ("Could not get any job listings from MyCareersFuture. Check your internet "
                    "connection, or try again in a few minutes.")
        if jobs_analysed == 0:
            return (f"The AI could not analyse any of the {listings_found} listings "
                    "found. Check your API key, or wait a minute if the AI service is busy.")
        return ""

    def partial_results_message(self, summary: dict, jobs_analysed: int) -> str:
        """What a search that partly failed left out (console and web), or "" if nothing was left out: listings the
        AI could not analyse, and job title searches MyCareersFuture didn't answer."""
        problems = []
        left_out = summary.get("listings_found", 0) - jobs_analysed
        if left_out > 0:
            problems.append(f"{left_out} of {summary['listings_found']} listings could not be analysed by the AI")
        if summary.get("searches_errored"):
            problems.append(f"{summary['searches_errored']} of {summary.get('searches', summary['searches_errored'])} "
                            "job title searches on MyCareersFuture failed")
        if not problems:
            return ""
        return f"{' and '.join(problems)}, so some matches may be missing. Search again later to fill the gaps."

    def search_summary(self, checked: int, jobs_per_search: int, keywords: list[str], removed: dict[str, int], shown: int) -> str:
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

    def describe_tailoring_notes(self, notes: list[dict]) -> list[str]:
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

    def describe_docx_changes(self, paragraphs: list[dict], edits: dict[int, str], reasons: dict[int, str]) -> list[str]:
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

    def describe_what_if(self, report: dict) -> list[str]:
        """The lines the console shows for one what-if: the headline, then each job's move."""
        lines = [f"If you learned {', '.join(report['skills'])}:",
                 f"  {report['jobs_moved_up']} of {len(report['movements'])} jobs move up ({report['places_gained']} places in "
                 f"total) and {report['outcome_upgrades']} matches improve."]
        if report['entered_top']:
            lines.append(f"  Enter your top {report['top_n']}: {', '.join(self.tidy_title(m['title']) for m in report['entered_top'])}")
        if report['left_top']:
            lines.append(f"  Drop out of your top {report['top_n']}: {', '.join(self.tidy_title(m['title']) for m in report['left_top'])}")
        for movement in report['movements']:
            change = f"{movement['outcome_before']} -> {movement['outcome_after']}" \
                if movement['outcome_before'] != movement['outcome_after'] else movement['outcome_after']
            gained = f" (covers {', '.join(movement['skills_gained'])})" if movement['skills_gained'] else ""
            lines.append(f"  #{movement['rank_before']} -> #{movement['rank_after']}  "
                         f"{self.shorten(self.tidy_title(movement['title']), 40)} - {change}{gained}")
        return lines

    def describe_roadmap(self, roadmap: dict) -> list[str]:
        """The lines the console shows for a roadmap: the skills in order, then the result of all of them."""
        if roadmap['is_empty']:
            return [f"{roadmap['planner_label']}: none of these skills would improve your matches."]
        lines = [f"Roadmap ({roadmap['planner_label']}):"]
        for number, step in enumerate(roadmap['steps'], start=1):
            effect = (f"lifts jobs {step['places_gained']} place{'s' if step['places_gained'] != 1 else ''} on its own"
                      if step['places_gained'] > 0 else "improves your skill match")
            lines.append(f"  {number}. {step['skill']} - {effect}; with the steps so far "
                         f"{step['report']['jobs_moved_up']} job{'s' if step['report']['jobs_moved_up'] != 1 else ''} move up")
        return lines + [""] + self.describe_what_if(roadmap['report'])

    # ---------- resume files: list, check and read the resumes in the resume folder ----------

    @property
    def resume_folder(self) -> str:
        """The folder the resumes are in."""
        return self.__resume_folder

    def list_resumes(self) -> list[str]:
        """Return the PDF and Word files in the resumes folder."""
        try:
            os.makedirs(self.__resume_folder, exist_ok=True)
            all_names = sorted(os.listdir(self.__resume_folder))
        except OSError:
            return []
        names = []
        for name in all_names:
            extension = os.path.splitext(name)[1].lower()
            if extension in ALLOWED_EXTENSIONS and not name.startswith("~$"):
                names.append(name)
        return names

    def check_resume_file(self, path: str) -> str:
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

    def read_pdf(self, path: str) -> str:
        """Return the text of a PDF file."""
        reader = PdfReader(path)
        pages = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
        return "\n".join(pages)

    def read_docx(self, path: str) -> str:
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

    def read_resume(self, path: str) -> str:
        """Return the resume text, or an empty string if unreadable."""
        try:
            if path.lower().endswith(".pdf"):
                text = self.read_pdf(path)
            else:
                text = self.read_docx(path)
        except Exception as error:
            logging.warning(f"Could not open the file ({type(error).__name__}).")
            return ""
        return text.strip()

    def load_resume(self, path: str) -> tuple[str, str]:
        """Check a resume file and read it. Return (text, problem); problem is empty if it worked."""
        problem = self.check_resume_file(path)
        if problem:
            return "", problem
        text = self.read_resume(path)
        if len(text) < MIN_RESUME_CHARS:
            return "", ("Could not read enough text from that file. It may be a scanned image, "
                        "password-protected or corrupted. Please try another file.")
        return text, ""

    def unused_resume_path(self, filename: str) -> str:
        """Path in the resumes folder that won't replace a saved resume:
        resume.pdf, or resume_2.pdf, resume_3.pdf... if that name is taken."""
        stem, extension = os.path.splitext(filename)
        path = os.path.join(self.__resume_folder, filename)
        number = 2
        while os.path.exists(path):
            path = os.path.join(self.__resume_folder, f"{stem}_{number}{extension}")
            number += 1
        return path

    def resume_path_from_form(self, uploaded_name: str, chosen_name: str) -> tuple[str, str]:
        """Where the web form's resume is: the upload's new path or the chosen saved one. Returns (path, problem)."""
        if uploaded_name:
            if os.path.splitext(uploaded_name)[1].lower() not in ALLOWED_EXTENSIONS:
                return "", "Please upload a PDF or Word (.docx) file."
            os.makedirs(self.__resume_folder, exist_ok=True)
            return self.unused_resume_path(uploaded_name), ""
        if chosen_name not in self.list_resumes():
            return "", "Please choose a resume or upload one."
        return os.path.join(self.__resume_folder, chosen_name), ""

    # ---------- console display ----------

    def display_message(self, text: str) -> None:
        """Print a normal message."""
        self.__write(text)

    def display_error(self, text: str) -> None:
        """Print an error message."""
        self.__write(f"[!] {text}")

    def display_header(self, title: str) -> None:
        """Print a section title."""
        self.__write("")
        self.__write("=" * LINE_WIDTH)
        self.__write(title.center(LINE_WIDTH))
        self.__write("=" * LINE_WIDTH)

    def display_profile(self, profile: dict) -> None:
        """Show what the AI found in the resume."""
        self.display_header("YOUR PROFILE")
        self.__write(f"Name          : {profile.get('candidate_name', 'N/A')}")
        self.__write(f"Qualification : {profile.get('qualification_detail', 'N/A')} "
              f"({profile.get('highest_qualification', 'N/A')})")
        self.__write(f"Experience    : {profile.get('years_of_experience', 'N/A')} years full-time, "
              f"{profile.get('internship_months', 'N/A')} months internship")
        self.__write(f"Level         : {profile.get('seniority_level', 'N/A')}")
        self.__write(f"Skills        : {self.join_list(profile.get('core_skills'))}")
        self.__write(f"Certifications: {self.join_list(profile.get('certifications'))}")

    def display_record(self, job: dict) -> None:
        """Show every detail of one job."""
        self.display_header(self.shorten(job.get("title", "N/A"), LINE_WIDTH))
        self.__write(f"Company         : {job.get('company', 'N/A')}")
        self.__write(f"Location        : {job.get('location', 'N/A')}")
        self.__write(f"Salary          : {self.format_salary(job)}")
        self.__write(f"Job type        : {self.join_list(job.get('employment_types'))}")
        self.__write(f"Work arrangement: {job.get('work_arrangement', 'N/A')}")
        self.__write(f"Experience      : {job.get('min_years_experience', 'N/A')} years minimum")
        self.__write(f"Education       : {job.get('min_education', 'N/A')}")
        self.__write(f"Skills you have : {self.join_list(job.get('matched_skills'))}")
        self.__write(f"Skills missing  : {self.join_list(job.get('missing_skills'))}")
        if job.get("match_outcome"):  # jobs saved before outcomes existed don't have one
            self.__write(f"Match           : {job['match_outcome']}. {job.get('outcome_reason', '')}")
        if job.get("status"):  # set by "update" on a saved job
            self.__write(f"Status          : {job['status']}")
        if job.get("note"):
            self.__write(f"Note            : {job['note']}")
        self.__write(f"Why             : {job.get('suitability_reason', 'N/A')}")
        self.__write(f"Link            : {job.get('job_url', 'N/A')}")

    def describe_tracker(self, summary: dict) -> str:
        """The application tracker in one line, e.g. "Saved 4 · Applied 2 · Interviewing 1 · Offer 0 · Rejected 0.
        3 strong matches are not applied to yet." (DataManager.tracker_summary)."""
        stages = " · ".join(f"{status} {count}" for status, count in summary["by_status"].items())
        waiting = summary["strong_not_applied"]
        if not waiting:
            return stages
        return f"{stages}. {waiting} strong match{'es are' if waiting != 1 else ' is'} not applied to yet."

    def display_tailoring(self, result: dict, saved_path: str) -> None:
        """Show what tailoring a resume changed, what it left out and where the new Word file is."""
        self.display_header("TAILORED RESUME")
        self.__write(f"Saved as: {saved_path}")
        if not result.get("had_description"):
            self.display_error("The full job description could not be fetched, so it was tailored to the job's title "
                               "and skills only.")
        self.__write("\nWhat changed:")
        for change in result.get("changes") or ["Nothing: the resume already fits this job as well as it can."]:
            self.__write(f"  - {change}")
        if result.get("not_added"):
            self.__write(f"\nNot added (your resume doesn't show them): {', '.join(result['not_added'])}")
        for note in result.get("notes", []):
            self.__write(f"  * {note}")

    def save_tailored_resume(self, docx_bytes: bytes, resume_name: str, job_title: str) -> str:
        """Writes a tailored resume next to the resumes, in a "tailored" folder, without replacing an earlier one,
        e.g. resumes/tailored/my_cv_Data_Analyst.docx. Returns its path; raises OSError if it can't be written."""
        folder = os.path.join(self.__resume_folder, "tailored")
        os.makedirs(folder, exist_ok=True)
        stem = re.sub(r"[^A-Za-z0-9-]+", "_", f"{os.path.splitext(resume_name)[0]}_{job_title}").strip("_")[:80]
        path, number = os.path.join(folder, f"{stem or 'tailored_resume'}.docx"), 2
        while os.path.exists(path):
            path = os.path.join(folder, f"{stem}_{number}.docx")
            number += 1
        with open(path, "wb") as file:
            file.write(docx_bytes)
        return path

    def display_list(self, jobs: list[dict]) -> None:
        """Show a numbered table of jobs."""
        self.__write(f"{'No.':<4} {'Title':<25} {'Company':<16} {'Match':<12} {'Salary'}")
        self.__write("-" * LINE_WIDTH)
        for number, job in enumerate(jobs, start=1):
            self.__write(f"{number:<4} {self.shorten(job.get('title', 'N/A'), 25):<25} "
                  f"{self.shorten(job.get('company', 'N/A'), 16):<16} {job.get('match_outcome', '-'):<12} {self.format_salary(job)}")

    def display_result(self, profile: dict, jobs: list[dict]) -> None:
        """Show the profile and the matching jobs."""
        self.display_profile(profile)
        self.display_header(f"MATCHING JOBS ({len(jobs)})")
        if not jobs:
            self.display_message("No jobs matched. Try a new search with wider filters.")
            return
        self.display_list(jobs)

    def prompt_text(self, prompt: str, allow_blank: bool = False) -> str:
        """Ask for text until something is typed (or once, if allow_blank)."""
        while True:
            answer = self.__read(f"{prompt}: ").strip()
            if answer or allow_blank:
                return answer
            self.display_error("This cannot be left blank.")

    def prompt_number(self, prompt: str, minimum: int, maximum: int, default: int | None = None) -> int:
        """Ask for a whole number within a range."""
        hint = f"{minimum}-{maximum}"
        if default is not None:
            hint += f", Enter for {default}"
        while True:
            answer = self.__read(f"{prompt} ({hint}): ").strip()
            if answer == "" and default is not None:
                return default
            answer = answer.replace(",", "").replace("$", "")
            try:
                value = int(answer)
            except ValueError:
                self.display_error(f"Please enter a whole number between {minimum} and {maximum}.")
                continue
            if value < minimum or value > maximum:
                self.display_error(f"Please enter a number between {minimum} and {maximum}.")
                continue
            return value

    def prompt_choice(self, prompt: str, options: list[str]) -> str:
        """Show numbered options and return the one picked."""
        self.__write("")
        self.__write(prompt)
        for number, option in enumerate(options, start=1):
            self.__write(f"  {number}. {option}")
        choice = self.prompt_number("Choose", 1, len(options))
        return options[choice - 1]

    def prompt_yes_no(self, prompt: str) -> bool:
        """Ask a yes/no question."""
        while True:
            answer = self.__read(f"{prompt} (y/n): ").strip().lower()
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            self.display_error("Please type y or n.")

    def prompt_resume(self) -> dict | None:
        """Let the user pick a resume and return its path and text."""
        while True:
            names = self.list_resumes()
            if not names:
                self.display_error("No PDF or Word (.docx) files found. Put your resume in this folder:")
                self.display_message(f"    {self.resume_folder}")
                answer = self.__read("Press Enter to check again, or q to cancel: ").strip().lower()
                if answer == "q":
                    return None
                continue

            choice = self.prompt_choice("Choose your resume", names + [MENU_REFRESH, MENU_CANCEL])
            if choice == MENU_CANCEL:
                return None
            if choice == MENU_REFRESH:
                continue

            path = os.path.join(self.resume_folder, choice)
            text, problem = self.load_resume(path)
            if problem:
                self.display_error(problem)
                continue

            self.display_message(f"Resume loaded ({len(text)} characters).")
            return {"resume_path": path, "resume_text": text}

    def prompt_filters(self) -> dict:
        """Ask for job preferences and return them as a dict."""
        self.display_message("\nYour job preferences (press Enter to accept the default)")

        while True:
            min_salary = self.prompt_number("Minimum monthly salary", 0, MAX_SALARY, default=0)
            max_salary = self.prompt_number("Maximum monthly salary", 0, MAX_SALARY, default=MAX_SALARY)
            if max_salary >= min_salary:
                break
            self.display_error("Maximum salary cannot be lower than minimum salary. Please enter both again.")

        max_years = self.prompt_number("Hide jobs that need more than how many years of experience",
                                  0, MAX_EXPERIENCE_YEARS, default=MAX_EXPERIENCE_YEARS)
        job_type = self.prompt_choice("Job type", JOB_TYPES)
        work_arrangement = self.prompt_choice("Work arrangement", WORK_ARRANGEMENTS)

        return {
            "min_salary": min_salary,
            "max_salary": max_salary,
            "max_years_experience": max_years,
            "job_type": job_type,
            "work_arrangement": work_arrangement,
        }

    def collect_input(self, ai_name: str = "an AI service") -> dict | None:
        """Collect the resume and filters for one search. ai_name is shown in the privacy notice."""
        self.display_header("NEW SEARCH")
        self.display_message(f"Note: your resume's text is sent to {ai_name} to analyse it. Remove anything you'd "
                        "rather not share (e.g. your home address or NRIC) first.")
        record = self.prompt_resume()
        if record is None:
            return None
        record["filters"] = self.prompt_filters()
        return record

    def prompt_missing_data(self, profile: dict) -> dict:
        """Ask for details the AI could not find in the resume."""
        if profile.get("highest_qualification", "None") == "None":
            self.display_message("\nThe AI could not find a completed qualification in your resume.")
            level = self.prompt_choice("Your highest completed qualification", EDUCATION_LEVELS)
            profile["highest_qualification"] = level
            if not profile.get("qualification_detail"):
                profile["qualification_detail"] = level

        if not profile.get("core_skills"):
            self.display_message("\nThe AI could not find any skills in your resume.")
            skills = []
            while not skills:
                answer = self.prompt_text("Your skills, separated by commas (e.g. Python, Excel)")
                for skill in answer.split(","):
                    if skill.strip():
                        skills.append(skill.strip())
                if not skills:
                    self.display_error("Please enter at least one skill.")
            profile["core_skills"] = skills

        return profile

    def check_filters(self, values: Mapping[str, str]) -> tuple[dict, str]:
        """The web version of prompt_filters: check filters typed into a form (any dict-like of text).
        Return (filters, problem), with filters in the same shape as prompt_filters."""
        numbers = {}
        # name: (default, maximum, how the form labels it, unit)
        limits = {
            "min_salary": (0, MAX_SALARY, "Minimum salary", "dollars a month"),
            "max_salary": (MAX_SALARY, MAX_SALARY, "Maximum salary", "dollars a month"),
            "max_years_experience": (MAX_EXPERIENCE_YEARS, MAX_EXPERIENCE_YEARS, "Maximum years of experience", "years"),
        }
        for name, (default, maximum, label, unit) in limits.items():
            text = values.get(name, "").strip().replace(",", "").replace("$", "")
            # Plain digits only: int() would also take "+3000", "3_000" and digits from other scripts
            if text and not (text.isascii() and text.isdigit()):
                return {}, f"{label} must be a whole number of 0 or more, e.g. {min(3000, maximum)}."
            numbers[name] = int(text) if text else default
            if not 0 <= numbers[name] <= maximum:
                return {}, f"{label} must be between 0 and {maximum:,} {unit}."
        if numbers["max_salary"] < numbers["min_salary"]:
            return {}, "Maximum salary cannot be lower than minimum salary."

        job_type = values.get("job_type", ANY)
        work_arrangement = values.get("work_arrangement", ANY)
        if job_type not in JOB_TYPES or work_arrangement not in WORK_ARRANGEMENTS:
            return {}, "Please pick a job type and work arrangement from the lists."
        return {**numbers, "job_type": job_type, "work_arrangement": work_arrangement}, ""

    def fill_missing_data(self, profile: dict, qualification: str, skills_text: str) -> dict:
        """The web version of prompt_missing_data: fill in what the AI could not find in the resume
        from the search form's optional fields. skills_text is comma-separated, e.g. "Python, Excel"."""
        if profile["highest_qualification"] == "None" and qualification in EDUCATION_LEVELS:
            profile["highest_qualification"] = qualification
            profile["qualification_detail"] = profile["qualification_detail"] or qualification
        skills = [skill.strip() for skill in skills_text.split(",") if skill.strip()]
        if not profile["core_skills"] and skills:
            profile["core_skills"] = skills
        return profile

    # ---------- the what-if simulator ----------

    def prompt_whatif_action(self, planners: list[tuple[str, str]]) -> tuple[str, str]:
        """What to do with the simulator. Returns ("back", ""), ("test", "") or ("roadmap", planner key)."""
        labels = {f"Roadmap: {label}": key for key, label in planners}
        choice = self.prompt_choice("What would you like to do?", [WHATIF_TEST, *labels, WHATIF_BACK])
        if choice == WHATIF_BACK:
            return "back", ""
        return ("test", "") if choice == WHATIF_TEST else ("roadmap", labels[choice])

    def prompt_skill_list(self, available: list[str], prompt: str, allow_blank: bool = False) -> list[str]:
        """Skills typed as a comma-separated list (shown the skills the jobs ask for). Not checked here."""
        self.display_message("Skills the jobs ask for that you don't have (most wanted first):")
        self.display_message("  " + self.shorten(", ".join(available), LINE_WIDTH * 3))
        answer = self.prompt_text(prompt, allow_blank=allow_blank)
        return [skill.strip() for skill in answer.split(",") if skill.strip()]

    def display_what_if(self, report: dict) -> None:
        for line in self.describe_what_if(report):
            self.display_message(line)

    def display_roadmap(self, roadmap: dict) -> None:
        for line in self.describe_roadmap(roadmap):
            self.display_message(line)

    def prompt_job_update(self, statuses: list[str], current_status: str | None) -> tuple[str | None, str | None]:
        """A saved job's new status and note, as (status, note); None for one left as it is (Enter keeps the note)."""
        keep = f"Keep as it is ({current_status or statuses[0]})"
        choice = self.prompt_choice("Status", [keep, *statuses])
        note = self.prompt_text("Note (Enter to keep the current one)", allow_blank=True)
        return (None if choice == keep else choice), (note or None)
