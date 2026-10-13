"""AI Manager: API interaction and response validation only.

Called by main.py and web_app.py: extract_candidate_profile, extract_requirements_for_listings,
tailor_resume and tailor_docx. Every AI call goes through run_task:
build_prompt -> call_api -> parse_response -> validate_response.
"""

# --- Standard library (comes with Python) ---
import json                                      # convert between JSON text and Python dicts
import logging                                   # print INFO/WARNING/ERROR messages
import os                                        # read environment variables, file paths
import re                                        # regular expressions (text pattern matching)
import time                                      # sleep() while waiting to retry
from collections.abc import Callable             # type hints for functions passed as arguments
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait  # run several AI calls at the same time
from datetime import date                        # today's date, so the AI can tell finished from ongoing studies
from typing import Any, cast                     # type hints: any JSON value; a value whose type is known
from pathlib import Path                         # nicer file path handling

# --- Installed packages (see requirements.txt) ---
import anthropic                  # official SDK for Claude
import openai                     # official SDK for OpenAI (also works for Qwen and Gemini)
from dotenv import load_dotenv    # loads the .env file into environment variables

# Logging is set up by the program that runs (main.py, web_app.py), not by this layer

# Load .env from the project root (parent of src/), so os.getenv("QWEN_API_KEY") etc. work.
# __file__ is this file's path; .parent.parent goes up from src/ai_manager.py to the project root.
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

# Settings for each AI provider (AI_PROVIDER and AI_MODEL in .env pick one).
# temperature None leaves the parameter out; 0.0 keeps answers consistent between runs.
PROVIDERS = {
    "qwen": {
        "label": "Alibaba Qwen",          # name shown to users, e.g. in the privacy notice
        "sdk": "openai",                  # which client library to use
        "key_env": "QWEN_API_KEY",        # name of the .env variable holding the API key
        "base_url": "https://ws-jqxl73dgs75w9q92.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-flash",
        "temperature": 0.0,
    },
    "gemini": {
        "label": "Google Gemini",
        "sdk": "openai",
        "key_env": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-3.1-flash-lite",
        "temperature": 0.0,
    },
    "openai": {
        "label": "OpenAI",
        "sdk": "openai",
        "key_env": "OPENAI_API_KEY",
        "base_url": None,                 # None = OpenAI's own servers
        "default_model": "gpt-5.5",
        "temperature": None,
    },
    "anthropic": {
        "label": "Anthropic Claude",
        "sdk": "anthropic",
        "key_env": "ANTHROPIC_API_KEY",
        "default_model": "claude-opus-5-5",
    },
}
DEFAULT_PROVIDER = "gemini"

# The provider and model currently in use. These are module-level ("global") variables:
# every function in this file reads them, and set_provider() changes them.
PROVIDER = DEFAULT_PROVIDER
MODEL = PROVIDERS[PROVIDER]["default_model"]

# --- Tunable settings ---
JOB_BATCH_SIZE = 10            # jobs sent to the AI per call
RATE_LIMIT_RETRIES = 3         # retries per AI call when the provider is busy
MALFORMED_RESPONSE_RETRIES = 1 # extra tries when a reply isn't valid JSON or fails the schema check
API_TIMEOUT_SECONDS = 90       # longest wait for one AI reply before it counts as a failed try
MAX_RESUME_CHARS = 20000       # longer resume text is cut, so a huge file can't overflow the AI's input limit
MAX_RATE_LIMIT_WAIT = 90  # longer suggested waits usually mean the daily quota is used up

# The API client object. Starts empty and is created the first time it's needed (_get_client).
# The leading underscore is a Python convention meaning "internal, don't use from other files".
_client = None


def set_provider(provider: str, model: str | None = None) -> None:
    """Switches the AI provider (and optionally model) used by all later calls."""
    # "global" lets this function change the module-level variables instead of making new local ones
    global PROVIDER, MODEL, _client
    provider = provider.strip().lower()
    if provider not in PROVIDERS:
        logging.warning(f"Unknown AI provider '{provider}', using {DEFAULT_PROVIDER}.")
        provider = DEFAULT_PROVIDER
    PROVIDER = provider
    # "model or ..." means: use the given model if there is one, otherwise the provider's default
    MODEL = model or PROVIDERS[provider]["default_model"]
    # Throw away the old client so the next call creates one for the new provider
    _client = None


