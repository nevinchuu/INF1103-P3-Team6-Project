"""AI Manager: prompts, API calls, JSON parsing and schema validation, behind one class.

Every AI call goes through AIManager.run_task(task_name, record): build the prompt -> provider.complete ->
parse -> validate against the task's schema in TASKS. The manager knows nothing about which AI service answers
(any AIProvider), so adding a provider changes neither it nor the other managers. Adding a kind
of call means one schema, one prompt method and one entry in TASKS. Called by the apps and SearchService.
"""

import json
import logging
import os
import re
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from ai_layer.ai_provider import AIProvider
from ai_layer.caching_ai_provider import CachingAIProvider
from ai_layer.gemini_provider import GeminiProvider


# Ordered lowest to highest so logic_manager can compare levels by index
# e.g. EDUCATION_LEVELS.index("Diploma") < EDUCATION_LEVELS.index("Bachelor's")
EDUCATION_LEVELS = ["None", "Secondary", "ITE/Nitec", "A-Level", "Diploma", "Bachelor's", "Master's", "Doctorate"]


JOB_EDUCATION_LEVELS = ["Not specified"] + EDUCATION_LEVELS


# Same labels MyCareersFuture uses in positionLevels
SENIORITY_LEVELS = [
    "Fresh/entry level", "Non-executive", "Junior Executive", "Executive", "Senior Executive",
    "Professional", "Manager", "Middle Management", "Senior Management",
]


WORK_ARRANGEMENTS = ["Onsite", "Hybrid", "Remote", "Not specified"]


# Shorthand for "a list of strings", reused in the schemas below
STRING_LIST = {"type": "array", "items": {"type": "string"}}


# What the AI must return about the candidate (AI call #1)
CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidate_name": {"type": "string"},
        # "enum" = the value must be one of the listed options
        "highest_qualification": {"type": "string", "enum": EDUCATION_LEVELS},
        "qualification_detail": {
            "type": "string",
            "description": "Full name of the highest qualification, e.g. 'Diploma in Financial Technology'",
        },
        "years_of_experience": {
            "type": "number",
            "description": "Full-time work experience in years, excluding internships. 0 for students/fresh grads.",
        },
        "internship_months": {"type": "integer", "description": "Total months of internships/attachments"},
        "seniority_level": {"type": "string", "enum": SENIORITY_LEVELS},
        "core_skills": STRING_LIST,
        "certifications": STRING_LIST,
        "search_keywords": {
            **STRING_LIST,  # ** copies the keys of STRING_LIST into this dict, then adds "description"
            "description": "4-5 short, common job titles to search for (1-3 words), e.g. 'Data Analyst'",
        },
    },
    "required": [
        "candidate_name", "highest_qualification", "qualification_detail", "years_of_experience",
        "internship_months", "seniority_level", "core_skills", "certifications", "search_keywords",
    ],
    "additionalProperties": False,  # the AI may not add extra keys
}


# What the AI must return about ONE job
JOB_REQUIREMENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "job_index": {"type": "integer", "description": "The JOB number this entry is for"},
        "required_skills": STRING_LIST,
        "min_education": {"type": "string", "enum": JOB_EDUCATION_LEVELS},
        "certifications_required": STRING_LIST,
        "work_arrangement": {"type": "string", "enum": WORK_ARRANGEMENTS},
        # Each match must name the candidate skill behind it, e.g.
        # {"job_skill": "React.js", "candidate_skill": "React"}, so logic_manager can check it (add_requirements)
        "skill_matches": {
            "type": "array",
            "description": "Each required skill the candidate covers, with the candidate skill that covers it",
            "items": {
                "type": "object",
                "properties": {"job_skill": {"type": "string"}, "candidate_skill": {"type": "string"}},
                "required": ["job_skill", "candidate_skill"],
                "additionalProperties": False,
            },
        },
        "suitability_reason": {
            "type": "string",
            "description": "1-2 factual sentences comparing the candidate with this job's requirements",
        },
    },
    "required": [
        "job_index", "required_skills", "min_education", "certifications_required", "work_arrangement",
        "skill_matches", "suitability_reason",
    ],
    "additionalProperties": False,
}


# Several jobs are sent per call to stay within free-tier rate limits,
# so the AI returns {"jobs": [ <one JOB_REQUIREMENTS_SCHEMA entry per job> ]}
JOB_BATCH_SCHEMA = {
    "type": "object",
    "properties": {"jobs": {"type": "array", "items": JOB_REQUIREMENTS_SCHEMA}},
    "required": ["jobs"],
    "additionalProperties": False,
}


