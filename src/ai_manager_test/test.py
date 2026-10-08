import json
import logging
import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from openai import OpenAI

logging.basicConfig(level=logging.INFO)

# Load .env from the project root (this file is in src/ai_manager_test/, so go up three levels)
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent.parent / ".env")


client = OpenAI(
    api_key=os.getenv("QWEN_API_KEY"),
    base_url="https://ws-jqxl73dgs75w9q92.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
)

MODEL = "qwen-flash"


# ==========================================
# 1. FILE READING
# ==========================================

def read_resume_file(file_path: str) -> str:
    """Reads resume text from a .txt or .pdf file."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Resume file not found at: {file_path}")

    if file_path.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        text = "".join([page.extract_text() or "" for page in reader.pages])
        return text
    else:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()


def get_data_root() -> str:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(base_dir, "..", ".."))
    data_root = os.path.join(project_root, "data")
    return data_root


def get_resume_file_path(data_root: str) -> str:
    "Prompts the user for a resume name and returns the full path to the resume file."
    while True:
        filename = input("\nEnter the PDF resume filename in Data/")

        if not filename:
            print("Filename cannot be empty. Please try again.")
            continue

        if not filename.lower().endswith(".pdf"):
            print("Please provide a valid PDF filename (e.g., 'resume.pdf').")
            continue

        target_path = os.path.join(data_root, filename)

        try:
            if not os.path.exists(target_path):
                raise FileNotFoundError(f"Resume file not found at: {target_path}")

            print(f"Resume file found: {target_path}")
            return target_path

        except FileNotFoundError as e:
            print(f"Exception occurred: {e}")


# ==========================================
# 2. ROBUST JSON PARSING + CALL HELPER
# ==========================================

def parse_json_response(raw_content: str, context: str = "") -> dict | None:
    """Parses a JSON object from the model's response. Handles cases where the
    model wraps output in markdown code fences or adds stray text, which can
    happen even with response_format set, depending on the endpoint."""
    if not raw_content:
        logging.error(f"[{context}] Empty response from model.")
        return None

    text = raw_content.strip()

    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]

    try:
        return json.loads(text.strip())
    except json.JSONDecodeError as e:
        logging.error(f"[{context}] JSON parse failed: {e}")
        logging.error(f"[{context}] RAW MODEL OUTPUT WAS:\n{raw_content}\n")
        return None


def call_qwen_structured(prompt: str, schema_name: str, schema: dict, context: str) -> dict | None:
    """Calls Qwen with strict JSON Schema mode. If the endpoint rejects or
    doesn't support json_schema mode, automatically falls back to JSON Object
    mode with the schema described in the prompt text instead."""
    try:
        completion = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
            temperature=0.0,
        )
        raw = completion.choices[0].message.content
        result = parse_json_response(raw, context=f"{context} (schema mode)")
        if result is not None:
            return result
        # Fell through parsing failure - try the fallback path below
        logging.warning(f"[{context}] Schema-mode output failed to parse; trying json_object fallback...")
    except Exception as e:
        logging.warning(f"[{context}] json_schema mode call failed ({e}); falling back to json_object mode.")

    # Fallback: json_object mode, schema spelled out in the prompt itself
    fallback_prompt = (
        prompt
        + f"\n\nReturn ONLY a single valid JSON object (no markdown, no extra text) "
        f"matching exactly this structure:\n{json.dumps(schema, indent=2)}"
    )
    try:
        completion = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": fallback_prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        raw = completion.choices[0].message.content
        return parse_json_response(raw, context=f"{context} (fallback mode)")
    except Exception as e:
        logging.error(f"[{context}] json_object fallback also failed: {e}")
        return None


# ==========================================
# 3. DYNAMIC RESUME EXTRACTION
# ==========================================

PROFILE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "candidate_name": {"type": "string"},
        "highest_qualification": {
            "type": "string",
            "description": "Exact qualification level, e.g., Diploma, Bachelor's, Master's, High School",
        },
        "years_of_experience": {
            "type": "integer",
            "description": "Exact full-time work experience in years",
        },
        "target_position_level": {
            "type": "string",
            "description": "Appropriate role level, e.g. Entry-level/Junior, Mid-Level, Senior, Managerial",
        },
        "seo_keywords": {
            "type": "array",
            "items": {"type": "string"},
            "description": "2 broad SEO keywords dynamically derived from the resume skills and target role",
        },
        "core_skills": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "candidate_name",
        "highest_qualification",
        "years_of_experience",
        "target_position_level",
        "seo_keywords",
        "core_skills",
    ],
    "additionalProperties": False,
}


def extract_resume_profile(resume_text: str) -> dict | None:
    """Dynamically extracts all profile parameters from the candidate's resume."""
    prompt = f"""Analyze this resume and dynamically extract the candidate's parameters:

{resume_text}

Extract:
1. Candidate name.
2. Highest qualification level present on the resume.
3. Total full-time years of work experience (0 if student/fresh grad).
4. Target position level matching their background.
5. 2 to 3 BROAD search keywords (1-2 words max, e.g. "Fintech", "Finance", "Data") rather than exact job titles.
6. Core technical/professional skills."""

    return call_qwen_structured(
        prompt, schema_name="candidate_profile", schema=PROFILE_JSON_SCHEMA, context="extract_resume_profile"
    )


