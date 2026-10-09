import json
import urllib.request
import urllib.error
from urllib.parse import urlparse

# Ordered lowest to highest, so a list index works as an education level number.
# Same values as ai_manager.EDUCATION_LEVELS (copied so this layer does not import another layer)
EDUCATION_LEVELS = ["None", "Secondary", "ITE/Nitec", "A-Level", "Diploma", "Bachelor's", "Master's", "Doctorate"]
ANY = "Any"  # the filter value meaning "no preference" (from io_manager)

def load_inventory():
    try:
        nested_list = []
        file_path = "src/sample_job_listings.json"
        with open(file_path, "r") as file:
            data = json.load(file)
            job_listings = data.get("job_listings", [])
            
            valid_job_listings = check_list_url(job_listings)
            data["job_listings"] = valid_job_listings
            print(valid_job_listings)
            
            # Sort jobs by maximum salary, from highest to lowest
            valid_job_listings.sort(
                key=lambda item: item.get("pay_range", {}).get("max", 0),
                reverse=True
            )
            
            for item in valid_job_listings:
                pay_range = item.get("pay_range", {})
                nested_list.append({
                    "job_title": item.get("job_title", "NaN"),
                    "company": item.get("company", "NaN"),
                    "location": item.get("location", "NaN"),
                    "employment_type": item.get("employment_type", "NaN"),
                    "suitability_reason": item.get("suitability_reason", "NaN"),
                    "min": pay_range.get("min", -1),
                    "max": pay_range.get("max", -1),
                    "currency": pay_range.get("currency", "NaN"),
                    "period": pay_range.get("period", "NaN"),
                    "job_url": item.get("job_url", "NaN") 
                })
            display_listings(nested_list)
            with open("src/updated_job_listings.json", "w") as file:
                json.dump(nested_list, file, indent=2)

        return nested_list

    except FileNotFoundError:
        print("Error: The file 'sample_job_listings.json' was not found.")
        return []

def display_listings(listings):
    print("==========================================")
    for item in listings:
        print(f"Job Title: {item['job_title']}")
        print(f"Company: {item['company']}")
        print(f"Location: {item['location']}")
        print(f"Employment Type: {item['employment_type']}")
        print(f"Suitability Reason: {item['suitability_reason']}")
        print(f"Min Pay: {item['min']}")
        print(f"Max Pay: {item['max']}")
        print(f"Currency: {item['currency']}")
        print(f"Period: {item['period']}")
        print(f"URL: {item.get('job_url', 'N/A')}")
        print("==========================================")
        
        
def check_list_url(listings):
    valid_url_list = []
    for item in listings:
        url = item.get("job_url", "NaN")
        if check_url(url):
            valid_url_list.append(item)
    return valid_url_list



def check_url(url):
    try:
        # Create request to URL
        req = urllib.request.Request(
            url,
            method="HEAD",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        # Try to connect to the URL
        with urllib.request.urlopen(req, timeout=10) as response:
            print("URL is reachable!")
            print("Status code:", response.status)
            return True

    # Checking HTTPError, URLError, and ValueError to handle different types of URL issues
    except urllib.error.HTTPError as e:
        print("HTTP error:", e.code)
        return False

    except urllib.error.URLError as e:
        print("URL is unreachable:", e.reason)
        return False

    except ValueError as e:
        print("Invalid URL:", e)
        return False


# Checks the link format only, without going online, so it is instant and
# cannot fail because of the network
def is_valid_job_url(url):
    if not isinstance(url, str):
        return False
    try:
        parts = urlparse(url)
    except ValueError:
        return False
    host = parts.hostname or ""
    return parts.scheme == "https" and (
        host == "mycareersfuture.gov.sg" or host.endswith(".mycareersfuture.gov.sg")
    )


# Returns why a job is removed, e.g. "salary", or "" if it has a valid link and
# meets all of the user's filters (from io_manager.prompt_filters)
def filter_reason(job, filters):
    if not is_valid_job_url(job.get("job_url")):
        return "bad link"

    # Salary: drop jobs whose pay range is completely outside the user's range.
    # Jobs that do not state a salary (0) are kept.
    job_min = job.get("min_salary") or 0
    job_max = job.get("max_salary") or 0
    if job_max > 0 and job_max < filters["min_salary"]:
        return "salary"
    if job_min > 0 and job_min > filters["max_salary"]:
        return "salary"

    # Experience: drop jobs that need more years than the user allows
    if (job.get("min_years_experience") or 0) > filters["max_years_experience"]:
        return "experience"

    # Job type: must be one of the job's employment types, unless the user picked "Any".
    # "Permanent" jobs on MyCareersFuture are full-time, so they also count as "Full Time".
    job_types = job.get("employment_types", [])
    if "Permanent" in job_types:
        job_types = job_types + ["Full Time"]
    if filters["job_type"] != ANY and filters["job_type"] not in job_types:
        return "job type"

    # Work arrangement: must match, unless the user picked "Any" or the job does not say
    arrangement = job.get("work_arrangement", "Not specified")
    if filters["work_arrangement"] != ANY and arrangement not in (filters["work_arrangement"], "Not specified"):
        return "work arrangement"

    return ""


# Returns True if a job has a valid link and meets all of the user's filters
def passes_filters(job, filters):
    return filter_reason(job, filters) == ""


# Counts how many jobs each filter removed, e.g. {"salary": 14, "job type": 6}.
# A job failing several filters is counted once, under the first one it fails.
def count_removed(jobs, filters):
    counts = {}
    for job in jobs:
        reason = filter_reason(job, filters)
        if reason:
            counts[reason] = counts.get(reason, 0) + 1
    return counts


# Returns the top_n jobs that best suit the candidate (moved from ai_manager_test.py).
# Jobs whose experience or education requirement the candidate misses rank below
# those they meet; within each group, jobs are ordered by share of required skills
# matched, then by number of skills matched.
def select_top_jobs(profile, jobs, top_n):
    candidate_level = EDUCATION_LEVELS.index(profile["highest_qualification"])

    # sorted() compares the returned tuples item by item, smallest first:
    #   1. experience_gap   - 0 years short beats 2 years short
    #   2. not education_ok - False (meets it) sorts before True (doesn't)
    #   3. -match_ratio     - negative so a HIGHER ratio sorts FIRST
    #   4. -matched         - tie-breaker: more matched skills first
    def sort_key(job):
        experience_gap = max(0, job["min_years_experience"] - profile["years_of_experience"])
        education_ok = (
            job["min_education"] == "Not specified"
            or EDUCATION_LEVELS.index(job["min_education"]) <= candidate_level
        )
        matched = len(job["matched_skills"])
        match_ratio = matched / len(job["required_skills"]) if job["required_skills"] else 0
        return (experience_gap, not education_ok, -match_ratio, -matched)

    return sorted(jobs, key=sort_key)[:top_n]


# Logic layer entry point, called by main.py: removes jobs with a bad link or that
# fail the user's filters, then returns the top_n best matches
def filter_and_rank(profile, jobs, filters, top_n=5):
    kept = []
    for job in jobs:
        if passes_filters(job, filters):
            kept.append(job)
    return select_top_jobs(profile, kept, top_n)