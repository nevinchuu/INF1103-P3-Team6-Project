"""SqliteStorage: saved jobs in a SQLite database (the advanced feature)."""

import json
import os
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager

from persistence.storage import Storage

SQLITE_BUSY_SECONDS = 5  # how long SQLite waits for another connection's lock before giving up


class SqliteStorage(Storage):
    """Saved jobs in a SQLite database: full CRUD with parameterised queries only, a unique key on the job
    link, and a connection per operation behind a lock so the background search threads can save at the same
    time. A missing database is created; a corrupt one is backed up and replaced; a locked one raises
    OSError instead of crashing."""

    _SCHEMA = """CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_url TEXT NOT NULL UNIQUE,
        title TEXT, company TEXT, location TEXT, resume TEXT,
        note TEXT, status TEXT,
        data TEXT NOT NULL,
        saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"""

    def __init__(self, path: str, busy_seconds: float = SQLITE_BUSY_SECONDS) -> None:
        self._path = path
        self._busy_seconds = busy_seconds
        self.__lock = threading.RLock()  # SQLite allows one writer at a time anyway; this keeps our threads polite

    @property
    def path(self) -> str:
        return self._path

    # ---------- the database ----------

    def __connect(self) -> sqlite3.Connection:
        """A new connection with the table in place (created if the database or table is missing)."""
        connection = sqlite3.connect(self._path, timeout=self._busy_seconds)
        try:
            connection.row_factory = sqlite3.Row
            # Lower-casing done in Python, so a keyword search treats every letter exactly as JsonStorage does
            connection.create_function("py_lower", 1, lambda text: str(text or "").lower(), deterministic=True)
            connection.execute(self._SCHEMA)
            connection.execute("SELECT count(*) FROM jobs").fetchone()  # fails on a damaged file
        except BaseException:
            connection.close()
            raise
        return connection

    @contextmanager
    def __session(self) -> Iterator[sqlite3.Connection]:
        """One thread-safe unit of work: a connection that commits on success and rolls back on an error."""
        with self.__lock:
            try:
                os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
                connection = self.__open()
                try:
                    with connection:  # commit, or roll back if the block raised
                        yield connection
                finally:
                    connection.close()
            except OSError as error:  # e.g. the folder can't be created
                raise OSError(f"Could not use {self._path} ({type(error).__name__}: {error})") from error
            except sqlite3.OperationalError as error:
                # Locked by another program, no permission, disk full...: reported, and the file is left alone
                raise OSError(f"Could not use {self._path} ({error})") from error
            except sqlite3.DatabaseError as error:
                raise OSError(f"The saved jobs database {self._path} is unusable ({error})") from error

    def __open(self) -> sqlite3.Connection:
        """A connection; a corrupt database file is backed up once and a new one started."""
        try:
            return self.__connect()
        except sqlite3.OperationalError:
            raise  # locked / unreadable: not corruption, so the file is not touched
        except sqlite3.DatabaseError as error:
            if not os.path.exists(self._path):
                raise
            self._back_up_broken_file(self._path, f"not a usable database ({error})")
            return self.__connect()

    # ---------- converting rows and jobs ----------

    @staticmethod
    def __to_job(row: sqlite3.Row, position: int) -> dict:
        job = json.loads(row["data"])
        job["ID"] = position
        return job

    @staticmethod
    def __columns(job: dict) -> tuple:
        """The searchable columns of a job (the full record is kept as JSON in data)."""
        return tuple(None if job.get(key) is None else str(job.get(key))
                     for key in ("title", "company", "location", "resume", "note", "status"))

    @staticmethod
    def __data(job: dict) -> str:
        # No default= here: a value JSON cannot hold raises TypeError, exactly as JsonStorage does
        return json.dumps({key: value for key, value in job.items() if key != "ID"})

    # ---------- the Storage interface ----------

    def load_all(self) -> list[dict]:
        with self.__session() as connection:
            rows = connection.execute("SELECT data FROM jobs ORDER BY id").fetchall()
        return [self.__to_job(row, position) for position, row in enumerate(rows)]

    def get(self, job_url: str) -> dict | None:
        return next((job for job in self.load_all() if job["job_url"] == job_url), None)

    def insert(self, job: dict) -> bool:
        return self.insert_many([job]) == 1

    def insert_many(self, jobs: Iterable[dict]) -> int:
        jobs = list(jobs)
        for job in jobs:
            self._check_new_job(job)
        with self.__session() as connection:
            added = 0
            for job in jobs:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO jobs (job_url, title, company, location, resume, note, status, data) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (job["job_url"], *self.__columns(job), self.__data(job)))
                added += cursor.rowcount  # 0 when the unique link was already saved
            return added

    def update(self, job_url: str, changes: dict) -> bool:
        self._check_changes(changes)
        with self.__session() as connection:
            row = connection.execute("SELECT data FROM jobs WHERE job_url = ?", (job_url,)).fetchone()
            if row is None:
                return False
            job = json.loads(row["data"])
            job.update({key: value for key, value in changes.items() if key != "ID"})
            connection.execute(
                "UPDATE jobs SET title = ?, company = ?, location = ?, resume = ?, note = ?, status = ?, data = ? "
                "WHERE job_url = ?", (*self.__columns(job), self.__data(job), job_url))
            return True

    def delete(self, job_url: str) -> bool:
        with self.__session() as connection:
            return connection.execute("DELETE FROM jobs WHERE job_url = ?", (job_url,)).rowcount > 0

    def clear(self) -> None:
        with self.__session() as connection:
            connection.execute("DELETE FROM jobs")

    def count_by_status_and_outcome(self) -> dict[tuple[str, str], int]:
        # The database does the counting: one GROUP BY over the status column and the outcome stored in each job's
        # JSON, instead of loading every job into Python
        with self.__session() as connection:
            rows = connection.execute(
                "SELECT coalesce(status, '') AS status, coalesce(json_extract(data, '$.match_outcome'), '') AS outcome, "
                "count(*) AS jobs FROM jobs GROUP BY 1, 2").fetchall()
        return {(str(row["status"]), str(row["outcome"])): row["jobs"] for row in rows}

    def search(self, keyword: str) -> list[dict]:
        words = keyword.lower().split()
        text = "py_lower(coalesce(title, '') || ' ' || coalesce(company, '') || ' ' || coalesce(location, ''))"
        # One parameterised condition per word, so the keyword never becomes part of the SQL text
        where = " AND ".join(f"instr({text}, ?) > 0" for _ in words) or "1"
        with self.__session() as connection:
            rows = connection.execute(f"SELECT id, data FROM jobs WHERE {where} ORDER BY id", words).fetchall()
            positions = {row["id"]: position for position, row in enumerate(
                connection.execute("SELECT id FROM jobs ORDER BY id").fetchall())}
        return [self.__to_job(row, positions[row["id"]]) for row in rows]
