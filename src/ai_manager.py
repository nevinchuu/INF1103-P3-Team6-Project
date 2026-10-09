"""AI Manager: API interaction and response validation only.

Turns unstructured text (resumes, job postings) into clean, validated records.
All pass/fail decisions, scoring and filtering belong in logic_manager.
Reading the resume file belongs to the input manager. To try this module on its
own, run src/ai_manager_test/ai_manager_test.py.

WHAT main.py AND web_app.py CALL
--------------------------------
profile = extract_candidate_profile(resume_text)
  resume_text: plain text of the resume (from the input manager)
  profile:     candidate record (CANDIDATE_SCHEMA), or None if extraction failed
jobs = search_and_extract_jobs(profile)
  jobs:        list of job records (portal facts + AI-extracted requirements)
Hand both to logic_manager.

HOW THEY FLOW
-------------
1. AI call #1: resume text -> candidate record             (sections 2-4, 6)
      e.g. qualification, years of experience, skills, job titles to search for
2. Search MyCareersFuture for each job title               (section 5)
3. AI calls #2+: batches of 10 jobs -> job records         (sections 2-4, 6)
      e.g. required skills, min education, which of your skills match, a reason

Separately, tailor_resume(resume_text, job) rewrites a resume for one job (1 AI call),
and fetch_job_details(job_url) gets that job's full description from the portal.

EVERY AI CALL GOES THROUGH THE SAME FOUR STEPS (run_task)
---------------------------------------------------------
build_prompt -> call_api -> parse_response -> validate_response
  write the      send it,     turn the reply   check the JSON has the
  instructions   get a reply  text into JSON   right keys, types and values
"""

# --- Standard library (comes with Python) ---
import html                                      # decode things like &amp; in job descriptions
import json                                      # convert between JSON text and Python dicts
import logging                                   # print INFO/WARNING/ERROR messages
import os                                        # read environment variables, file paths
import re                                        # regular expressions (text pattern matching)
import time                                      # sleep() while waiting to retry
from concurrent.futures import ThreadPoolExecutor, as_completed  # run several AI calls at the same time
from datetime import date                        # today's date, so the AI can tell finished from ongoing studies
from pathlib import Path                         # nicer file path handling

# --- Installed packages (see requirements.txt) ---
import anthropic                  # official SDK for Claude
import openai                     # official SDK for OpenAI (also works for Qwen and Gemini)
import requests                   # plain HTTP requests (used for the job portal)
from dotenv import load_dotenv    # loads the .env file into environment variables

# Show INFO-level messages and above in the terminal
logging.basicConfig(level=logging.INFO)

# Load .env from the project root (parent of src/), so os.getenv("QWEN_API_KEY") etc. work.
# __file__ is this file's path; .parent.parent goes up from src/ai_manager.py to the project root.
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

# Settings for each AI provider we support.
# Pick a provider with AI_PROVIDER in .env; AI_MODEL overrides its default model.
# "openai" SDK providers are called through the OpenAI client (Qwen and Gemini expose
# OpenAI-compatible endpoints); Anthropic is called through its own SDK.
# temperature=None means the parameter is left out (OpenAI's GPT-5 models only accept the default).
# temperature=0.0 makes answers as consistent as possible between runs.
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
JOBS_API_URL = "https://api.mycareersfuture.gov.sg/v2/search"
JOB_DETAILS_API_URL = "https://api.mycareersfuture.gov.sg/v2/jobs/{job_id}"
MAX_DESCRIPTION_CHARS = 2000   # job descriptions are cut to this length to keep prompts small
JOB_BATCH_SIZE = 10            # jobs sent to the AI per call
JOB_FETCH_ATTEMPTS = 3         # tries per job-portal search before giving up
RATE_LIMIT_RETRIES = 3         # retries per AI call when the provider is busy
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
            _client = anthropic.Anthropic(api_key=api_key, timeout=300, max_retries=0)
        else:
            _client = openai.OpenAI(api_key=api_key, base_url=config["base_url"], timeout=300, max_retries=0)
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
        # {"job_skill": "React.js", "candidate_skill": "React"}, so we can check it (_ground_skill_matches)
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
        "organisation": {"type": "string", "description": "Company, school or group. Empty string if none."},
        "dates": {"type": "string", "description": "Copied exactly from the resume. Empty string if none."},
        "bullets": STRING_LIST,
    },
    "required": ["title", "organisation", "dates", "bullets"],
    "additionalProperties": False,
}