# One entry in a resume section, e.g. a job, a course or a project
RESUME_ENTRY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "e.g. 'Data Analyst Intern' or 'Diploma in Information Technology'"},
        "organisation": {"type": "string",
                         "description": "Company, school or group, if on the same line as the title. Empty string if not."},
        "subtitle": {"type": "string",
                     "description": "The line under the title, as written, e.g. 'Example Polytechnic | GPA 3.6 / 4.0'. "
                                    "Empty string if none."},
        "dates": {"type": "string", "description": "Copied exactly from the resume. Empty string if none."},
        "bullets": STRING_LIST,
    },
    "required": ["title", "organisation", "subtitle", "dates", "bullets"],
    "additionalProperties": False,
}


# One group of skills, e.g. "Technical: Python, SQL"
SKILL_GROUP_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "description": "e.g. 'Technical'. Empty string if the skills aren't grouped."},
        "skills": STRING_LIST,
    },
    "required": ["label", "skills"],
    "additionalProperties": False,
}


# One section of a resume, in the resume's own order. kind says which of text, skill_groups
# and entries it uses; the other two are left empty.
RESUME_SECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "heading": {"type": "string", "description": "Copied exactly from the resume, e.g. 'PROFILE'"},
        "kind": {"type": "string", "enum": ["text", "skills", "entries"],
                 "description": "text: a paragraph, e.g. a profile; skills: skill lists; "
                                "entries: jobs, qualifications, projects and similar"},
        "text": {"type": "string", "description": "For kind text. Empty string otherwise."},
        "skill_groups": {"type": "array", "items": SKILL_GROUP_SCHEMA,
                         "description": "For kind skills. Empty list otherwise."},
        "entries": {"type": "array", "items": RESUME_ENTRY_SCHEMA, "description": "For kind entries. Empty list otherwise."},
    },
    "required": ["heading", "kind", "text", "skill_groups", "entries"],
    "additionalProperties": False,
}


# For each of the job card's matched skills, where the tailored resume shows it (both tailoring calls).
# Checked afterwards (TailoringGuard.skills_shown): each quote must really be in the tailored resume
SKILL_EVIDENCE_SCHEMA = {
    "type": "array",
    "description": "For each MATCHED SKILL, the phrase in this tailored resume that shows it",
    "items": {
        "type": "object",
        "properties": {
            "job_skill": {"type": "string", "description": "Copied exactly from MATCHED SKILLS"},
            "evidence": {"type": "string", "description": "Copied exactly from this tailored resume"},
        },
        "required": ["job_skill", "evidence"],
        "additionalProperties": False,
    },
}


# A resume rewritten for one job (tailor_resume), keeping the resume's own sections, order and
# headings. Checked against the original resume afterwards (logic_manager.ground_tailored_resume), so
# nothing the candidate didn't write gets through.
TAILORED_RESUME_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "contact": {"type": "string", "description": "The contact line, copied exactly with its separators"},
        "sections": {"type": "array", "items": RESUME_SECTION_SCHEMA,
                     "description": "Every section of the resume, in the resume's own order"},
        "changes": {**STRING_LIST, "description": "3-6 short notes on what was changed for this job and why"},
        "skill_evidence": SKILL_EVIDENCE_SCHEMA,
    },
    "required": ["name", "contact", "sections", "changes", "skill_evidence"],
    "additionalProperties": False,
}


# New wording for some paragraphs of a Word resume (tailor_docx). Only the wording changes:
# the file keeps its own layout. Checked against the original afterwards (logic_manager.ground_docx_edits).
DOCX_EDITS_SCHEMA = {
    "type": "object",
    "properties": {
        "edits": {
            "type": "array",
            "description": "Only the paragraphs that were changed",
            "items": {
                "type": "object",
                "properties": {
                    "paragraph": {"type": "integer", "description": "The [number] of the paragraph"},
                    "text": {"type": "string", "description": "Its new wording. Empty string to remove a bullet."},
                    "reason": {"type": "string",
                               "description": "Why, in under 15 words, e.g. 'leads with SQL, which the job asks for'"},
                },
                "required": ["paragraph", "text", "reason"],
                "additionalProperties": False,
            },
        },
        "skill_evidence": SKILL_EVIDENCE_SCHEMA,
    },
    "required": ["edits", "skill_evidence"],
    "additionalProperties": False,
}


# The kinds of AI call this module makes, and the schema each one uses
TASKS = {
    "candidate_profile": CANDIDATE_SCHEMA,
    "job_requirements": JOB_BATCH_SCHEMA,
    "tailored_resume": TAILORED_RESUME_SCHEMA,
    "docx_edits": DOCX_EDITS_SCHEMA,
}


# Range checks applied by validate_response (inclusive)
NUMERIC_RANGES = {"years_of_experience": (0, 60), "internship_months": (0, 240), "job_index": (0, 999),
                  "paragraph": (0, 9999)}


