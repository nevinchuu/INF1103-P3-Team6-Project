"""Data layer: the saved jobs, kept between runs, and the job records the portal lists.

DataManager is the only class the rest of the program talks to for data: it saves new jobs, loads them on startup,
searches and finds them, updates a note or status, removes them, and fetches listings from the job portal. The saved
jobs are kept by a Storage (storage.py): PostgresStorage (postgres_storage.py), whose address comes from the
DATABASE_URL environment variable. A database that can't be reached is reported as DataStoreError, so the app
shows a message instead of crashing.
"""

import os
from collections.abc import Callable
from typing import TypeVar

from data_layer.data_store_error import DataStoreError
from data_layer.job_portal import JobPortal, SAVED_LISTINGS_PATH
from data_layer.postgres_storage import PostgresStorage
from data_layer.storage import Storage

# Where the database is when DATABASE_URL isn't set (the db service in docker-compose.yml)
DEFAULT_DATABASE_URL = "postgresql://jobmatcher:jobmatcher@localhost:5432/jobmatcher"

# What a saved job's status can be set to (the update operation: a status and a note on a saved job)
STATUSES = ["Saved", "Applied", "Interviewing", "Offer", "Rejected"]
DEFAULT_STATUS = STATUSES[0]  # what a saved job without a status counts as
MAX_NOTE_CHARS = 500
STRONG_MATCH = "Strong match"  # the logic layer's best outcome, counted by tracker_summary
# Fields the pages loop over: a saved job from an older version or a hand-edited file may hold something else
LIST_FIELDS = ["required_skills", "missing_skills", "listed_skills", "employment_types", "position_levels"]

T = TypeVar("T")


class DataManager:
    """Saves, loads, searches and updates the jobs the program has found."""

    def __init__(self, storage: Storage, portal: JobPortal) -> None:
        # Aggregation: the storage and the portal are handed in and exist on their own (a test hands in fakes);
        # the manager uses them without owning them
        self.__storage = storage
        self.__portal = portal

    @classmethod
    def from_environment(cls) -> "DataManager":
        """A manager using the PostgreSQL database in DATABASE_URL (or the default one) and the real job portal,
        which keeps the last listings of each search in data/ (PORTAL_LISTINGS_PATH overrides)."""
        return cls(PostgresStorage(os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL),
                   JobPortal(saved_listings_path=os.getenv("PORTAL_LISTINGS_PATH") or SAVED_LISTINGS_PATH))

    def __stored(self, action: Callable[..., T], *args: object) -> T:
        """Runs one storage call; a store that can't be reached is reported as DataStoreError."""
        try:
            return action(*args)
        except OSError as error:
            raise DataStoreError(f"The saved jobs could not be reached ({error})") from error

    # ---------- job records from the portal ----------

    def fetch_listings(self, keywords: list[str], steps: list[str], preferences: dict | None, limit: int = 10,
                       fits: Callable[[dict, str], bool] | None = None,
                       on_progress: Callable[[str, int, int], None] | None = None,
                       summary: dict | None = None) -> list[dict]:
        """Job listings from the portal for each keyword, trying the search steps in order (see JobPortal)."""
        return self.__portal.fetch_listings(keywords, steps, preferences, limit_per_search=limit, fits=fits,
                                            on_progress=on_progress, summary=summary)

    def portal_job_details(self, job_url: str) -> dict | None:
        """One job's full description from the portal, or None if it can't be fetched."""
        return self.__portal.details(job_url)

    def portal_filters(self, preferences: dict | None, step: str) -> dict:
        """The portal's own search filters for one search step."""
        return self.__portal.filters_for(preferences, step)

    # ---------- create ----------

    def save_new_jobs(self, jobs: list[dict], resume_name: str) -> int:
        """Saves the jobs with a link that aren't saved yet, noting which resume found them; returns how many
        were new. Raises DataStoreError if the store can't be reached."""
        # Copies, so the caller's records aren't changed; "resume" is which resume to tailor for this job later.
        # A job without a link is not saved: it can't be found again or checked for duplicates
        new_jobs = [{**job, "resume": resume_name}
                    for job in jobs if Storage.has_link(job)]
        return self.__stored(self.__storage.insert_many, new_jobs)

    # ---------- read ----------

    def read_saved_jobs(self) -> list[dict]:
        """Every saved job, oldest first. Raises DataStoreError if the store can't be opened."""
        return [self.__tidy(job) for job in self.__stored(self.__storage.load_all)]

    def get_saved_job(self, job_url: str) -> dict | None:
        """The saved job with this link, or None."""
        job = self.__stored(self.__storage.get, job_url)
        return self.__tidy(job) if job is not None else None

    @staticmethod
    def __tidy(job: dict) -> dict:
        """A copy of a saved job the pages can always show: list fields that aren't lists become empty, skill
        matches without a job skill are dropped, and an unknown status counts as the default one."""
        tidied = {**job}
        for field in LIST_FIELDS:
            if field in tidied and not isinstance(tidied[field], list):
                tidied[field] = []
        matches = tidied.get("matched_skills", [])
        tidied["matched_skills"] = [match for match in (matches if isinstance(matches, list) else [])
                                    if isinstance(match, dict) and isinstance(match.get("job_skill"), str)]
        if "status" in tidied and tidied["status"] not in STATUSES:
            tidied["status"] = DEFAULT_STATUS
        return tidied

    def tracker_summary(self) -> dict:
        """Where the job search stands: {"total", "by_status": {status: count, in STATUSES order}, "strong_not_applied":
        strong matches still only saved}. Counted by the storage (SQL in PostgreSQL). Raises DataStoreError like the reads."""
        by_status = {status: 0 for status in STATUSES}
        strong_not_applied = 0
        for (status, outcome), count in self.__stored(self.__storage.count_by_status_and_outcome).items():
            status = status if status in STATUSES else DEFAULT_STATUS  # as tidy does for one job
            by_status[status] += count
            if status == DEFAULT_STATUS and outcome == STRONG_MATCH:
                strong_not_applied += count
        return {"total": sum(by_status.values()), "by_status": by_status, "strong_not_applied": strong_not_applied}

    def search_saved(self, keyword: str) -> list[dict]:
        """Query on the store itself: saved jobs matching every word in keyword."""
        return [self.__tidy(job) for job in self.__stored(self.__storage.search, keyword)]

    # ---------- update ----------

    def update_saved_job(self, job_url: str, status: str | None = None, note: str | None = None) -> bool:
        """Sets a saved job's status and/or note. Returns False if the job isn't saved. The caller has checked them
        (LogicManager.validate_saved_update)."""
        changes = {}
        if status is not None:
            changes["status"] = status
        if note is not None:
            changes["note"] = note.strip()
        if not changes:
            return self.__stored(self.__storage.get, job_url) is not None
        return self.__stored(self.__storage.update, job_url, changes)

    # ---------- delete ----------

    def remove_saved_job(self, job_url: str) -> bool:
        """Removes one saved job, found by its link. False if it wasn't saved."""
        return self.__stored(self.__storage.delete, job_url)

    def clear_saved_jobs(self) -> None:
        """Removes every saved job."""
        self.__stored(self.__storage.clear)