# ==========================================
# 4. DYNAMIC JOB PORTAL FETCH
# ==========================================

def fetch_mycareersfuture_jobs(search_query: str, max_experience_allowed: int, limit: int = 5) -> list[dict]:
    """Fetches real jobs and dynamically filters using the candidate's experience parameter."""
    resp = requests.get(
        "https://api.mycareersfuture.gov.sg/v2/jobs",
        params={"search": search_query, "limit": limit, "salary": 0},
        timeout=15,
    )
    resp.raise_for_status()
    raw_jobs = resp.json().get("results", [])

    trimmed = []
    for job in raw_jobs:
        company = (job.get("hiringCompany") or job.get("postedCompany") or {}).get("name")
        salary = job.get("salary") or {}
        districts = (job.get("address") or {}).get("districts") or []
        location = districts[0]["location"] if districts else "Singapore"
        min_years = job.get("minimumYearsExperience", 0)

        if min_years is not None and min_years > max_experience_allowed:
            continue

        trimmed.append({
            "title": job.get("title", "N/A"),
            "company": company or "N/A",
            "location": location,
            "min_salary": salary.get("minimum", 0),
            "max_salary": salary.get("maximum", 0),
            "employment_types": [e["employmentType"] for e in job.get("employmentTypes", [])],
            "min_years_experience": min_years,
            "job_url": (job.get("metadata") or {}).get("jobDetailsUrl", "N/A"),
        })
    return trimmed


# ==========================================
# 5. DYNAMIC EVALUATION
# ==========================================

JOB_EVALUATION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "job_title": {"type": "string"},
        "company": {"type": "string"},
        "location": {"type": "string"},
        "employment_type": {"type": "string"},
        "suitability_reason": {
            "type": "string",
            "description": "Concise summary (max 2 sentences, under 30 words) explaining profile fit.",
        },
        "min_pay": {"type": "integer"},
        "max_pay": {"type": "integer"},
        "currency": {"type": "string"},
        "job_url": {"type": "string"},
        "comments": {
            "type": "string",
            "description": "One short sentence (max 15 words) noting key takeaways or warnings.",
        },
    },
    "required": [
        "job_title", "company", "location", "employment_type",
        "suitability_reason", "min_pay", "max_pay",
        "currency", "job_url", "comments",
    ],
    "additionalProperties": False,
}