def provider_name() -> str:
    """The AI service in use, as people know it, e.g. "Google Gemini" (for the privacy notice)."""
    return PROVIDERS[PROVIDER]["label"]


# Runs once when this file is loaded: pick the provider/model from .env (or the defaults)
set_provider(os.getenv("AI_PROVIDER", DEFAULT_PROVIDER), os.getenv("AI_MODEL"))


def _get_client() -> openai.OpenAI | anthropic.Anthropic:
    """Creates the API client on first use so a missing key is reported, not crashed on at import."""
    global _client
    if _client is None:
        config = PROVIDERS[PROVIDER]
        api_key = os.getenv(config["key_env"])
        if not api_key:
            raise RuntimeError(f"{config['key_env']} is not set (add it to .env or the environment)")
        # Retries are handled in call_api so each one is visible and counts once against quota
        # (max_retries=0 turns off the SDK's own hidden retries)
        if config["sdk"] == "anthropic":
            _client = anthropic.Anthropic(api_key=api_key, timeout=API_TIMEOUT_SECONDS, max_retries=0)
        else:
            _client = openai.OpenAI(api_key=api_key, base_url=config["base_url"],
                                    timeout=API_TIMEOUT_SECONDS, max_retries=0)
        logging.info(f"Using {PROVIDER} ({MODEL})")
    return _client


# ==========================================
# 1. RECORD SCHEMAS
# A "schema" describes exactly what JSON we want back from the AI: which keys,
# what type each value is, and which values are allowed. Each schema is used twice:
#   - sent to the AI so it must reply in exactly this shape (structured output)
#   - used by validate_response to double-check the reply
# ==========================================

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
            "description": "3-4 job titles to search for, without seniority words, e.g. 'Software Engineer'",
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
# Checked afterwards (logic_manager.skills_shown): each quote must really be in the tailored resume
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


# ==========================================
# 2. PROMPT BUILDING  (step 1 of every AI call)
# Writes the instructions sent to the AI. The RULES sections are where most
# accuracy fixes live, e.g. "Internships do NOT count" or "Python does not cover C++".
# ==========================================

def _candidate_profile_prompt(record: dict) -> str:
    """AI call #1: the resume -> the candidate's profile. record = {"resume_text": str}"""
    # f"""...""" is a multi-line f-string: {...} parts are replaced with values
    return f"""Extract the candidate's details from this resume.

RESUME:
{record["resume_text"]}

TODAY'S DATE: {date.today().isoformat()}

RULES:
- highest_qualification: the highest COMPLETED qualification, picked from {EDUCATION_LEVELS}.
  Qualifications still in progress, "expected", or ending after today's date do not count.
  One that ended on or before today's date counts as completed.
- qualification_detail: full name of that completed qualification.
- years_of_experience: full-time work only, in years (decimals allowed). Internships do NOT count. 0 if none.
- internship_months: total months of internships or attachments. 0 if none.
- seniority_level: the level they should apply at, picked from {SENIORITY_LEVELS}.
- core_skills: technical and professional skills shown in the resume.
- certifications: professional certifications only (not degrees). Empty list if none.
- search_keywords: 3-4 common job titles this candidate should search for, without seniority words
  (e.g. "Software Engineer", "Data Analyst"), based on their skills and qualification.
- Use only facts in the resume. Do not invent anything.

Return JSON only."""


def _job_requirements_prompt(record: dict) -> str:
    """AI call #2+: a batch of listings -> each one's requirements, compared with the candidate.
    record = {"jobs": list[dict], "candidate": dict (candidate record)}"""
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
- required_skills: the 5-10 most important skills the job asks for, from the description and listed skills.
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


def _tailored_resume_prompt(record: dict) -> str:
    """Tailoring a PDF resume: rewrite it for one job.
    record = {"resume_text": str, "job": dict (job record, with description if known)}"""
    job = record["job"]
    # The skills the job card counted as matched, so the tailored resume keeps the same count
    matched_skills = "\n".join(f'- {match["job_skill"]} (the candidate\'s "{match["candidate_skill"]}")'
                               for match in job.get("matched_skills", [])) or "None"
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
- Use ONLY facts in the resume. Never add a skill, tool, employer, job title, qualification, date,
  number or achievement that is not in the resume, even if the job asks for it. Leave gaps out.