# A resume rewritten for one job (tailor_resume). Checked against the original resume
# afterwards (_ground_tailored_resume), so nothing the candidate didn't write gets through.
TAILORED_RESUME_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "contact": {"type": "string", "description": "Email, phone and links from the resume, separated by ' · '"},
        "summary": {"type": "string", "description": "2-3 sentences written for this job"},
        "skills": {**STRING_LIST, "description": "Skills from the resume, most relevant to the job first"},
        "sections": {
            "type": "array",
            "description": "The resume's sections, e.g. Experience, Education, Projects, most relevant first",
            "items": {
                "type": "object",
                "properties": {"heading": {"type": "string"}, "entries": {"type": "array", "items": RESUME_ENTRY_SCHEMA}},
                "required": ["heading", "entries"],
                "additionalProperties": False,
            },
        },
        "changes": {**STRING_LIST, "description": "3-6 short notes on what was changed for this job and why"},
    },
    "required": ["name", "contact", "summary", "skills", "sections", "changes"],
    "additionalProperties": False,
}

# The kinds of AI call this module makes, and the schema each one uses
TASKS = {
    "candidate_profile": CANDIDATE_SCHEMA,
    "job_requirements": JOB_BATCH_SCHEMA,
    "tailored_resume": TAILORED_RESUME_SCHEMA,
}

# Range checks applied by validate_response (inclusive)
NUMERIC_RANGES = {"years_of_experience": (0, 60), "internship_months": (0, 240), "job_index": (0, 999)}
NON_EMPTY_LISTS = {"search_keywords"}  # we can't search for jobs without at least one keyword


# ==========================================
# 2. PROMPT BUILDING  (step 1 of every AI call)
# Writes the instructions sent to the AI. The RULES sections are where most
# accuracy fixes live, e.g. "Internships do NOT count" or "Python does not cover C++".
# ==========================================

def build_prompt(record: dict, task: str) -> str:
    """Constructs the prompt for a task from its input record.

    candidate_profile: record = {"resume_text": str}
    job_requirements:  record = {"jobs": list[dict], "candidate": dict (candidate record)}
    tailored_resume:   record = {"resume_text": str, "job": dict (job record, with description if known)}
    """
    if task == "candidate_profile":
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

    if task == "job_requirements":
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

    if task == "tailored_resume":
        job = record["job"]
        return f"""Rewrite this candidate's resume so it is tailored to the job below.

JOB:
Title: {job.get('title')}
Company: {job.get('company')}
Key skills: {', '.join(job.get('required_skills', [])) or 'Not known'}
Listed skills: {', '.join(job.get('listed_skills', [])) or 'None'}
Description: {job.get('description') or 'Not available'}

RESUME:
{record["resume_text"]}

RULES:
- Use ONLY facts in the resume. Never add a skill, tool, employer, job title, qualification, date,
  number or achievement that is not in the resume, even if the job asks for it. Leave gaps out.
- name and contact: copied exactly from the resume (contact: email, phone, links, separated by " · ").
  Empty string if the resume has none.
- summary: 2-3 sentences aimed at this job, using only facts from the resume. No numbers that are not in it.
- skills: skill names copied exactly as written in the resume, most relevant to this job first.
  Only if there are more than 15, leave out the ones that do not help with this job.
- sections: the resume's sections (e.g. Experience, Education, Projects, Certifications, Leadership),
  most relevant to this job first. Keep every job and qualification.
  For each entry, copy title, organisation and dates exactly as written in the resume.
  bullets: rewrite to show what matters for this job: start with an action verb, put the most relevant
  first, use the job's key skill words only where the resume shows that skill, and keep any numbers exactly.
  You may shorten or drop bullets that do not help with this job.
- changes: 3-6 short notes on what you changed and why,
  e.g. "Moved SQL to the top of Skills because the job asks for it".

Return JSON only."""

    raise ValueError(f"Unknown task: {task}")


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
            return result  # either the reply text, or None if it failed for another reason

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


