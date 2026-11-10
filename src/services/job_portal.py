"""JobPortal: where job listings come from (MyCareersFuture, Singapore's public job portal)."""

import html
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable

import requests

# MyCareersFuture, where the job listings come from (search, details)
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
# The last listings each search returned, used when MyCareersFuture can't be reached (<project root>/data/)
SAVED_LISTINGS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data",
                                   "portal_listings.json")


class JobPortal:
    """MyCareersFuture through its public search API: search one job title, fetch one job's details, and run the
    multi-step search (all preferences, then fewer) the apps share."""

    def __init__(self, http: object = requests, sleep: Callable[[float], None] = time.sleep,
                 saved_listings_path: str | None = None) -> None:
        self._http = http      # anything with post/get like the requests module (a stub in tests)
        self._sleep = sleep    # how to wait between retries (a no-op in tests)
        # Where each search's last listings are kept, so a search still works when the portal can't be reached
        # (None: keep nothing, e.g. in tests)
        self.__saved_path = saved_listings_path
        self.__lock = threading.Lock()  # web searches run in background threads

    @classmethod
    def from_environment(cls) -> "JobPortal":
        """The real portal, keeping the last listings of each search in data/ (PORTAL_LISTINGS_PATH overrides)."""
        return cls(saved_listings_path=os.getenv("PORTAL_LISTINGS_PATH") or SAVED_LISTINGS_PATH)

    def clean_html(self, text: str) -> str:
        """Strips HTML tags and collapses whitespace."""
        # Job descriptions arrive as HTML, e.g. "<p>We are <strong>hiring</strong></p>" -> "We are hiring"
        text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
        return re.sub(r"\s+", " ", text).strip()

    def search(self, search_query: str, limit: int = 10, portal_filters: dict | None = None) -> list[dict] | None:
        """Fetches job listings from MyCareersFuture, with retries. Returns [] if none are listed, or None if
        every attempt failed."""
        raw_jobs = None
        for attempt in range(1, JOB_FETCH_ATTEMPTS + 1):
            try:
                # The portal reads the search words and filters from the JSON body; in the URL they are ignored
                response = self._http.post(JOBS_API_URL, params={"limit": limit},
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
                    return self.__last_listings(search_query, limit, portal_filters)
                logging.warning(f"Job search for '{search_query}' failed ({type(error).__name__}); retrying...")
                self._sleep(2 * attempt)  # wait a little longer each time: 2s, then 4s

        jobs = []
        for job in raw_jobs:
            try:
                jobs.append(self.listing_from_portal(job))
            except (AttributeError, KeyError, TypeError) as error:
                logging.warning(f"Skipped a '{search_query}' listing in an unexpected format "
                                f"({type(error).__name__}: {error})")
        self.__keep_listings(search_query, limit, portal_filters, jobs)
        return jobs

    # ---------- the last listings of each search, for when the portal can't be reached ----------

    @staticmethod
    def __search_key(search_query: str, limit: int, portal_filters: dict | None) -> str:
        return json.dumps([search_query.strip().lower(), limit, portal_filters or {}], sort_keys=True)

    def __read_saved(self) -> dict:
        try:
            with open(self.__saved_path, encoding="utf-8") as file:
                saved = json.load(file)
            return saved if isinstance(saved, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            logging.warning(f"Could not read the saved listings ({type(error).__name__}); ignoring them")
            return {}

    def __keep_listings(self, search_query: str, limit: int, portal_filters: dict | None, jobs: list[dict]) -> None:
        """Saves a successful search's listings. Saving is a convenience, so a failure is only logged."""
        if self.__saved_path is None:
            return
        with self.__lock:
            saved = self.__read_saved()
            saved[self.__search_key(search_query, limit, portal_filters)] = jobs
            temporary = f"{self.__saved_path}.tmp"
            try:
                os.makedirs(os.path.dirname(self.__saved_path) or ".", exist_ok=True)
                with open(temporary, "w", encoding="utf-8") as file:
                    json.dump(saved, file)
                os.replace(temporary, self.__saved_path)  # never leaves half a file
            except OSError as error:
                logging.warning(f"Could not save the listings ({type(error).__name__}: {error})")

    def __last_listings(self, search_query: str, limit: int, portal_filters: dict | None) -> list[dict] | None:
        """The listings this exact search returned last time, or None if it never succeeded."""
        if self.__saved_path is None:
            return None
        with self.__lock:
            jobs = self.__read_saved().get(self.__search_key(search_query, limit, portal_filters))
        if not isinstance(jobs, list):
            return None
        logging.warning(f"Using the last saved listings for '{search_query}' (MyCareersFuture could not be reached)")
        return [job for job in jobs if isinstance(job, dict)]

    def listing_from_portal(self, job: dict) -> dict:
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
            "description": self.clean_html(job.get("description", ""))[:MAX_DESCRIPTION_CHARS],
            "job_url": (job.get("metadata") or {}).get("jobDetailsUrl", "N/A"),
        }

    def details(self, job_url: str) -> dict | None:
        """Gets one job's full description from MyCareersFuture, using the id at the end of its link.
        Returns {"description", "listed_skills", "is_open"}, or None if it can't be fetched."""
        # e.g. ".../job/it/data-analyst-acme-5365191b6ddd018b70f7b8149c0a20ca" -> "5365191b...20ca"
        match = re.search(r"([0-9a-f]{32})/?(?:\?.*)?$", job_url or "")
        if not match:
            return None
        try:
            response = self._http.get(JOB_DETAILS_API_URL.format(job_id=match.group(1)), timeout=20)
            response.raise_for_status()
            job = response.json()
            return {
                "description": self.clean_html(job.get("description", ""))[:MAX_DESCRIPTION_CHARS],
                "listed_skills": [skill["skill"] for skill in job.get("skills", [])],
                "is_open": ((job.get("status") or {}).get("jobStatus") or "Open") == "Open",
            }
        # A reply in an unexpected format (AttributeError, KeyError, TypeError) is treated like no reply
        except (requests.RequestException, ValueError, AttributeError, KeyError, TypeError) as error:
            logging.warning(f"Could not fetch job details for {job_url} ({type(error).__name__})")
            return None

    def filters_for(self, preferences: dict | None, step: str) -> dict:
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

    def fetch_listings(self, keywords: list[str], steps: list[str], preferences: dict | None, limit_per_search: int = 10,
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
            portal_filters = self.filters_for(preferences, step)
            # A dict keyed by URL removes duplicates: the same job found by two searches is kept once
            unique_jobs = {}
            portal_failed = False
            summary["search_step"] = step
            summary["searches_failed"] = 0
            summary["searches_errored"] = 0  # only the ones that failed (no reply), not empty ones
            for done, keyword in enumerate(keywords, start=1):
                key = (keyword, repr(sorted(portal_filters.items())))
                if key not in fetched:
                    logging.info(f"Searching portal for '{keyword}' ({step} search filters: {portal_filters})...")
                    fetched[key] = self.search(keyword, limit=limit_per_search, portal_filters=portal_filters)
                found = fetched[key]
                if not found:
                    summary["searches_failed"] += 1  # failed, or nothing listed under that title
                if found is None:
                    summary["searches_errored"] += 1
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
        summary["searches"] = len(keywords)
        return list(unique_jobs.values())
