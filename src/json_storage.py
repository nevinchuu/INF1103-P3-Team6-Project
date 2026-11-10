"""JsonStorage: saved jobs in one JSON file (the Phase 1 behaviour)."""

import json
import os
import tempfile
import threading
import time
from collections.abc import Iterable

from storage import Storage

# How often to retry swapping in a new file when Windows reports it as locked
# (e.g. an antivirus or OneDrive scan has it open for a moment), and the wait between tries
REPLACE_ATTEMPTS = 5
REPLACE_WAIT_SECONDS = 0.2


class JsonStorage(Storage):
    """Saved jobs in one JSON file (the Phase 1 behaviour). A missing file is created, and a corrupt one is
    backed up and replaced, so a bad file never crashes the program."""

    def __init__(self, path: str) -> None:
        self._path = path
        self.__lock = threading.RLock()  # one read-change-write at a time

    @property
    def path(self) -> str:
        return self._path

    # ---------- the file ----------

    def __read(self) -> list[object]:
        """Reads the file. A missing one is created empty; a corrupt or non-list one is backed up, read as empty."""
        try:
            with open(self._path, 'r', encoding='utf-8') as file:
                contents = json.load(file)
        except FileNotFoundError:
            self.__write([])
            return []
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._back_up_broken_file(self._path, "not valid JSON")
            return []
        except OSError as error:
            # e.g. no permission to read: raised, so a save never overwrites a file it couldn't read
            raise OSError(f"Could not read {self._path} ({type(error).__name__}: {error})") from error

        if not isinstance(contents, list):  # valid JSON but not a list (e.g. edited by hand): same as corrupt
            self._back_up_broken_file(self._path, f"a JSON {type(contents).__name__}, not a list of jobs")
            return []
        return contents

    def __write(self, jobs: list[dict]) -> None:
        """Writes the jobs, numbering their IDs first. Written to a temporary file that is then swapped in,
        so a failed save never leaves a half-written file."""
        for index, job in enumerate(jobs):
            job["ID"] = index
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            file_descriptor, temp_path = tempfile.mkstemp(dir=os.path.dirname(self._path) or ".", suffix=".tmp")
        except OSError as error:
            raise OSError(f"Could not write {self._path} ({type(error).__name__}: {error})") from error
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as file:
                json.dump(jobs, file, indent=2)
            for attempt in range(1, REPLACE_ATTEMPTS + 1):
                try:
                    os.replace(temp_path, self._path)
                    break
                except PermissionError:
                    # Windows: another program has the file open for a moment; wait and try again
                    if attempt == REPLACE_ATTEMPTS:
                        raise
                    time.sleep(REPLACE_WAIT_SECONDS)
        except OSError as error:
            raise OSError(f"Could not write {self._path} ({type(error).__name__}: {error})") from error
        finally:
            if os.path.exists(temp_path):  # after a failed save, don't leave the temporary file behind
                os.remove(temp_path)

    def __jobs(self) -> list[dict]:
        """Every job record in the file, skipping entries that aren't job records or have no link."""
        return [job for job in self.__read() if self.has_link(job)]

    # ---------- the Storage interface ----------

    def load_all(self) -> list[dict]:
        with self.__lock:
            return self.__jobs()

    def get(self, job_url: str) -> dict | None:
        with self.__lock:
            return next((job for job in self.__jobs() if job["job_url"] == job_url), None)

    def insert(self, job: dict) -> bool:
        return self.insert_many([job]) == 1

    def insert_many(self, jobs: Iterable[dict]) -> int:
        with self.__lock:
            saved = self.__jobs()
            known_links = {job["job_url"] for job in saved}
            added = 0
            for job in jobs:
                self._check_new_job(job)
                if job["job_url"] in known_links:  # a duplicate: skipped
                    continue
                saved.append(dict(job))
                known_links.add(job["job_url"])
                added += 1
            if added:
                self.__write(saved)
            return added

    def update(self, job_url: str, changes: dict) -> bool:
        self._check_changes(changes)
        with self.__lock:
            saved = self.__jobs()
            job = next((job for job in saved if job["job_url"] == job_url), None)
            if job is None:
                return False
            job.update({key: value for key, value in changes.items() if key != "ID"})
            self.__write(saved)
            return True

    def delete(self, job_url: str) -> bool:
        with self.__lock:
            saved = self.__jobs()
            remaining = [job for job in saved if job["job_url"] != job_url]  # IDs are renumbered on write
            if len(remaining) == len(saved):
                return False
            self.__write(remaining)
            return True

    def clear(self) -> None:
        with self.__lock:
            self.__write([])