- Keep the resume's own layout: the same sections, in the same order, with the same headings
  (e.g. keep "PROFILE"; do not rename it "Summary"). Do not add, merge, split or move sections.
- Keep it the same length: the resume has about {len(record["resume_text"].split())} words, and the tailored one
  must not have more, so it fits on the same number of pages.
- Write job keywords in normal sentence case inside a sentence, e.g. "collaborating with stakeholders",
  not "Collaboration With Stakeholders".
- name and contact: copied exactly from the resume, including the contact line's separators
  (e.g. "Singapore | +65 9000 0000 | name@example.com"). Empty string if the resume has none.
- kind "text" sections (e.g. a profile or summary): rewrite the paragraph to aim at this job,
  about as long as the original, using only facts from the resume. No numbers that are not in it.
- kind "skills" sections: keep the resume's own groups and labels (e.g. "Technical"), in the same order.
  Copy skill names exactly as written; within each group, put the most relevant to this job first.
  Only if there are more than 15 skills in total, leave out ones that do not help with this job.
- kind "entries" sections (e.g. experience, education, projects, certifications): keep every entry,
  in the resume's order. Copy title, organisation, subtitle and dates exactly as written.
  bullets: rewrite to show what matters for this job: start with an action verb, put the most relevant
  first, use the job's key skill words only where the resume shows that skill, and keep any numbers exactly.
  You may shorten or drop bullets that do not help with this job.
- changes: 3-6 short notes on what you changed and why,
  e.g. "Moved SQL to the front of the Technical skills because the job asks for it".
- Keep every MATCHED SKILL visible in the tailored resume (in a skills group, a text section or a bullet).
- skill_evidence: one item per MATCHED SKILL. job_skill is copied exactly from MATCHED SKILLS.
  evidence is a short phrase (2-12 words) copied exactly, character for character, from your
  tailored resume (a skill name, or part of a text section or a bullet) that shows that skill.
  Do not add items for skills that are not in MATCHED SKILLS. Empty list if there are none.

Return JSON only."""


def _docx_edits_prompt(record: dict) -> str:
    """Tailoring a Word resume: new wording for some of its paragraphs.
    record = {"paragraphs": list[dict] (io_manager.read_docx_paragraphs), "job": dict,
              "use_keywords": list[str], "avoid_keywords": list[str] (logic_manager.job_keywords)}"""
    job = record["job"]
    matched_skills = "\n".join(f'- {match["job_skill"]} (the candidate\'s "{match["candidate_skill"]}")'
                               for match in job.get("matched_skills", [])) or "None"
    # Paragraphs that may be reworded get a [number]; the rest are shown only so the AI has context
    lines = []
    for paragraph in record["paragraphs"]:
        if paragraph["editable"]:
            words = len(paragraph["text"].split())
            lines.append(f"[{paragraph['id']}] ({paragraph['kind']}, {words} words) {paragraph['text']}")
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

JOB KEYWORDS THE RESUME SHOWS (use these words where they describe what the resume shows):
{', '.join(record["use_keywords"]) or 'None'}

JOB KEYWORDS THE RESUME DOES NOT SHOW (never use these words):
{', '.join(record["avoid_keywords"]) or 'None'}

MATCHED SKILLS (job skills the candidate already has, and the resume skill that shows each):
{matched_skills}

RESUME (one paragraph per line; only [numbered] ones can be changed):
{resume_lines}

RULES:
- Use ONLY facts in the resume. Never add a skill, tool, employer, job title, qualification, date
  or achievement that is not in the resume, even if the job asks for it.
- Never add a number, duration or count (e.g. "2 years of experience") that is not in the resume.
- edits: only the paragraphs you change, each with its [number] and its new wording.
  Leave out paragraphs that are already a good fit. Paragraphs cannot be moved or added.
- Keep the resume the same length: use at most the number of words shown for each paragraph, and
  fewer if you can. A longer resume can spill onto another page.
- Write job keywords in normal sentence case inside a sentence, e.g. "collaborating with stakeholders",
  not "Collaboration With Stakeholders".
- (text) paragraphs such as a profile: aim them at this job, using only facts from the resume.
- (bullet) paragraphs: start with an action verb, put what matters for this job first, describe the work
  with the job's words from the first keyword list where they fit, and keep any numbers exactly.
- Never change a list of names, e.g. "Relevant modules: ...", certifications, courses or awards:
  adding a keyword there would claim something the candidate never did. Leave those paragraphs out.
- Remove only the few bullets that clearly do not help with this job, by giving them an empty text.
  Most bullets should be kept or reworded, and never remove every bullet under a heading.
- reason: for each edit, why you made it, in under 15 words,
  e.g. "leads with SQL, which the job asks for" or "not relevant to a data role".
- Keep every MATCHED SKILL visible: don't remove or reword away the only place that shows one.
- skill_evidence: one item per MATCHED SKILL. job_skill is copied exactly from MATCHED SKILLS.
  evidence is a short phrase (2-12 words) copied exactly, character for character, from the resume
  as it will read after your edits (a paragraph you changed, or one you left as it is) that shows
  that skill. Do not add items for skills that are not in MATCHED SKILLS. Empty list if there are none.

Return JSON only."""


