"""Data layer: the saved jobs, kept between runs.

DataManager is the only class the rest of the program talks to: it saves new jobs, loads them on startup,
searches and finds them, updates a note or status, and removes them. Where they are kept is a Storage
(storage.py): a JSON file (json_storage.py) or a SQLite database (sqlite_storage.py), chosen by the
STORAGE_BACKEND environment variable. A missing file or database is created, and a corrupt one is backed up
and replaced, so a bad file never crashes the program.
"""

import os

from persistence.json_storage import JsonStorage
from persistence.sqlite_storage import SqliteStorage
from persistence.storage import Storage

# The data files live in <project root>/data/, wherever the program is run from
DATA_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
JSON_PATH = os.path.join(DATA_FOLDER, "job_listings.json")
SQLITE_PATH = os.path.join(DATA_FOLDER, "jobs.db")


# What a saved job's status can be set to (the update operation: a status and a note on a saved job)
STATUSES = ["Saved", "Applied", "Interviewing", "Offer", "Rejected"]
DEFAULT_STATUS = STATUSES[0]  # what a saved job without a status counts as
MAX_NOTE_CHARS = 500
STRONG_MATCH = "Strong match"  # LogicManager's best outcome (logic_manager.OUTCOME_STRONG), counted by tracker_summary
# Fields the pages loop over: a saved job from an older version or a hand-edited file may hold something else
LIST_FIELDS = ["required_skills", "missing_skills", "listed_skills", "employment_types", "position_levels"]


class DataManager:
    """Saves, loads, searches and updates the jobs the program has found."""

    def __init__(self, storage: Storage) -> None:
        # Aggregation: the storage is handed in and exists on its own (JSON or SQLite, or a test's temporary one);
        # the manager uses it without owning it
        self._storage = storage

    @classmethod
    def from_environment(cls) -> "DataManager":
        """A manager using the backend named by STORAGE_BACKEND ("json", the default, or "sqlite") and the file
        in JOBS_DB_PATH (or the default under data/)."""
        backend = os.getenv("STORAGE_BACKEND", "json").strip().lower()
        if backend == "sqlite":
            return cls(SqliteStorage(os.getenv("JOBS_DB_PATH") or SQLITE_PATH))
        return cls(JsonStorage(os.getenv("JOBS_DB_PATH") or JSON_PATH))

    @property
    def storage(self) -> Storage:
        """The storage in use (for showing where jobs are kept)."""
        return self._storage

    # ---------- create ----------

    def save_new_jobs(self, jobs: list[dict], resume_name: str) -> int:
        """Saves the jobs with a link that aren't saved yet, noting which resume found them; returns how many
        were new. Raises OSError if the store can't be reached."""
        # Copies, so the caller's records aren't changed; "resume" is which resume to tailor for this job later.
        # A job without a link is not saved: it can't be found again or checked for duplicates
        new_jobs = [{**job, "resume": resume_name}
                    for job in jobs if Storage.has_link(job)]
        return self._storage.insert_many(new_jobs)

    # ---------- read ----------

    def read_saved_jobs(self) -> list[dict]:
        """Every saved job, oldest first. Raises OSError if the store can't be opened."""
        return [self.tidy(job) for job in self._storage.load_all()]

    def get_saved_job(self, job_url: str) -> dict | None:
        """The saved job with this link, or None."""
        job = self._storage.get(job_url)
        return self.tidy(job) if job is not None else None

    @staticmethod
    def tidy(job: dict) -> dict:
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

    @staticmethod
    def search_jobs(saved_jobs: list[dict], keyword: str) -> list[dict]:
        """Query on a list already loaded (e.g. this session's results): jobs whose title, company or location
        contain every word in keyword, ignoring case."""
        words = keyword.lower().split()
        results = []
        for job in saved_jobs:
            text = " ".join(str(job.get(field, "")) for field in ("title", "company", "location")).lower()
            if all(word in text for word in words):
                results.append(job)
        return results

    def tracker_summary(self) -> dict:
        """Where the job search stands: {"total", "by_status": {status: count, in STATUSES order}, "strong_not_applied":
        strong matches still only saved}. Counted by the storage (SQL on SQLite). Raises OSError like the reads."""
        by_status = {status: 0 for status in STATUSES}
        strong_not_applied = 0
        for (status, outcome), count in self._storage.count_by_status_and_outcome().items():
            status = status if status in STATUSES else DEFAULT_STATUS  # as tidy does for one job
            by_status[status] += count
            if status == DEFAULT_STATUS and outcome == STRONG_MATCH:
                strong_not_applied += count
        return {"total": sum(by_status.values()), "by_status": by_status, "strong_not_applied": strong_not_applied}

    def search_saved(self, keyword: str) -> list[dict]:
        """Query on the store itself: saved jobs matching every word in keyword."""
        return self._storage.search(keyword)

    @staticmethod
    def find_by_url(saved_jobs: list[dict], job_url: str) -> dict | None:
        """Query on a list already loaded: the job with this link, or None."""
        for job in saved_jobs:
            if job.get("job_url") == job_url:
                return job
        return None

    # ---------- update ----------

    def update_saved_job(self, job_url: str, status: str | None = None, note: str | None = None) -> bool:
        """Sets a saved job's status and/or note. Returns False if the job isn't saved. ValueError for a status
        that isn't one of STATUSES or a note longer than MAX_NOTE_CHARS."""
        changes = {}
        if status is not None:
            if status not in STATUSES:
                raise ValueError(f"Status must be one of {', '.join(STATUSES)}")
            changes["status"] = status
        if note is not None:
            note = note.strip()
            if len(note) > MAX_NOTE_CHARS:
                raise ValueError(f"A note can be at most {MAX_NOTE_CHARS} characters")
            changes["note"] = note
        if not changes:
            return self._storage.get(job_url) is not None
        return self._storage.update(job_url, changes)

    # ---------- delete ----------

    def remove_saved_job(self, job_url: str) -> bool:
        """Removes one saved job, found by its link. False if it wasn't saved."""
        return self._storage.delete(job_url)

    def clear_saved_jobs(self) -> None:
        """Removes every saved job."""
        self._storage.clear()