def evaluate_job_fit(candidate_profile: dict, job_listing: dict) -> dict | None:
    """Evaluates job fit dynamically against candidate parameters."""
    cand_qual = candidate_profile.get("highest_qualification", "Not specified")
    cand_exp = candidate_profile.get("years_of_experience", 0)
    cand_level = candidate_profile.get("target_position_level", "Entry-level")
    cand_skills = ", ".join(candidate_profile.get("core_skills", []))

    prompt = f"""
Evaluate if this REAL JOB LISTING matches the CANDIDATE PROFILE based strictly on their resume parameters.

DYNAMIC EVALUATION CRITERIA:
1. Qualification Match: Candidate's qualification is '{cand_qual}'.
2. Experience Match: Candidate has {cand_exp} years of experience.
3. Level Match: Target level is '{cand_level}' with skills: [{cand_skills}].
4. Strict Facts: Do NOT invent details. Use ONLY facts from REAL JOB LISTING DATA.
5. Set currency strictly to 'SGD'.

CONCISENESS RULES:
- suitability_reason: Max 2 short sentences (under 30 words total). State match or mismatch directly.
- comments: Max 1 short sentence (under 15 words). Example: "Good starter role for fresh grad" or "Requires 2 extra years of experience".

CANDIDATE PROFILE:
Name: {candidate_profile.get('candidate_name')}
Qualification: {cand_qual}
Experience: {cand_exp} years
Target Level: {cand_level}
Core Skills: {cand_skills}

REAL JOB LISTING DATA:
{json.dumps(job_listing, indent=2)}
"""

    return call_qwen_structured(
        prompt, schema_name="job_evaluation", schema=JOB_EVALUATION_JSON_SCHEMA, context="evaluate_job_fit"
    )


# ==========================================
# 6. PIPELINE ORCHESTRATOR
# ==========================================

def run_job_matching_pipeline(file_path: str) -> list[dict]:
    logging.info(f"Reading resume from {file_path}...")
    resume_text = read_resume_file(file_path)

    logging.info("Extracting dynamic candidate parameters from resume...")
    profile = extract_resume_profile(resume_text)
    if not profile:
        logging.error("Could not extract candidate profile.")
        return []

    print("\n--- DYNAMICALLY EXTRACTED PROFILE ---")
    print(f"Name:          {profile['candidate_name']}")
    print(f"Qualification: {profile['highest_qualification']}")
    print(f"Experience:    {profile['years_of_experience']} years")
    print(f"Target Level:  {profile['target_position_level']}")
    print(f"Skills:        {', '.join(profile['core_skills'])}")
    print(f"SEO Keywords:  {profile['seo_keywords']}\n")

    cand_exp = profile["years_of_experience"]
    max_allowed_exp = cand_exp + (1 if cand_exp <= 2 else 2)

    seen_urls = set()
    raw_jobs = []

    for keyword in profile.get("seo_keywords", []):
        logging.info(f"Searching portal for query: '{keyword}' (Max Exp Filter: {max_allowed_exp} yrs)...")
        jobs = fetch_mycareersfuture_jobs(
            search_query=keyword,
            max_experience_allowed=max_allowed_exp,
            limit=12,
        )
        for j in jobs:
            if j["job_url"] not in seen_urls and j["job_url"] != "N/A":
                seen_urls.add(j["job_url"])
                raw_jobs.append(j)

    logging.info(f"Retrieved {len(raw_jobs)} unique candidate-matching listings. Evaluating fit...")

    evaluated_jobs = []
    for job in raw_jobs:
        evaluated = evaluate_job_fit(profile, job)
        if not evaluated:
            logging.warning(f"No evaluation returned for: {job.get('title')}")
            continue
        evaluated_jobs.append(evaluated)

    return evaluated_jobs


# ==========================================
# 7. EXECUTION
# ==========================================

if __name__ == "__main__":
    try:
        resume_path = get_resume_file_path(get_data_root())
        results = run_job_matching_pipeline(resume_path)

        print("\n--- DYNAMICALLY MATCHED JOB LISTINGS ---")
        print(json.dumps(results, indent=2))
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")