def _with_retries(request, task: str):
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


def _validate_value(value, spec: dict, name: str, path: str):
    """Validates one value against its schema spec and returns the cleaned value.
    Raises ValueError describing the first problem found.

    This function is recursive: for a list or object it calls itself on each item/field,
    so one function can check the whole nested structure. "path" tracks where we are
    (e.g. "job_requirements.jobs[3].min_education") so error messages point to the problem.
    """
    expected = spec["type"]

    if expected == "string":
        if not isinstance(value, str):
            raise ValueError(f"{path} should be a string, got {value!r}")
        value = value.strip()
        if "enum" in spec:
            # Match the allowed option ignoring case, e.g. "diploma" -> "Diploma", "PhD" -> None
            matched = {option.lower(): option for option in spec["enum"]}.get(value.lower())
            if matched is None:
                raise ValueError(f"{path}={value!r} is not one of {spec['enum']}")
            value = matched
        return value

    if expected in ("integer", "number"):
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

    if expected == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path} should be a list, got {value!r}")
        item_spec = spec["items"]
        cleaned = []
        for i, item in enumerate(value):
            try:
                cleaned.append(_validate_value(item, item_spec, name, f"{path}[{i}]"))
            except ValueError as e:
                if item_spec["type"] != "object":
                    raise
                # One bad entry in a list of records shouldn't sink the rest
                # (e.g. one malformed job in a batch of 10 is dropped; the other 9 are kept)
                logging.warning(f"Dropping malformed entry: {e}")
        if item_spec["type"] == "string":
            cleaned = [v for v in cleaned if v]  # drop empty strings
        if name in NON_EMPTY_LISTS and not cleaned:
            raise ValueError(f"{path} must not be empty")
        return cleaned

    if expected == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path} should be an object, got {value!r}")
        cleaned = {}
        # Only required keys are copied over, so any extra keys the AI added are dropped
        for key in spec["required"]:
            if key not in value:
                raise ValueError(f"{path}.{key} is missing")
            cleaned[key] = _validate_value(value[key], spec["properties"][key], key, f"{path}.{key}")
        return cleaned

    raise ValueError(f"{path} has unsupported schema type {expected!r}")


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
    """build_prompt -> call_api -> parse_response -> validate_response."""
    # Each step returns None on failure, so we stop at the first problem
    raw = call_api(build_prompt(record, task), task)
    if raw is None:
        return None
    data = parse_response(raw)
    if data is None:
        return None
    return validate_response(data, task)


# ==========================================
# 5. JOB PORTAL FETCH
# Gets real job listings from MyCareersFuture (no AI involved here).
# ==========================================