# Which function writes the prompt for each kind of AI call (the keys of TASKS)
PROMPT_BUILDERS = {
    "candidate_profile": _candidate_profile_prompt,
    "job_requirements": _job_requirements_prompt,
    "tailored_resume": _tailored_resume_prompt,
    "docx_edits": _docx_edits_prompt,
}


def build_prompt(record: dict, task: str) -> str:
    """Constructs the prompt for a task from its input record."""
    if task not in PROMPT_BUILDERS:
        raise ValueError(f"Unknown task: {task}")
    return PROMPT_BUILDERS[task](record)


# ==========================================
# 3. API CALL  (step 2 of every AI call)
# Sends the prompt to the chosen provider and returns the reply as text.
# Handles temporary failures (busy servers, rate limits) by waiting and retrying.
# ==========================================

# 429 = rate limit/quota, 5xx/529 = provider overloaded, connection error/timeout:
# all usually temporary, so call_api waits and retries.
# These are tuples of exception classes, so "except RETRYABLE_ERRORS" catches any of them.
RETRYABLE_ERRORS = (
    openai.RateLimitError, openai.InternalServerError, openai.APIConnectionError,
    anthropic.RateLimitError, anthropic.InternalServerError, anthropic.OverloadedError,
    anthropic.APIConnectionError,
)
# Any other HTTP error (e.g. 400 bad request, 401 wrong key) - not worth retrying
STATUS_ERRORS = (openai.APIStatusError, anthropic.APIStatusError)

# A "sentinel": a unique object used as a special signal. _with_retries returns it to mean
# "the provider rejected this request format", which is different from None ("it failed").
_FORMAT_REJECTED = object()  # sentinel: provider rejected the response format (HTTP 400)


def call_api(prompt: str, task: str) -> str | None:
    """Sends the prompt and returns the raw response text, or None on failure.

    OpenAI-compatible providers: tries strict JSON Schema mode first. If the endpoint
    rejects it (HTTP 400), retries in JSON Object mode with the schema written into the prompt.
    Anthropic: uses structured outputs (output_config.format) through the Anthropic SDK.
    """
    try:
        client = _get_client()
    except RuntimeError as e:
        logging.error(f"[{task}] {e}")
        return None

    # "attempts" is a list of functions to try in order. "lambda: ..." creates a small
    # function without running it yet; _with_retries calls it (possibly several times).
    if PROVIDERS[PROVIDER]["sdk"] == "anthropic":
        attempts = [lambda: _anthropic_request(client, prompt, task)]
    else:
        schema = TASKS[task]
        # Attempt 1: strict mode - the provider guarantees the reply matches the schema
        schema_mode = {
            "type": "json_schema",
            "json_schema": {"name": task, "strict": True, "schema": schema},
        }
        # Attempt 2 (only if attempt 1 is rejected): plain JSON mode, with the schema
        # pasted into the prompt so the AI still knows the shape we want
        fallback_prompt = (
            prompt + f"\n\nReturn ONLY a single JSON object matching this structure:\n{json.dumps(schema, indent=2)}"
        )
        attempts = [
            lambda: _openai_request(client, prompt, schema_mode),
            lambda: _openai_request(client, fallback_prompt, {"type": "json_object"}),
        ]

    for attempt in attempts:
        result = _with_retries(attempt, task)
        if result is not _FORMAT_REJECTED:
            return cast(str | None, result)  # either the reply text, or None if it failed for another reason

    logging.error(f"[{task}] All response formats were rejected.")
    return None


