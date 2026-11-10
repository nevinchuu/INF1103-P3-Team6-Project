"""Storage: the abstract class every saved-jobs backend inherits (JsonStorage, SqliteStorage)."""

import logging
import os
import time
from abc import ABC, abstractmethod
from collections.abc import Iterable


class Storage(ABC):
    """Abstract class: a store of saved jobs, each found by its unique job_url. The abstract methods are what every
    backend must do its own way; insert_many, search and the checks are shared behaviour every backend inherits.

    insert, update and delete say whether they changed anything (False for a duplicate or a missing job) rather
    than raising; only a store that cannot be reached raises OSError.
    """

    @staticmethod
    def has_link(job: object) -> bool:
        """True if the job has a link to save it by; "N/A" is the portal layer's placeholder for a missing one."""
        return isinstance(job, dict) and isinstance(job.get("job_url"), str) and job["job_url"] not in ("", "N/A")

    @staticmethod
    def _back_up_broken_file(path: str, reason: str) -> None:
        """Keeps a broken storage file as a dated backup, e.g. job_listings.json.20261009-153000.bak, never
        overwriting one, so a fresh file can be started."""
        timestamp = time.strftime('%Y%m%d-%H%M%S')
        backup_path = f"{path}.{timestamp}.bak"
        backup_number = 1
        while os.path.exists(backup_path):  # two broken files in the same second each keep their own backup
            backup_number += 1
            backup_path = f"{path}.{timestamp}-{backup_number}.bak"
        os.replace(path, backup_path)
        logging.warning(f"Saved jobs file was {reason}; kept it as {backup_path} and started a new one")

    @abstractmethod
    def load_all(self) -> list[dict]:
        """Every saved job, oldest first, each with an "ID" (its position, from 0)."""

    @abstractmethod
    def get(self, job_url: str) -> dict | None:
        """The saved job with this link, or None."""

    @abstractmethod
    def insert(self, job: dict) -> bool:
        """Saves a new job. False if a job with the same link is already saved. ValueError if it has no link."""

    @abstractmethod
    def update(self, job_url: str, changes: dict) -> bool:
        """Changes fields of the saved job with this link. False if there is no such job. The link itself
        cannot be changed (ValueError)."""

    @abstractmethod
    def delete(self, job_url: str) -> bool:
        """Removes the saved job with this link. False if there is no such job."""

    @abstractmethod
    def clear(self) -> None:
        """Removes every saved job."""

    def insert_many(self, jobs: Iterable[dict]) -> int:
        """Saves every job not saved yet; returns how many were new. Shared behaviour: backends that can do it
        in one step (one file write, one transaction) override this."""
        return sum(self.insert(job) for job in jobs)

    def search(self, keyword: str) -> list[dict]:
        """Saved jobs whose title, company or location contain every word in keyword, ignoring case.
        Shared behaviour: a backend that can search faster itself overrides this."""
        words = keyword.lower().split()
        results = []
        for job in self.load_all():
            text = " ".join(str(job.get(field, "")) for field in ("title", "company", "location")).lower()
            if all(word in text for word in words):
                results.append(job)
        return results

    def count_by_status_and_outcome(self) -> dict[tuple[str, str], int]:
        """How many saved jobs have each (status, match outcome), e.g. {("Applied", "Strong match"): 2}; a job with
        no status counts as "" and one with no outcome as "". Shared behaviour: SqliteStorage counts with SQL."""
        counts: dict[tuple[str, str], int] = {}
        for job in self.load_all():
            key = (str(job.get("status") or ""), str(job.get("match_outcome") or ""))
            counts[key] = counts.get(key, 0) + 1
        return counts

    @staticmethod
    def _check_changes(changes: dict) -> None:
        """A saved job's link and ID are its identity: update may not change them."""
        if "job_url" in changes:
            raise ValueError("A saved job's link cannot be changed")

    @staticmethod
    def _check_new_job(job: dict) -> None:
        if not Storage.has_link(job):
            raise ValueError("A job needs a link to be saved")
