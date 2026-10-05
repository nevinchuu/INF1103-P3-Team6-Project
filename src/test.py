import json
import logging
import os
import requests
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)

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


# ==========================================
# 2. DYNAMIC RESUME EXTRACTION (No Hardcoded Profile Assumptions)
# ==========================================

PROFILE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "candidate_name": {"type": "STRING"},
        "highest_qualification": {"type": "STRING", "description": "Exact qualification level, e.g., Diploma, Bachelor's, Master's, High School"},
        "years_of_experience": {"type": "INTEGER", "description": "Exact full-time work experience in years"},
        "target_position_level": {"type": "STRING", "description": "Appropriate role level, e.g. Entry-level/Junior, Mid-Level, Senior, Managerial"},
        "seo_keywords": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
            "description": "2 broad SEO keywords dynamically derived from the resume skills and target role"
        },
        "core_skills": {
            "type": "ARRAY",
            "items": {"type": "STRING"}
        }
    },
    "required": [
        "candidate_name", 
        "highest_qualification", 
        "years_of_experience", 
        "target_position_level", 
        "seo_keywords", 
        "core_skills"
    ]
}

def extract_resume_profile(client: genai.Client, resume_text: str) -> dict | None:
    """Dynamically extracts all profile parameters from the candidate's resume."""
    prompt = f"""Analyze this resume and dynamically extract the candidate's parameters:

{resume_text}

Extract:
1. Candidate name.
2. Highest qualification level present on the resume.
3. Total full-time years of work experience (0 if student/fresh grad).
4. Target position level matching their background.
5. 2 broad SEO search keywords matching their domain and level for portal search.
6. Core technical/professional skills."""

    try:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=PROFILE_SCHEMA,
                thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                max_output_tokens=350,
                temperature=0.0
            )
        )
        return json.loads(response.text)
    except Exception as e:
        logging.error(f"Failed to extract dynamic profile: {e}")
        return None


# ==========================================
# 3. DYNAMIC JOB PORTAL FETCH
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

        # Dynamic Experience Filter based on candidate's resume parameters
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
# 4. DYNAMIC EVALUATION SCHEMA & FUNCTION
# ==========================================

JOB_EVALUATION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "job_title": {"type": "STRING"},
        "company": {"type": "STRING"},
        "location": {"type": "STRING"},
        "employment_type": {"type": "STRING"},
        "match_score": {"type": "INTEGER", "description": "1-10 match score evaluated against candidate parameters"},
        "suitability_reason": {"type": "STRING", "description": "Explanation comparing candidate profile specs to job specs"},
        "min_pay": {"type": "INTEGER"},
        "max_pay": {"type": "INTEGER"},
        "currency": {"type": "STRING"},
        "job_url": {"type": "STRING"},
        "comments": {"type": "STRING"}
    },
    "required": [
        "job_title", "company", "location", "employment_type",
        "match_score", "suitability_reason", "min_pay", "max_pay", 
        "currency", "job_url", "comments"
    ]
}

def evaluate_job_fit(client: genai.Client, candidate_profile: dict, job_listing: dict) -> dict | None:
    """Evaluates job fit dynamically against candidate parameters."""
    cand_qual = candidate_profile.get("highest_qualification", "Not specified")
    cand_exp = candidate_profile.get("years_of_experience", 0)
    cand_level = candidate_profile.get("target_position_level", "Entry-level")
    cand_skills = ", ".join(candidate_profile.get("core_skills", []))

    # Dynamic rules injection from extracted parameters
    prompt = f"""
Evaluate if this REAL JOB LISTING matches the CANDIDATE PROFILE based strictly on their resume parameters.

DYNAMIC EVALUATION CRITERIA:
1. Qualification Match: Candidate's qualification is '{cand_qual}'. If the job mandates a higher qualification degree than '{cand_qual}', penalize match_score (< 4).
2. Experience Match: Candidate has {cand_exp} years of experience. If the job requires significantly more than {cand_exp} years, penalize match_score (< 4).
3. Level Match: Target level is '{cand_level}'. Rate high scores (7-10) for roles matching this level and skills: [{cand_skills}].
4. Strict Facts: Do NOT invent or hallucinate job details. Use ONLY facts from REAL JOB LISTING DATA.
5. Set currency strictly to 'SGD'.

CANDIDATE PROFILE:
Name: {candidate_profile.get('candidate_name')}
Qualification: {cand_qual}
Experience: {cand_exp} years
Target Level: {cand_level}
Core Skills: {cand_skills}

REAL JOB LISTING DATA:
{json.dumps(job_listing, indent=2)}
"""

    try:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=JOB_EVALUATION_SCHEMA,
                thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                max_output_tokens=350,
                temperature=0.0
            )
        )
        return json.loads(response.text)
    except Exception as e:
        logging.error(f"Error evaluating job: {e}")
        return None


# ==========================================
# 5. PIPELINE ORCHESTRATOR
# ==========================================

def run_job_matching_pipeline(file_path: str, min_match_score: int = 2) -> list[dict]:
    client = genai.Client()

    # Step 1: Read resume
    logging.info(f"Reading resume from {file_path}...")
    resume_text = read_resume_file(file_path)

    # Step 2: Extract candidate parameters dynamically
    logging.info("Extracting dynamic candidate parameters from resume...")
    profile = extract_resume_profile(client, resume_text)
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

    # Dynamic calculation of max experience tolerance (allows up to +1 year gap)
    cand_exp = profile["years_of_experience"]
    max_allowed_exp = cand_exp + (1 if cand_exp <= 2 else 2)

    seen_urls = set()
    raw_jobs = []

    # Step 3: Fetch jobs dynamically using extracted keywords and constraints
    for keyword in profile.get("seo_keywords", []):
        logging.info(f"Searching portal for query: '{keyword}' (Max Exp Filter: {max_allowed_exp} yrs)...")
        jobs = fetch_mycareersfuture_jobs(
            search_query=keyword, 
            max_experience_allowed=max_allowed_exp, 
            limit=12
        )
        
        for j in jobs:
            if j["job_url"] not in seen_urls and j["job_url"] != "N/A":
                seen_urls.add(j["job_url"])
                raw_jobs.append(j)

    logging.info(f"Retrieved {len(raw_jobs)} unique candidates-matching listings. Evaluating fit...")

    # Step 4: Evaluate and rank jobs
    matched_jobs = []
    for job in raw_jobs:
        evaluated = evaluate_job_fit(client, profile, job)
        if evaluated and evaluated.get("match_score", 0) >= min_match_score:
            matched_jobs.append(evaluated)

    matched_jobs.sort(key=lambda x: x.get("match_score", 0), reverse=True)
    return matched_jobs


# ==========================================
# 6. EXECUTION
# ==========================================

if __name__ == "__main__":
    RESUME_FILE_PATH = r"C:\Users\nev\SIT\INF1103 Programming\Project\nevin_chua.pdf"

    if not os.path.exists(RESUME_FILE_PATH):
        with open(RESUME_FILE_PATH, "w") as f:
            f.write("Nevin Chua\nDiploma in Information Technology. Skills in Python, HTML/CSS, SQL.")

    results = run_job_matching_pipeline(RESUME_FILE_PATH, min_match_score=5)

    print("\n--- DYNAMICALLY MATCHED JOB LISTINGS ---")
    print(json.dumps(results, indent=2))