def _clean_html(text: str) -> str:
    """Strips HTML tags and collapses whitespace."""
    # Job descriptions arrive as HTML, e.g. "<p>We are <strong>hiring</strong></p>" -> "We are hiring"
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def fetch_jobs(search_query: str, limit: int = 10) -> list[dict]:
    """Fetches job listings from MyCareersFuture. No filtering is done here.
    Retries timeouts and server errors; returns an empty list if every attempt fails."""
    raw_jobs = None
    for attempt in range(1, JOB_FETCH_ATTEMPTS + 1):
        try:
            # The portal reads the search words from the JSON body; in the URL they are ignored
            resp = requests.post(JOBS_API_URL, params={"limit": limit}, json={"search": search_query}, timeout=20)
            resp.raise_for_status()  # turns HTTP errors (e.g. 500) into exceptions
            raw_jobs = resp.json().get("results", [])
            break  # success - stop retrying
        except (requests.RequestException, ValueError) as e:
            if attempt == JOB_FETCH_ATTEMPTS:
                logging.error(f"Job search for '{search_query}' failed after {attempt} attempts: {e}")
                return []
            logging.warning(f"Job search for '{search_query}' failed ({type(e).__name__}); retrying...")
            time.sleep(2 * attempt)  # wait a little longer each time: 2s, then 4s

    # The portal returns lots of nested data; keep only the fields we use, with simple names.
    # "x.get(key) or {}" means: use the value if present, otherwise an empty dict, so missing
    # data doesn't crash the program.
    jobs = []
    for job in raw_jobs:
        company = (job.get("hiringCompany") or job.get("postedCompany") or {}).get("name")
        salary = job.get("salary") or {}
        districts = (job.get("address") or {}).get("districts") or []

        jobs.append({
            "title": job.get("title", "N/A"),
            "company": company or "N/A",
            "location": districts[0]["location"] if districts else "Singapore",
            "min_salary": salary.get("minimum", 0),
            "max_salary": salary.get("maximum", 0),
            "salary_period": ((salary.get("type") or {}).get("salaryType") or "Monthly").lower(),
            "employment_types": [e["employmentType"] for e in job.get("employmentTypes", [])],
            "position_levels": [p["position"] for p in job.get("positionLevels", [])],
            "min_years_experience": job.get("minimumYearsExperience") or 0,
            "flexible_work_arrangements": [
                f["flexibleWorkArrangement"] for f in job.get("flexibleWorkArrangements") or []
            ],
            "listed_skills": [s["skill"] for s in job.get("skills", [])],
            "description": _clean_html(job.get("description", ""))[:MAX_DESCRIPTION_CHARS],
            "job_url": (job.get("metadata") or {}).get("jobDetailsUrl", "N/A"),
        })
    return jobs


def fetch_job_details(job_url: str) -> dict | None:
    """Gets one job's full description from MyCareersFuture, using the id at the end of its link.
    Returns {"description", "listed_skills", "is_open"}, or None if it can't be fetched."""
    # e.g. ".../job/it/data-analyst-acme-5365191b6ddd018b70f7b8149c0a20ca" -> "5365191b...20ca"
    match = re.search(r"([0-9a-f]{32})/?(?:\?.*)?$", job_url or "")
    if not match:
        return None
    try:
        resp = requests.get(JOB_DETAILS_API_URL.format(job_id=match.group(1)), timeout=20)
        resp.raise_for_status()
        job = resp.json()
    except (requests.RequestException, ValueError) as e:
        logging.warning(f"Could not fetch job details for {job_url} ({type(e).__name__})")
        return None
    return {
        "description": _clean_html(job.get("description", ""))[:MAX_DESCRIPTION_CHARS],
        "listed_skills": [s["skill"] for s in job.get("skills", [])],
        "is_open": ((job.get("status") or {}).get("jobStatus") or "Open") == "Open",
    }


# ==========================================
# 6. PUBLIC FUNCTIONS FOR OTHER MODULES
# These tie sections 1-5 together. Other files (main.py, and the scripts in
# src/ai_manager_test/) should call these rather than the lower-level functions above.
# main.py and web_app.py need extract_candidate_profile and search_and_extract_jobs.
# ==========================================

def extract_candidate_profile(resume_text: str) -> dict | None:
    """Returns a validated candidate record (see CANDIDATE_SCHEMA), or None."""
    return run_task({"resume_text": resume_text}, "candidate_profile")