def _openai_request(client: openai.OpenAI, prompt: str, response_format: dict) -> str | None:
    """One request through the OpenAI client (Qwen, Gemini, OpenAI)."""
    params = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": response_format,
    }
    temperature = PROVIDERS[PROVIDER].get("temperature")
    if temperature is not None:
        params["temperature"] = temperature
    # **params unpacks the dict into keyword arguments: model=..., messages=..., ...
    completion = client.chat.completions.create(**params)

    if not completion.choices:
        # e.g. Gemini can send no choices at all when it blocks a reply
        logging.error(f"Response from {PROVIDER} ({MODEL}) had no answer in it.")
        return None
    choice = completion.choices[0]
    if choice.finish_reason == "length":
        logging.error(f"Response from {PROVIDER} ({MODEL}) was cut off at its length limit.")
        return None
    if not choice.message.content:
        logging.error(f"Empty response from {PROVIDER} ({MODEL}); finish_reason={choice.finish_reason}")
    return choice.message.content


def _anthropic_request(client: anthropic.Anthropic, prompt: str, task: str) -> str | None:
    """One request through the Anthropic SDK, with structured outputs.

    Claude 5-generation models also get an explicit effort level and server-side refusal
    fallback (a declined request is re-run on Anthropic's recommended fallback model).
    Haiku 4.5 supports neither, so it gets a plain structured-output request.
    """
    params = {
        "model": MODEL,
        "max_tokens": 16000,  # upper limit on reply length
        "output_config": {"format": {"type": "json_schema", "schema": TASKS[task]}},
        "messages": [{"role": "user", "content": prompt}],
    }
    if MODEL.startswith("claude-haiku"):
        response = client.messages.create(**params)
    else:
        params["output_config"]["effort"] = "medium"  # how much the model thinks before answering
        response = client.beta.messages.create(
            **params, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
        )

    # stop_reason says why Claude stopped writing; only "end_turn" means a complete answer
    if response.stop_reason == "refusal":
        logging.error(f"[{task}] Claude declined the request (refusal).")
        return None
    if response.stop_reason == "max_tokens":
        logging.error(f"[{task}] Claude's response was cut off at max_tokens.")
        return None
    # The reply is a list of content blocks; return the text of the first text block
    return next((block.text for block in response.content if block.type == "text"), None)


def _with_retries(request: Callable[[], str | None], task: str) -> str | None | object:
    """Runs one request function, retrying temporary failures.

    Returns the response text, None on failure, or _FORMAT_REJECTED on HTTP 400
    so call_api can try its next response format.
    """
    for retry in range(RATE_LIMIT_RETRIES + 1):  # 1 try + up to 3 retries
        try:
            return request()
        except RETRYABLE_ERRORS as e:
            # Temporary problem: wait, then loop round and try again
            reason = f"HTTP {e.status_code}" if hasattr(e, "status_code") else "connection error/timeout"
            wait = _retry_delay(e)
            if retry == RATE_LIMIT_RETRIES or wait > MAX_RATE_LIMIT_WAIT:
                logging.error(f"[{task}] AI API still unavailable ({reason}); giving up. {_short(e)}")
                return None
            logging.warning(f"[{task}] AI API unavailable ({reason}); retrying in {wait:.0f}s...")
            time.sleep(wait)
        except STATUS_ERRORS as e:
            # Permanent problem (e.g. wrong API key): don't retry
            if e.status_code != 400:
                logging.error(f"[{task}] AI API returned HTTP {e.status_code}: {_short(e)}")
                return None
            # 400 often means "I don't support this request format" - let call_api try the next one
            logging.warning(f"[{task}] Request rejected (HTTP 400: {_short(e)}); trying fallback if any.")
            return _FORMAT_REJECTED
        except Exception as e:
            # Anything unexpected (e.g. a reply in an odd shape) fails this call only, like any other
            # failed call, so the caller can offer to try again. The traceback goes to the log at INFO
            # level, which main.py writes to its log file but not to the terminal.
            logging.error(f"[{task}] Unexpected error calling the AI API ({type(e).__name__}: {e}).")
            logging.info(f"[{task}] Traceback of the unexpected error:", exc_info=True)
            return None
    return None


def _short(error: Exception) -> str:
    """First line of an API error message, so logs stay readable."""
    message = getattr(error, "message", None) or str(error)
    match = re.search(r"'message': '([^'\\]+)", message)
    return (match.group(1) if match else message.splitlines()[0])[:200]


