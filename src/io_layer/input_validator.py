"""InputValidator: checks what the user sends (forms, uploads, saved-job changes) and says what is wrong in words.

It belongs to the IO manager, which the brief gives "collect and validate user input": IOManager builds it and runs
every check before any work starts. It has no state. Every check returns a problem message, "" if the input is
valid, so the web pages can show the message as it is.
"""

import os
from collections.abc import Mapping, Sequence

# The saved-job stages and the longest note are the data layer's own rules
from data_layer.data_manager import MAX_NOTE_CHARS, STATUSES  # noqa: F401  (STATUSES is also read from here)
# The filters' choices and limits are the business rules' own ("Any" = no preference): the logic layer owns them
from logic_layer.logic_manager import (ANY, EDUCATION_LEVELS, JOB_TYPES, MAX_EXPERIENCE_YEARS,  # noqa: F401
                                       MAX_SALARY, WORK_ARRANGEMENTS)

# The resumes: which files count, and the least text a readable resume has
# (resume_files.py has its own list of extensions, for listing the folder)
ALLOWED_EXTENSIONS = [".pdf", ".docx"]
MIN_RESUME_CHARS = 100
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # largest resume upload: 5 MB (app.js checks it too)

SHOW_OPTIONS = [5, 10, 20]  # "Show" choices on the results page
DEFAULT_SHOW = 5

MAX_SKILLS = 15  # most skills one what-if can test
MAX_STEPS = 10   # longest roadmap
DEFAULT_STEPS = 3  # roadmap length when the form doesn't say
MAX_SKILL_LENGTH = 60  # longest skill name accepted (the jobs' own skills are far shorter)


class InputValidator:
    """The input rules: search filters, resume files, uploads, What-If forms and saved-job changes."""

    def check_filters(self, values: Mapping[str, str]) -> tuple[dict, str]:
        """Checks the search form (any dict-like of text). Returns (filters, problem): the five filters, "show"
        (how many jobs to list; a choice that is not offered becomes DEFAULT_SHOW) and the optional
        "fallback_qualification" and "fallback_skills" (a list) the AI's reading of the resume can be filled in from.
        On a problem, ({}, message)."""
        numbers = {}
        # name: (default, maximum, how the form labels it, unit)
        limits = {
            "min_salary": (0, MAX_SALARY, "Minimum salary", "dollars a month"),
            "max_salary": (MAX_SALARY, MAX_SALARY, "Maximum salary", "dollars a month"),
            "max_years_experience": (MAX_EXPERIENCE_YEARS, MAX_EXPERIENCE_YEARS, "Experience asked", "years"),
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

        try:
            show = int(values.get("show", ""))
        except ValueError:
            show = DEFAULT_SHOW
        qualification = values.get("fallback_qualification", "")
        skills = [skill.strip() for skill in values.get("fallback_skills", "").split(",") if skill.strip()]
        return {**numbers, "job_type": job_type, "work_arrangement": work_arrangement,
                "show": show if show in SHOW_OPTIONS else DEFAULT_SHOW,
                "fallback_qualification": qualification if qualification in EDUCATION_LEVELS[1:] else "",
                "fallback_skills": skills}, ""

    def check_resume(self, filename: str, text: str) -> str:
        """What is wrong with a resume file's name or the text read from it, or ""."""
        extension = os.path.splitext(filename)[1].lower()
        if extension not in ALLOWED_EXTENSIONS:
            return f"'{extension}' files are not supported. Please use a PDF or Word (.docx) file."
        if len(text) < MIN_RESUME_CHARS:
            return ("Could not read enough text from that file. It may be a scanned image, "
                    "password-protected or corrupted. Please try another file.")
        return ""

    def check_upload(self, filename: str, size: int) -> str:
        """What is wrong with an uploaded file, going by its name and size in bytes, or ""."""
        if os.path.splitext(filename)[1].lower() not in ALLOWED_EXTENSIONS:
            return "Please upload a PDF or Word (.docx) file."
        if size == 0:
            return "That file is empty."
        if size > MAX_UPLOAD_BYTES:
            return f"That file is too large. Please upload a resume under {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        return ""

    def check_what_if(self, values: Mapping, skills: Sequence[str]) -> tuple[dict, str]:
        """Checks the what-if form. values has "mode" ("test", or a planner's key), "skills" (the ticked ones),
        "extra_skills" (typed, comma separated), "steps" and "top_n" (text or whole numbers; a missing one means the
        default). skills are the ones the jobs ask for: a chosen skill that matches one ignoring case and spaces
        is returned the way the jobs write it. Returns ({"mode", "skills", "steps", "top_n"}, "") or ({}, problem).
        In a roadmap, no skills at all is returned as None: the simulator chooses. It does not reject a skill the
        jobs don't ask for: only the simulator can tell "you already have it" from "no job asks for it"."""
        mode = values.get("mode", "test")
        testing = mode == "test"
        try:
            # Testing skills doesn't use the roadmap length, so a blank one there is fine
            steps = DEFAULT_STEPS if testing else self.__whole_number(values.get("steps", DEFAULT_STEPS))
            top_n = self.__whole_number(values.get("top_n", DEFAULT_SHOW))
        except ValueError:
            return {}, ("Use a whole number for the list size." if testing
                        else "Use whole numbers for the number of skills and the list size.")
        if top_n < 1:
            return {}, "The list must show at least 1 job."
        if not testing and not 1 <= steps <= MAX_STEPS:
            return {}, f"A plan can have 1 to {MAX_STEPS} skills."

        typed = [skill.strip() for skill in values.get("extra_skills", "").split(",") if skill.strip()]
        chosen = [skill.strip() for skill in [*values.get("skills", []), *typed]
                  if isinstance(skill, str) and skill.strip()]
        if not chosen and testing:
            # Says the way out, because a roadmap is the mode that does accept no skills at all
            return {}, "Pick at least one skill, or use Plan what to learn."
        if any(len(skill) > MAX_SKILL_LENGTH for skill in chosen):
            return {}, f"Each skill name must be {MAX_SKILL_LENGTH} characters or fewer."
        # A roadmap may choose from any number of ticked skills: it only tests them one at a time
        if testing and len(chosen) > MAX_SKILLS:
            return {}, f"Please test {MAX_SKILLS} skills or fewer at a time."
        seen: set[str] = set()
        for skill in chosen:
            if self.__normalise(skill) in seen:
                return {}, f'"{skill}" is listed more than once.'
            seen.add(self.__normalise(skill))

        written = {self.__normalise(skill): skill for skill in skills}
        spelled = [written.get(self.__normalise(skill), skill) for skill in chosen]
        return {"mode": mode, "skills": spelled if spelled or testing else None, "steps": steps, "top_n": top_n}, ""

    @staticmethod
    def __whole_number(value: object) -> int:
        """A whole number given as a number or as text. ValueError if it is neither (a blank box included)."""
        if isinstance(value, bool):
            raise ValueError
        return value if isinstance(value, int) else int(str(value))

    @staticmethod
    def __normalise(skill: str) -> str:
        """A skill's comparison form: "SQL", " sql " and "Sql" are one skill."""
        return " ".join(skill.lower().split())

    def check_saved_update(self, status: str | None, note: str | None) -> str:
        """What is wrong with a saved job's new status or note (None or "" means no change), or ""."""
        if status and status not in STATUSES:
            return f"Status must be one of {', '.join(STATUSES)}"
        if note is not None and len(note.strip()) > MAX_NOTE_CHARS:
            return f"A note can be at most {MAX_NOTE_CHARS} characters"
        return ""