def _ground_skill_matches(requirements: dict, candidate_skills: list[str]) -> dict:
    """Keeps only skill matches that point at a real candidate skill and a real
    required skill, then derives matched_skills and missing_skills from them.

    This is the anti-hallucination check: if the AI claims "C++ <- Rust" but the
    candidate has no "Rust" skill, the match is thrown away and C++ counts as missing.
    """
    known_candidate = {s.lower() for s in candidate_skills}       # a set, for fast "in" checks
    required = requirements["required_skills"]
    required_lookup = {s.lower(): s for s in required}             # lowercase -> original spelling

    matches = {}
    # .pop() removes skill_matches from the dict and returns it; it's replaced below
    for m in requirements.pop("skill_matches"):
        job_skill = required_lookup.get(m["job_skill"].lower())
        if job_skill and m["candidate_skill"].lower() in known_candidate:
            matches.setdefault(job_skill, m["candidate_skill"])   # keep the first match per job skill

    requirements["matched_skills"] = [{"job_skill": j, "candidate_skill": c} for j, c in matches.items()]
    requirements["missing_skills"] = [s for s in required if s not in matches]
    return requirements


def extract_job_requirements(jobs: list[dict], profile: dict) -> list[dict]:
    """Extracts requirements for a batch of job listings in one AI call.

    Returns each listing merged with its requirements. Listings the model skipped
    or returned malformed are left out. Descriptions are dropped to keep records small.
    """
    result = run_task({"jobs": jobs, "candidate": profile}, "job_requirements")
    if result is None:
        logging.warning(f"Could not extract requirements for a batch of {len(jobs)} jobs.")
        return []

    # Match each AI answer back to its job using job_index (the "### JOB n" number in the prompt)
    by_index = {}
    for requirements in result["jobs"]:
        index = requirements.pop("job_index")
        if index < len(jobs):
            by_index.setdefault(index, requirements)

    records = []
    for i, job in enumerate(jobs):
        if i not in by_index:
            logging.warning(f"No requirements returned for: {job.get('title')}")
            continue
        listing = {k: v for k, v in job.items() if k != "description"}
        # {**a, **b} merges two dicts: the portal's facts + the AI's extracted requirements
        records.append({**listing, **_ground_skill_matches(by_index[i], profile["core_skills"])})
    return records


def search_and_extract_jobs(profile: dict, limit_per_search: int = 10, max_workers: int = 5,
                            on_progress=None) -> list[dict]:
    """Searches the job portal with the profile's keywords and extracts each
    listing's requirements. Listings are de-duplicated by URL and sent to the AI
    in batches of JOB_BATCH_SIZE, with batches running in parallel.

    on_progress (optional) lets a UI show progress. It is called as
    on_progress(stage, done, total): stage "search" after each portal search,
    "analyse" after each AI batch finishes.
    """
    # A dict keyed by URL removes duplicates: the same job found by two searches is kept once
    unique_jobs = {}
    keywords = profile["search_keywords"]
    for done, keyword in enumerate(keywords, start=1):
        logging.info(f"Searching portal for '{keyword}'...")
        for job in fetch_jobs(keyword, limit=limit_per_search):
            if job["job_url"] != "N/A":
                unique_jobs.setdefault(job["job_url"], job)
        if on_progress:
            on_progress("search", done, len(keywords))

    # Split into batches of 10, e.g. 40 jobs -> [jobs 0-9, 10-19, 20-29, 30-39]
    jobs = list(unique_jobs.values())
    batches = [jobs[i:i + JOB_BATCH_SIZE] for i in range(0, len(jobs), JOB_BATCH_SIZE)]
    logging.info(f"Extracting requirements for {len(jobs)} unique listings in {len(batches)} batches...")

    # Send up to 5 batches to the AI at the same time instead of one after another.
    # as_completed yields each batch as soon as it finishes, so progress can be reported;
    # the results are then read back in the original batch order.
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(extract_job_requirements, batch, profile) for batch in batches]
        for done, _ in enumerate(as_completed(futures), start=1):
            if on_progress:
                on_progress("analyse", done, len(batches))
        results = [future.result() for future in futures]
    # Flatten the list of lists into one list of job records
    return [record for batch_records in results for record in batch_records]