NON_EMPTY_LISTS = {"search_keywords"}  # we can't search for jobs without at least one keyword

# One rule for how a skill is written, in the resume's skills and in each job's key skills, so the same skill has
# the same name on both sides (LogicManager also treats an abbreviation as its full name: SkillName)
SKILL_NAMING_RULE = ('Write each skill once, by its full common name: "User Acceptance Testing", not "UAT" or '
                     '"UAT Testing"; "Microsoft Excel", not "MS Excel". Keep an abbreviation only where it is the '
                     'name everyone uses (SQL, KYC, AWS).')



JOB_BATCH_SIZE = 10            # jobs sent to the AI per call
MALFORMED_RESPONSE_RETRIES = 1  # extra tries when a reply isn't valid JSON or fails the schema check
MAX_RESUME_CHARS = 20000       # longer resume text is cut, so a huge file can't overflow the AI's input limit
DEFAULT_PROVIDER = "gemini"
# Saved AI replies (CachingAIProvider), next to the saved jobs in <project root>/data/
AI_CACHE_PATH = str(Path(__file__).resolve().parents[2] / "data" / "ai_cache.json")  # <project root>/data/


class AIManager:
    """Runs the AI tasks: builds each prompt, calls the provider, parses and validates the reply."""

    # Which class handles each provider (a lookup, so adding a provider does not need an if/else chain)
    __PROVIDER_CLASSES = {"gemini": GeminiProvider}

    def __init__(self, provider: AIProvider) -> None:
        # Aggregation: the provider is handed in and lives on its own (it can be swapped, shared or faked);
        # the manager only uses it
        self.__provider = provider
        # How each kind of AI call writes its prompt (its schema is in TASKS)
        self.__prompts = {"candidate_profile": self.__candidate_prompt,
                          "job_requirements": self.__requirements_prompt,
                          "tailored_resume": self.__tailored_resume_prompt,
                          "docx_edits": self.__docx_edits_prompt}

    @classmethod
    def from_environment(cls) -> "AIManager":
        """A manager using the provider chosen by AI_PROVIDER and AI_MODEL in .env (or the defaults); every record
        goes to the live AI, unless AI_CACHE=on wraps the provider in a CachingAIProvider."""
        # Load .env from the project root (parent of src/), so os.getenv("GEMINI_API_KEY") etc. work
        load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")
        provider = cls.create_provider(os.getenv("AI_PROVIDER", DEFAULT_PROVIDER), os.getenv("AI_MODEL"))
        # Live AI for every record by default. AI_CACHE=on saves and replays replies (same input, same output, and an
        # offline demo can replay a run done earlier), e.g. as a fallback when the network or the API is down
        if os.getenv("AI_CACHE", "off").strip().lower() in ("on", "1", "true", "yes"):
            provider = CachingAIProvider(provider, os.getenv("AI_CACHE_PATH") or AI_CACHE_PATH)
        return cls(provider)

    @classmethod
    def create_provider(cls, name: str, model: str | None = None) -> AIProvider:
        """A provider by name; an unknown name falls back to the default one, with a warning."""
        name = name.strip().lower()
        if name not in cls.__PROVIDER_CLASSES:
            logging.warning(f"Unknown AI provider '{name}', using {DEFAULT_PROVIDER}.")
            name = DEFAULT_PROVIDER
        return cls.__PROVIDER_CLASSES[name](model)

    @property
    def provider_name(self) -> str:
        """The AI service in use, as people know it, e.g. "Google Gemini" (for the privacy notice)."""
        return self.__provider.provider_name

    @property
    def setup_problem(self) -> str:
        """Why the AI can't work until the setup is fixed (e.g. "GEMINI_API_KEY is not set"), or ""."""
        return self.__provider.setup_problem

    # ---------- the pipeline every AI call follows ----------

    def run_task(self, task_name: str, record: dict) -> dict | None:
        """The prompt for task_name -> provider.complete -> parse -> validate against TASKS[task_name].

        A reply that isn't valid JSON or fails the schema check is asked for again
        (MALFORMED_RESPONSE_RETRIES times). A failed API call is not: the provider has already retried it.
        """
        prompt = self.__prompts[task_name](record)
        schema = TASKS[task_name]
        for attempt in range(1, MALFORMED_RESPONSE_RETRIES + 2):
            raw = self.__provider.complete(prompt, task_name, schema)
            if raw is None:
                return None
            # Each step returns None on failure
            data = self.__parse(raw)
            result = self.__validate_schema(data, task_name, schema) if data is not None else None
            if result is not None:
                return result
            self.__provider.discard_reply(prompt, task_name, schema)  # never replay an unusable reply
            if attempt <= MALFORMED_RESPONSE_RETRIES:
                logging.warning(f"[{task_name}] Malformed response; asking again "
                                f"({attempt} of {MALFORMED_RESPONSE_RETRIES}).")
        return None

    # ---------- reading the reply: text -> dict -> checked dict ----------

    @staticmethod
    def __parse(raw: str | None) -> dict | None:
        """Extracts a JSON object from the response text. Handles markdown code fences and stray text around
        the object. Returns None (and logs why) if there is no usable object."""
        if not raw:
            logging.error("Empty response from model.")
            return None

        text = raw.strip()
        # Some models wrap JSON in ```json ... ``` - strip those markers
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
        try:
            data = json.loads(text)  # JSON text -> Python dict
        except json.JSONDecodeError:
            # Fall back to the outermost {...} in the text
            start, end = text.find("{"), text.rfind("}")
            if start == -1 or end <= start:
                logging.error(f"No JSON object found in response:\n{raw}")
                return None
            try:
                data = json.loads(text[start:end + 1])
            except json.JSONDecodeError as error:
                logging.error(f"JSON parse failed ({error}). Raw response:\n{raw}")
                return None

        if not isinstance(data, dict):
            logging.error(f"Expected a JSON object, got {type(data).__name__}.")
            return None
        return data

    def __validate_schema(self, data: dict, task_name: str, schema: dict) -> dict | None:
        """Checks required keys, types, enum values and ranges against the schema.

        Returns a cleaned copy (unknown keys dropped, strings trimmed, enums normalised), or None if anything
        is malformed. Malformed entries inside lists of objects are dropped individually.
        """
        try:
            return self.__validate_value(data, schema, task_name, task_name)
        except ValueError as error:
            logging.error(f"[{task_name}] Invalid response: {error}")
            return None

    def __validate_value(self, value: Any, spec: dict, name: str, path: str) -> Any:
        """Validates one value against its schema (recursively) and returns the cleaned value; raises ValueError
        for the first problem. path says where it is, e.g. "job_requirements.jobs[3].min_education"."""
        expected = spec["type"]
        if expected == "string":
            return self.__validate_string(value, spec, path)
        if expected in ("integer", "number"):
            return self.__validate_number(value, expected, name, path)
        if expected == "array":
            return self.__validate_list(value, spec, name, path)
        if expected == "object":
            return self.__validate_object(value, spec, path)
        raise ValueError(f"{path} has unsupported schema type {expected!r}")

    @staticmethod
    def __validate_string(value: Any, spec: dict, path: str) -> str:
        """A string, trimmed; an allowed option ("enum") is matched ignoring case, e.g. "diploma" -> "Diploma"."""
        if not isinstance(value, str):
            raise ValueError(f"{path} should be a string, got {value!r}")
        value = value.strip()
        if "enum" in spec:
            matched = {option.lower(): option for option in spec["enum"]}.get(value.lower())
            if matched is None:
                raise ValueError(f"{path}={value!r} is not one of {spec['enum']}")
            value = matched
        return value

    @staticmethod
    def __validate_number(value: Any, expected: str, name: str, path: str) -> int | float:
        """A number (a whole one for "integer"), inside its NUMERIC_RANGES range if it has one. A whole number is
        kept as an int (2.0 -> 2), so the pages never show "2.0 years"; 1.5 stays 1.5."""
        # bool is checked separately because in Python True/False count as integers
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{path} should be a number, got {value!r}")
        if value != value or value in (float("inf"), float("-inf")):  # NaN is the one value not equal to itself
            raise ValueError(f"{path} should be a finite number, got {value!r}")
        if expected == "integer" and value != int(value):
            raise ValueError(f"{path} should be a whole number, got {value!r}")
        if value == int(value):
            value = int(value)
        if name in NUMERIC_RANGES:
            low, high = NUMERIC_RANGES[name]
            if not low <= value <= high:
                raise ValueError(f"{path}={value} is outside {low}-{high}")
        return value

    def __validate_list(self, value: Any, spec: dict, name: str, path: str) -> list:
        """A list, each item checked; a bad record is dropped on its own, empty strings are dropped."""
        if not isinstance(value, list):
            raise ValueError(f"{path} should be a list, got {value!r}")
        item_spec = spec["items"]
        cleaned = []
        for index, item in enumerate(value):
            try:
                cleaned.append(self.__validate_value(item, item_spec, name, f"{path}[{index}]"))
            except ValueError as error:
                if item_spec["type"] != "object":
                    raise
                # One bad entry in a list of records shouldn't sink the rest
                # (e.g. one malformed job in a batch of 10 is dropped; the other 9 are kept)
                logging.warning(f"Dropping malformed entry: {error}")
        if item_spec["type"] == "string":
            cleaned = [text for text in cleaned if text]  # drop empty strings
        if name in NON_EMPTY_LISTS and not cleaned:
            raise ValueError(f"{path} must not be empty")
        return cleaned

    def __validate_object(self, value: Any, spec: dict, path: str) -> dict:
        """An object with every required key, each value checked; keys the AI added are dropped."""
        if not isinstance(value, dict):
            raise ValueError(f"{path} should be an object, got {value!r}")
        cleaned = {}
        for key in spec["required"]:
            if key not in value:
                raise ValueError(f"{path}.{key} is missing")
            cleaned[key] = self.__validate_value(value[key], spec["properties"][key], key, f"{path}.{key}")
        return cleaned

    # ---------- the prompt for each kind of call ----------

    @staticmethod
    def __candidate_prompt(record: dict) -> str:
        """AI call #1: the resume -> the candidate's profile."""
        # f"""...""" is a multi-line f-string: {...} parts are replaced with values
        return f"""Extract the candidate's details from this resume.

RESUME:
{record["resume_text"]}

TODAY'S DATE: {date.today().isoformat()}

RULES:
- highest_qualification: the MOST ADVANCED qualification the candidate has already COMPLETED,
  picked from {EDUCATION_LEVELS}. That list runs from lowest to highest, so pick the one furthest
  along it, NOT the most recent one, the last one written in the resume, or the one studied first.
  Only a COMPLETED qualification may be picked: one still in progress, "expected", or ending after
  today's date does not count at all, however advanced it is. One that ended on or before today's
  date counts as completed.
  Example: a resume listing a completed Diploma and a completed GCE O-Level is "Diploma", because
  Diploma is further along the list, even though the O-Level is written lower down.
- qualification_detail: full name of that completed qualification.
- years_of_experience: full-time work only, in years (decimals allowed). Internships do NOT count. 0 if none.
- internship_months: total months of internships or attachments. 0 if none.
- seniority_level: the level they should apply at, picked from {SENIORITY_LEVELS}.
- core_skills: technical and professional skills shown in the resume: tools and software, and areas of
  professional know-how (the same kind of skill each job's key skills are read as, so the two can be compared).
  {SKILL_NAMING_RULE}
- certifications: professional certifications only (not degrees). Empty list if none.
- search_keywords: 4-5 job titles this candidate should search for, based on their skills and qualification.
  The job portal only finds postings that contain every word of a title, so each title is short (1-3 words)
  and as common as employers write it (e.g. "Data Analyst", "Operations Analyst", "Software Engineer"), with no
  industry or product words (write "Operations Analyst", not "Fintech Operations Analyst").
  If they have less than 1 year of full-time work, make one of them an entry-level title employers use, such as
  "Junior Analyst", "Graduate Trainee" or "Management Associate"; otherwise use no seniority words.
- Use only facts in the resume. Do not invent anything.

Return JSON only."""

    @staticmethod
    def __requirements_prompt(record: dict) -> str:
        """AI call #2+: a batch of listings -> each one's requirements, compared with the candidate."""
        # Number each job (### JOB 0, ### JOB 1, ...) so the AI can say which answer is for which job
        job_blocks = []
        for i, job in enumerate(record["jobs"]):
            job_blocks.append(
                f"### JOB {i}\n"
                f"Title: {job.get('title')}\n"
                f"Company: {job.get('company')}\n"
                f"Minimum years of experience: {job.get('min_years_experience')}\n"
                f"Listed skills: {', '.join(job.get('listed_skills', []))}\n"
                f"Flexible work arrangements: {', '.join(job.get('flexible_work_arrangements', [])) or 'None'}\n"
                f"Description: {job.get('description', '')}"
            )
        jobs_text = "\n\n".join(job_blocks)
        candidate = record["candidate"]
        return f"""Extract the requirements of each job posting below and compare them with the candidate.
Return one entry in "jobs" per JOB, with job_index set to that JOB's number.

{jobs_text}

CANDIDATE:
Highest completed qualification: {candidate["qualification_detail"]} ({candidate["highest_qualification"]})
Full-time experience: {candidate["years_of_experience"]} years
Internships: {candidate["internship_months"]} months
Certifications: {", ".join(candidate["certifications"]) or "None"}

CANDIDATE SKILLS:
{", ".join(candidate["core_skills"])}

RULES (apply to each job separately):
- required_skills: the 4-5 most important technical and professional skills this job asks for, read from
  the WHOLE posting (description and listed skills) the same way a resume's skills are read: tools and
  software (e.g. SQL, Python, Power BI, Microsoft Excel, SAP) and areas of professional know-how (e.g.
  Financial Analysis, Data Analysis, Reconciliation, KYC, Regulatory Compliance, Risk Management). The same
  holds in any field: a lab job's skills read like HPLC, PCR or ISO 13485; an engineering job's like AutoCAD.
  Most important first: what the job needs to be done before what is "an advantage" or "a plus".
  Name each one separately ("SQL and Python" is two skills) and as the candidate's resume would write it,
  so the two lists can be compared. {SKILL_NAMING_RULE} Take CANDIDATE SKILLS above as the guide to which of this posting's
  skills belong to the candidate's field. Never add a skill the posting does not ask for.
  Leave out workplace behaviours and personal qualities, however the posting words them: "Teamwork",
  "Team Player", "Problem Solving", "Time Management", "Good Communication", "Attention to Detail",
  "Work in a Fast Paced Environment", "Willingness to Learn".
- min_education: the minimum qualification stated, picked from {JOB_EDUCATION_LEVELS}.
  "Degree" means "Bachelor's". Use "Not specified" if the posting does not say.
- certifications_required: certifications the posting asks for. Empty list if none.
- work_arrangement: "Remote" or "Hybrid" only if the posting says so (Telecommuting suggests Hybrid);
  "Onsite" if it names an office/site; otherwise "Not specified".
- skill_matches: for each required skill the candidate covers, give the job_skill (copied from
  required_skills) and the candidate_skill (copied EXACTLY from CANDIDATE SKILLS) that covers it.
  Close equivalents count (e.g. "React" covers "frontend frameworks", "PostgreSQL" covers "SQL").
  Be strict: a different language or tool is NOT a match (e.g. "Python" does not cover "C++"),
  and leave out required skills with no clear candidate skill behind them.
- suitability_reason: 1-2 sentences (under 40 words) stating how the candidate's skills, experience and
  qualification compare with this job's requirements, naming the key matches and gaps.
  State facts only; do not say whether they should apply.
- Use only facts in the posting and candidate details. Do not invent anything.

Return JSON only."""

    @staticmethod
    def __tailored_resume_prompt(record: dict) -> str:
        """Tailoring a PDF resume: the whole resume rewritten for one job."""
        job = record["job"]
        # The skills the job card counted as matched, so the tailored resume keeps the same count
        matched_skills = "\n".join(f'- {match["job_skill"]} (the candidate\'s "{match["candidate_skill"]}")'
                                   for match in job.get("matched_skills", [])) or "None"
        # A second try says what was wrong with the first, last, where a small model follows it best
        retry = "" if not record.get("feedback") else (
            "\nYOUR LAST ANSWER BROKE RULE 6 HERE, so these changes were thrown out. Write them again without the "
            "problem, or leave them as the resume has them, and keep your other changes:\n"
            + "\n".join(f"- {line}" for line in record["feedback"]) + "\n")
        return f"""Rewrite this candidate's resume so it is tailored to the job below.

JOB:
Title: {job.get('title')}
Company: {job.get('company')}
Key skills: {', '.join(job.get('required_skills', [])) or 'Not known'}
Listed skills: {', '.join(job.get('listed_skills', [])) or 'None'}
Description: {job.get('description') or 'Not available'}

MATCHED SKILLS (job skills the candidate already has, and the resume skill that shows each):
{matched_skills}

RESUME:
{record["resume_text"]}

RULES:
1. Layout: the resume's own sections, in the same order, with the same headings (keep "PROFILE", never
   "Summary"). Copy exactly: name, contact line (with its separators, e.g. "Singapore | +65 9000 0000"; empty
   if none), and each entry's title, organisation, subtitle and dates. At most about
   {len(record["resume_text"].split())} words in total, so it keeps its pages.
2. What to change: aim a "text" section such as a profile at this job. In "skills" sections keep the resume's
   groups, labels and skill names, putting the most relevant first (drop some only if there are over 15).
   In "entries" sections keep every entry in order; a bullet starts with an action verb and puts what matters
   for this job first, and you may shorten or drop bullets that don't help. Write job words in sentence case.
3. Keep every MATCHED SKILL visible (in a skills group, a text section or a bullet).
4. changes: 3-6 short notes on what you changed and why ("Moved SQL to the front because the job asks for it").
5. skill_evidence: one item per MATCHED SKILL: job_skill copied from MATCHED SKILLS, and evidence, a 2-12 word
   phrase copied character for character from your tailored resume. Empty list if none.
6. Before you answer, check what you wrote and undo any change that fails: a bullet names only the tools its
   own resume bullet names; nothing gets a number, qualification, employer or date the resume doesn't give it;
   and the candidate's own words for how skilled they are stay (keep "Comfortable with").
{retry}
Return JSON only."""

    @staticmethod
    def __docx_edits_prompt(record: dict) -> str:
        """Tailoring a Word resume: new wording for some of its paragraphs."""
        job = record["job"]
        matched_skills = "\n".join(f'- {match["job_skill"]} (the candidate\'s "{match["candidate_skill"]}")'
                                   for match in job.get("matched_skills", [])) or "None"
        # A second try says what was wrong with the first, last, where a small model follows it best
        retry = "" if not record.get("feedback") else (
            "\nYOUR LAST ANSWER BROKE RULE 6 HERE, so these edits were thrown out. Write them again without the "
            "problem, or leave those paragraphs out, and keep your other edits:\n"
            + "\n".join(f"- {line}" for line in record["feedback"]) + "\n")
        # Paragraphs that may be reworded get a [number]; the rest are shown only so the AI has context. Each numbered
        # one also lists the job keywords it already names: a small model kept adding a keyword the resume shows
        # elsewhere (Tableau into a Power BI bullet) until each paragraph said which ones are its own
        # The tools it looks for: the job keywords the resume shows, and the resume's own skills lists
        # (e.g. "Technical: Python, pandas, SQL, Power BI"), so a Power BI bullet says "Power BI" even for a job
        # that doesn't ask for it
        tools = list(record["use_keywords"])
        for paragraph in record["paragraphs"]:
            label, _, items = paragraph["text"].partition(":")
            if items.count(",") >= 2 and len(label.split()) <= 4:
                tools += [re.sub(r"\(.*?\)", "", item).strip() for item in items.split(",")]
        tools = [tool for tool in dict.fromkeys(tools) if 1 < len(tool) <= 30]
        lines = []
        for paragraph in record["paragraphs"]:
            if paragraph["editable"]:
                words = len(paragraph["text"].split())
                names = [tool for tool in tools if re.search(
                    rf"(?<![\w+#]){re.escape(tool)}(?![\w+#])", paragraph["text"], re.IGNORECASE)]
                lines.append(f"[{paragraph['id']}] ({paragraph['kind']}, {words} words, names: "
                             f"{', '.join(names) or 'none'}) {paragraph['text']}")
            else:
                lines.append(f"    {paragraph['text']}")
        resume_lines = "\n".join(lines)
        return f"""Tailor this candidate's resume to the job below by rewording some of its paragraphs.
The resume's layout, headings, section order, titles, dates and skills lists stay exactly as they are:
you can only change the paragraphs marked with a [number].

JOB:
Title: {job.get('title')}
Company: {job.get('company')}
Description: {job.get('description') or 'Not available'}

JOB KEYWORDS THE RESUME SHOWS (use one only in a paragraph whose "names" list has it):
{', '.join(record["use_keywords"]) or 'None'}

JOB KEYWORDS THE RESUME DOES NOT SHOW (never use these words):
{', '.join(record["avoid_keywords"]) or 'None'}

MATCHED SKILLS (job skills the candidate already has, and the resume skill that shows each):
{matched_skills}

RESUME (one paragraph per line; only [numbered] ones can be changed):
{resume_lines}

RULES:
1. What to change: reword freely so each paragraph fits this job, keeping every fact as the resume states it.
   Aim a (text) paragraph such as a profile at this job. A (bullet) starts with an action verb and puts what
   matters for this job first. Leave lists of names (modules, certifications, courses, awards) as they are.
   Write job words in sentence case ("collaborating with stakeholders").
2. Length: at most the number of words shown for each paragraph, so the resume keeps its pages.
3. Dropping: give a bullet that clearly doesn't help an empty text. Drop only a few, never every bullet under
   a heading, and never the only place a MATCHED SKILL shows.
4. edits: only the paragraphs you change, each with its [number], new wording and a reason under 15 words
   (e.g. "leads with SQL, which the job asks for" or "not relevant to a data role").
5. skill_evidence: one item per MATCHED SKILL: job_skill copied from MATCHED SKILLS, and evidence, a 2-12 word
   phrase copied character for character from the resume as it will read after your edits. Empty list if none.
6. Before you answer, check each edit and drop any that fails: a bullet names only the tools in its own
   "names" list; nothing gets a number, qualification, employer or date its original doesn't have (a profile may
   use any from the resume); and the candidate's own words for how skilled they are stay (keep "Comfortable with").
{retry}
Return JSON only."""

    # ---------- public calls for the other managers ----------

    @staticmethod
    def __capped_resume(resume_text: str) -> str:
        """The resume text, cut to MAX_RESUME_CHARS so a very long file can't overflow the AI's input limit."""
        if len(resume_text) > MAX_RESUME_CHARS:
            logging.warning(f"Resume text is {len(resume_text)} characters; using the first {MAX_RESUME_CHARS}.")
        return resume_text[:MAX_RESUME_CHARS]

    def extract_candidate_profile(self, resume_text: str) -> dict | None:
        """AI call: the resume -> a validated candidate record (see CANDIDATE_SCHEMA), or None."""
        return self.run_task("candidate_profile", {"resume_text": self.__capped_resume(resume_text)})

    def __requirements_for_batch(self, jobs: list[dict], profile: dict) -> list[dict | None]:
        """AI call for one batch of listings. Returns one validated answer per listing, None where the AI
        gave none."""
        answers: list[dict | None] = [None] * len(jobs)
        result = self.run_task("job_requirements", {"jobs": jobs, "candidate": profile})
        if result is None:
            logging.warning(f"Could not extract requirements for a batch of {len(jobs)} jobs.")
            return answers

        # Match each AI answer to its job by job_index (the "### JOB n" number in the prompt)
        for requirements in result["jobs"]:
            job_index = requirements.pop("job_index")
            if job_index < len(jobs) and answers[job_index] is None:
                answers[job_index] = requirements
        for job_index, job in enumerate(jobs):
            if answers[job_index] is None:
                logging.warning(f"No requirements returned for: {job.get('title')}")
        return answers

    def extract_requirements_for_listings(self, listings: list[dict], profile: dict, max_workers: int = 5,
                                          on_progress: Callable[[str, int, int], None] | None = None,
                                          summary: dict | None = None) -> list[dict | None]:
        """AI calls for every listing, in parallel batches. Returns one answer per listing, in the same order
        (None where the AI gave none); LogicManager.add_requirements turns them into job records."""
        if summary is None:
            summary = {}

        # Split into batches of 10, e.g. 40 jobs -> [jobs 0-9, 10-19, 20-29, 30-39]
        batches = [listings[start:start + JOB_BATCH_SIZE] for start in range(0, len(listings), JOB_BATCH_SIZE)]
        summary["batches_failed"] = 0
        logging.info(f"Extracting requirements for {len(listings)} unique listings in {len(batches)} batches...")

        # Send up to 5 batches at once; wait in 0.5 s slices so Ctrl+C works on Windows
        pool = ThreadPoolExecutor(max_workers=max_workers)
        try:
            futures = [pool.submit(self.__requirements_for_batch, batch, profile) for batch in batches]
            still_running = set(futures)
            done = 0
            while still_running:
                finished, still_running = wait(still_running, timeout=0.5, return_when=FIRST_COMPLETED)
                for _ in finished:
                    done += 1
                    if on_progress:
                        on_progress("analyse", done, len(batches))
            answers: list[dict | None] = []
            for batch, future in zip(batches, futures):
                try:
                    answers.extend(future.result())
                except Exception as error:
                    # An unexpected error in one batch loses only that batch's listings
                    summary["batches_failed"] += 1
                    answers.extend([None] * len(batch))
                    logging.error(f"A batch of {len(batch)} listings failed ({type(error).__name__}: {error}); "
                                  "skipping it.")
        except BaseException:
            # Ctrl+C or a cancelled search: don't start the queued batches, and don't wait for the running ones
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        pool.shutdown(wait=True)
        summary["listings_analysed"] = sum(answer is not None for answer in answers)
        return answers

    def tailor_resume(self, resume_text: str, job: dict, feedback: list[str] | None = None) -> dict | None:
        """AI call: a PDF resume rewritten for one job. Returns the validated reply (name, contact, sections,
        changes, skill_evidence), or None. TailoringGuard.ground_tailored_resume checks it against the original.
        feedback: on a second try, why changes from the first were thrown out, e.g. ['In "Profile": ...']."""
        return self.run_task("tailored_resume", {"resume_text": self.__capped_resume(resume_text), "job": job,
                                                 "feedback": feedback or []})

    def tailor_docx(self, paragraphs: list[dict], job: dict, keywords: tuple[list[str], list[str]],
                    feedback: list[str] | None = None) -> dict | None:
        """AI call: new wording for some paragraphs of a Word resume. keywords = (use, avoid), what
        LogicManager.job_keywords returns. Returns the validated reply (edits, skill_evidence) plus the paragraphs
        it was sent, or None. TailoringGuard.ground_docx_edits checks it. feedback: as for tailor_resume."""
        use_keywords, avoid_keywords = keywords
        # Like _capped_resume: stop adding paragraphs once MAX_RESUME_CHARS is reached
        capped, length = [], 0
        for paragraph in paragraphs:
            length += len(paragraph["text"]) + 1
            if length > MAX_RESUME_CHARS:
                logging.warning(f"Resume has more than {MAX_RESUME_CHARS} characters; "
                                "using the paragraphs before that.")
                break
            capped.append(paragraph)

        result = self.run_task("docx_edits", {"paragraphs": capped, "job": job, "use_keywords": use_keywords,
                                                 "avoid_keywords": avoid_keywords, "feedback": feedback or []})
        if result is None:
            return None
        return {**result, "paragraphs": capped}