def _retry_delay(error: Exception) -> float:
    """Reads the suggested wait from a retry-after header or the error message
    ("retry in 29.8s"), defaulting to 30s."""
    response = getattr(error, "response", None)
    header = response.headers.get("retry-after") if response is not None else None
    if header:
        try:
            return float(header) + 1
        except ValueError:
            pass
    match = re.search(r"retry in ([\d.]+)s", str(error))
    return float(match.group(1)) + 1 if match else 30.0


# ==========================================
# 4. RESPONSE PARSING + VALIDATION  (steps 3 and 4 of every AI call)
# Never trust the AI's reply blindly: turn it into a Python dict, then check
# every field against the schema before anything else uses it.
# ==========================================

def parse_response(raw: str | None) -> dict | None:
    """Extracts a JSON object from the response text. Handles markdown code
    fences and stray text around the object."""
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
        except json.JSONDecodeError as e:
            logging.error(f"JSON parse failed ({e}). Raw response:\n{raw}")
            return None

    if not isinstance(data, dict):
        logging.error(f"Expected a JSON object, got {type(data).__name__}.")
        return None
    return data


def _validate_value(value: Any, spec: dict, name: str, path: str) -> Any:
    """Validates one value against its schema (recursively) and returns the cleaned value; raises ValueError
    for the first problem. path says where it is, e.g. "job_requirements.jobs[3].min_education"."""
    expected = spec["type"]
    if expected == "string":
        return _validate_string(value, spec, path)
    if expected in ("integer", "number"):
        return _validate_number(value, expected, name, path)
    if expected == "array":
        return _validate_list(value, spec, name, path)
    if expected == "object":
        return _validate_object(value, spec, path)
    raise ValueError(f"{path} has unsupported schema type {expected!r}")


def _validate_string(value: Any, spec: dict, path: str) -> str:
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


def _validate_number(value: Any, expected: str, name: str, path: str) -> int | float:
    """A number (a whole one for "integer"), inside its NUMERIC_RANGES range if it has one."""
    # bool is checked separately because in Python True/False count as integers
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} should be a number, got {value!r}")
    if expected == "integer":
        if value != int(value):
            raise ValueError(f"{path} should be a whole number, got {value!r}")
        value = int(value)
    if name in NUMERIC_RANGES:
        low, high = NUMERIC_RANGES[name]
        if not low <= value <= high:
            raise ValueError(f"{path}={value} is outside {low}-{high}")
    return value


def _validate_list(value: Any, spec: dict, name: str, path: str) -> list:
    """A list, each item checked; a bad record is dropped on its own, empty strings are dropped."""
    if not isinstance(value, list):
        raise ValueError(f"{path} should be a list, got {value!r}")
    item_spec = spec["items"]
    cleaned = []
    for index, item in enumerate(value):
        try:
            cleaned.append(_validate_value(item, item_spec, name, f"{path}[{index}]"))
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


def _validate_object(value: Any, spec: dict, path: str) -> dict:
    """An object with every required key, each value checked; keys the AI added are dropped."""
    if not isinstance(value, dict):
        raise ValueError(f"{path} should be an object, got {value!r}")
    cleaned = {}
    for key in spec["required"]:
        if key not in value:
            raise ValueError(f"{path}.{key} is missing")
        cleaned[key] = _validate_value(value[key], spec["properties"][key], key, f"{path}.{key}")
    return cleaned


def validate_response(data: dict, task: str) -> dict | None:
    """Checks required keys, types, enum values and ranges against the task's schema.

    Returns a cleaned copy (unknown keys dropped, strings trimmed, enums normalised),
    or None if anything is malformed. Malformed entries inside lists of objects are
    dropped individually.
    """
    try:
        return _validate_value(data, TASKS[task], task, task)
    except ValueError as e:
        logging.error(f"[{task}] Invalid response: {e}")
        return None


def run_task(record: dict, task: str) -> dict | None:
    """build_prompt -> call_api -> parse_response -> validate_response.

    A reply that isn't valid JSON or fails the schema check is asked for again
    (MALFORMED_RESPONSE_RETRIES times). A failed API call is not: call_api has already retried it.
    """
    prompt = build_prompt(record, task)
    for attempt in range(1, MALFORMED_RESPONSE_RETRIES + 2):
        raw = call_api(prompt, task)
        if raw is None:
            return None
        # Each step returns None on failure
        data = parse_response(raw)
        result = validate_response(data, task) if data is not None else None
        if result is not None:
            return result
        if attempt <= MALFORMED_RESPONSE_RETRIES:
            logging.warning(f"[{task}] Malformed response; asking again ({attempt} of {MALFORMED_RESPONSE_RETRIES}).")
    return None