def _normalise(text: str) -> str:
    """Lowercase, one kind of dash, single spaces: "Jan 2024 – Present" and "jan 2024 - present" match."""
    text = re.sub(r"[‐-―−]", "-", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _ground_tailored_resume(tailored: dict, resume_text: str) -> tuple[dict, list[str], list[str]]:
    """The anti-hallucination check for tailor_resume: removes anything the original resume
    doesn't contain. Returns (resume, skills removed, notes about other removals).

    Name, contact details, skills, organisations and dates must appear in the resume text
    as whole words (so a skill "C" is not "found" inside "Excel").
    The summary and bullets may be reworded, but every number in them (e.g. "30%", "2,000")
    must be a whole number in the resume (so "15" is not "found" inside a phone number).
    Known limit: a real number reused for a different claim still passes, e.g. "2 dashboards"
    when the resume only says "2-week sprint".
    """
    def numbers_in(text: str) -> set[str]:
        # Every whole number, without commas: "2,000 users, 15%" -> {"2000", "15"}
        return {number.replace(",", "") for number in re.findall(r"\d+(?:[.,]\d+)*", text)}

    source = _normalise(resume_text)
    source_numbers = numbers_in(source)

    def found(text: str) -> bool:
        # Whole words only: "SQL" is found in "SQL, Excel", but "Java" is not found in "JavaScript"
        return re.search(rf"(?<!\w){re.escape(_normalise(text))}(?!\w)", source) is not None

    def numbers_found(text: str) -> bool:
        return numbers_in(text) <= source_numbers  # <= on sets: every number is in the resume

    notes = []
    if tailored["name"] and not found(tailored["name"]):
        tailored["name"] = ""
    contact_parts = re.split(r"\s*[·|•]\s*", tailored["contact"])
    tailored["contact"] = " · ".join(part for part in contact_parts if part and found(part))

    removed_skills = [skill for skill in tailored["skills"] if not found(skill)]
    tailored["skills"] = [skill for skill in tailored["skills"] if found(skill)]

    if not numbers_found(tailored["summary"]):
        notes.append("The summary was left out because it had a number that isn't in your resume.")
        tailored["summary"] = ""

    for section in tailored["sections"]:
        kept = []
        for entry in section["entries"]:
            where = entry["organisation"] or entry["title"]
            if not where or not found(where):
                notes.append(f'Left out "{entry["title"] or entry["organisation"]}": it isn\'t in your resume.')
                continue
            if entry["dates"] and not found(entry["dates"]):
                entry["dates"] = ""
            bullets = [bullet for bullet in entry["bullets"] if numbers_found(bullet)]
            dropped = len(entry["bullets"]) - len(bullets)
            if dropped:
                notes.append(f'Left out {dropped} point(s) under "{entry["title"]}" with numbers that '
                             f"aren't in your resume.")
            entry["bullets"] = bullets
            kept.append(entry)
        section["entries"] = kept
    tailored["sections"] = [section for section in tailored["sections"] if section["entries"]]
    return tailored, removed_skills, notes


def tailor_resume(resume_text: str, job: dict) -> dict | None:
    """Rewrites a resume for one job (1 AI call), keeping only facts from the original.
    job is a job record, ideally with "description" from fetch_job_details.
    Returns {"resume": TAILORED_RESUME_SCHEMA record, "removed_skills", "notes"}, or None."""
    result = run_task({"resume_text": resume_text, "job": job}, "tailored_resume")
    if result is None:
        return None
    resume, removed_skills, notes = _ground_tailored_resume(result, resume_text)
    return {"resume": resume, "removed_skills": removed_skills, "notes": notes}