# ==========================================
# 5. PUBLIC FUNCTIONS FOR OTHER MODULES
# These tie sections 1-4 together. Other files (main.py, and the scripts in
# src/ai_manager_test/) should call these rather than the lower-level functions above.
# main.py and web_app.py need extract_candidate_profile and extract_requirements_for_listings.
# ==========================================

def _capped_resume(resume_text: str) -> str:
    """The resume text, cut to MAX_RESUME_CHARS so a very long file can't overflow the AI's input limit."""
    if len(resume_text) > MAX_RESUME_CHARS:
        logging.warning(f"Resume text is {len(resume_text)} characters; using the first {MAX_RESUME_CHARS}.")
    return resume_text[:MAX_RESUME_CHARS]


def extract_candidate_profile(resume_text: str) -> dict | None:
    """AI call: the resume -> a validated candidate record (see CANDIDATE_SCHEMA), or None."""
    return run_task({"resume_text": _capped_resume(resume_text)}, "candidate_profile")


def extract_job_requirements(jobs: list[dict], profile: dict) -> list[dict | None]:
    """AI call for one batch of listings. Returns one validated answer per listing, None where the AI gave none."""
    answers: list[dict | None] = [None] * len(jobs)
    result = run_task({"jobs": jobs, "candidate": profile}, "job_requirements")
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


def extract_requirements_for_listings(listings: list[dict], profile: dict, max_workers: int = 5,
                                      on_progress: Callable[[str, int, int], None] | None = None,
                                      summary: dict | None = None) -> list[dict | None]:
    """AI calls for every listing, in parallel batches. Returns one answer per listing, in the same order
    (None where the AI gave none); logic_manager.add_requirements turns them into job records."""
    if summary is None:
        summary = {}

    # Split into batches of 10, e.g. 40 jobs -> [jobs 0-9, 10-19, 20-29, 30-39]
    batches = [listings[start:start + JOB_BATCH_SIZE] for start in range(0, len(listings), JOB_BATCH_SIZE)]
    summary["batches_failed"] = 0
    logging.info(f"Extracting requirements for {len(listings)} unique listings in {len(batches)} batches...")

    # Send up to 5 batches at once; wait in 0.5 s slices so Ctrl+C works on Windows
    pool = ThreadPoolExecutor(max_workers=max_workers)
    try:
        futures = [pool.submit(extract_job_requirements, batch, profile) for batch in batches]
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
                logging.error(f"A batch of {len(batch)} listings failed ({type(error).__name__}: {error}); skipping it.")
    except BaseException:
        # Ctrl+C or a cancelled search: don't start the queued batches, and don't wait for the running ones
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    summary["listings_analysed"] = sum(answer is not None for answer in answers)
    return answers


def tailor_resume(resume_text: str, job: dict) -> dict | None:
    """AI call: a PDF resume rewritten for one job. Returns the validated reply (name, contact, sections,
    changes, skill_evidence), or None. logic_manager.ground_tailored_resume checks it against the original."""
    return run_task({"resume_text": _capped_resume(resume_text), "job": job}, "tailored_resume")


def tailor_docx(paragraphs: list[dict], job: dict, use_keywords: list[str],
                avoid_keywords: list[str]) -> dict | None:
    """AI call: new wording for some paragraphs of a Word resume. Returns the validated reply (edits,
    skill_evidence) plus the paragraphs it was sent, or None. logic_manager.ground_docx_edits checks it."""
    # Like _capped_resume: stop adding paragraphs once MAX_RESUME_CHARS is reached
    capped, length = [], 0
    for paragraph in paragraphs:
        length += len(paragraph["text"]) + 1
        if length > MAX_RESUME_CHARS:
            logging.warning(f"Resume has more than {MAX_RESUME_CHARS} characters; using the paragraphs before that.")
            break
        capped.append(paragraph)

    result = run_task({"paragraphs": capped, "job": job, "use_keywords": use_keywords,
                       "avoid_keywords": avoid_keywords}, "docx_edits")
    if result is None:
        return None
    return {**result, "paragraphs": capped}
