"""PostgresStorage: saved jobs in a PostgreSQL database, through a pool of connections."""

import json
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from data_layer.storage import Storage

POOL_SIZE = 5
POOL_TIMEOUT_SECONDS = 5  # how long a request waits for the database before the Saved page says it is down

# Every statement is a constant, so a test can tell which one ran. Values are always passed as %s parameters,
# never formatted into the text, so a job title or a search word can't change what the statement does.
SCHEMA_SQL = """CREATE TABLE IF NOT EXISTS jobs (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_url       TEXT NOT NULL UNIQUE,
    title         TEXT, company TEXT, location TEXT, resume TEXT,
    note          TEXT, status TEXT, match_outcome TEXT,
    data          JSONB NOT NULL,
    saved_at      TIMESTAMPTZ NOT NULL DEFAULT now())"""
LOAD_ALL_SQL = "SELECT data FROM jobs ORDER BY id"
GET_SQL = ("SELECT data, (SELECT count(*) FROM jobs AS older WHERE older.id < jobs.id) AS position "
           "FROM jobs WHERE job_url = %s")
INSERT_SQL = ("INSERT INTO jobs (job_url, title, company, location, resume, note, status, match_outcome, data) "
              "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) ON CONFLICT (job_url) DO NOTHING")
SELECT_FOR_UPDATE_SQL = "SELECT data FROM jobs WHERE job_url = %s FOR UPDATE"
UPDATE_SQL = ("UPDATE jobs SET title = %s, company = %s, location = %s, resume = %s, note = %s, status = %s, "
              "match_outcome = %s, data = %s::jsonb WHERE job_url = %s")
DELETE_SQL = "DELETE FROM jobs WHERE job_url = %s"
CLEAR_SQL = "DELETE FROM jobs"
COUNT_SQL = ("SELECT coalesce(status, '') AS status, coalesce(match_outcome, '') AS outcome, count(*) AS jobs "
             "FROM jobs GROUP BY 1, 2")
SEARCH_SQL = ("SELECT data, position FROM (SELECT id, data, coalesce(title, '') || ' ' || coalesce(company, '') "
              "|| ' ' || coalesce(location, '') AS text, row_number() OVER (ORDER BY id) - 1 AS position "
              "FROM jobs) AS numbered")
SEARCH_WORD_SQL = "text ILIKE %s ESCAPE '\\'"
SEARCH_ORDER_SQL = " ORDER BY id"
COLUMNS = ("title", "company", "location", "resume", "note", "status", "match_outcome")  # copies of fields in data


class PostgresStorage(Storage):
    """Saved jobs in one PostgreSQL table. Each job is kept whole as JSON, with a few fields copied into columns
    so the database can search and count them. A database that can't be reached raises ConnectionError (an
    OSError), which DataManager reports as DataStoreError; no psycopg error leaves this file."""

    def __init__(self, dsn: str, pool: ConnectionPool | None = None) -> None:
        self.__dsn = dsn
        # The pool is this storage's own; a test passes a stand-in so no database is needed. open=False: nothing
        # connects at import or start-up, so the app starts with the database down
        self.__pool = pool if pool is not None else ConnectionPool(
            conninfo=dsn, min_size=1, max_size=POOL_SIZE, timeout=POOL_TIMEOUT_SECONDS,
            open=False, kwargs={"row_factory": dict_row})
        self.__schema_ready = False
        self.__schema_lock = threading.Lock()  # web searches run in background threads

    # ---------- the connection ----------

    @contextmanager
    def __connect(self) -> Iterator[Connection]:
        """One unit of work on a pooled connection: commits on success, rolls back on an error."""
        try:
            self.__pool.open()  # does nothing once open
            self.__create_schema()
            with self.__pool.connection() as connection:
                yield connection
        except psycopg.Error as error:  # PoolTimeout, OperationalError, ... are all psycopg.Error
            raise ConnectionError(f"Could not use the jobs database ({type(error).__name__}: {error})") from error

    def __create_schema(self) -> None:
        """Creates the table the first time the database is reached."""
        with self.__schema_lock:
            if self.__schema_ready:
                return
            with self.__pool.connection() as connection:
                connection.execute(SCHEMA_SQL)
            self.__schema_ready = True

    # ---------- turning a job into rows and back ----------

    @staticmethod
    def __columns(job: dict) -> tuple[str | None, ...]:
        return tuple(None if job.get(key) is None else str(job.get(key)) for key in COLUMNS)

    @staticmethod
    def __data_text(job: dict) -> str:
        """The whole job as JSON text, without its "ID" (that is its position, worked out when it is read).
        Raises TypeError for a value JSON can't hold, before any SQL runs."""
        return json.dumps({key: value for key, value in job.items() if key != "ID"})

    @staticmethod
    def __job(data: dict | str, position: int) -> dict:
        """A job as read from the data column (a dict, or JSON text), with its "ID"."""
        job = json.loads(data) if isinstance(data, str) else dict(data)
        return {**job, "ID": int(position)}

    # ---------- the Storage interface ----------

    def load_all(self) -> list[dict]:
        with self.__connect() as connection:
            rows = connection.execute(LOAD_ALL_SQL).fetchall()
        return [self.__job(row["data"], position) for position, row in enumerate(rows)]

    def get(self, job_url: str) -> dict | None:
        with self.__connect() as connection:
            row = connection.execute(GET_SQL, (job_url,)).fetchone()
        return None if row is None else self.__job(row["data"], row["position"])

    def insert(self, job: dict) -> bool:
        return self.insert_many([job]) == 1

    def insert_many(self, jobs: Iterable[dict]) -> int:
        new_jobs = list(jobs)
        for job in new_jobs:
            self._check_new_job(job)
        rows = [(job["job_url"], *self.__columns(job), self.__data_text(job)) for job in new_jobs]
        added = 0
        with self.__connect() as connection:
            for row in rows:
                added += connection.execute(INSERT_SQL, row).rowcount
        return added

    def update(self, job_url: str, changes: dict) -> bool:
        self._check_changes(changes)
        with self.__connect() as connection:
            row = connection.execute(SELECT_FOR_UPDATE_SQL, (job_url,)).fetchone()
            if row is None:
                return False
            job = self.__job(row["data"], 0)
            job.update({key: value for key, value in changes.items() if key != "ID"})
            connection.execute(UPDATE_SQL, (*self.__columns(job), self.__data_text(job), job_url))
            return True

    def delete(self, job_url: str) -> bool:
        with self.__connect() as connection:
            return connection.execute(DELETE_SQL, (job_url,)).rowcount > 0

    def clear(self) -> None:
        with self.__connect() as connection:
            connection.execute(CLEAR_SQL)

    def count_by_status_and_outcome(self) -> dict[tuple[str, str], int]:
        """The database counts with GROUP BY; no job is loaded."""
        with self.__connect() as connection:
            rows = connection.execute(COUNT_SQL).fetchall()
        return {(row["status"], row["outcome"]): row["jobs"] for row in rows}

    def search(self, keyword: str) -> list[dict]:
        """Saved jobs whose title, company or location contain every word in keyword, matched by the database."""
        words = keyword.split()
        sql = SEARCH_SQL
        if words:
            sql += " WHERE " + " AND ".join(SEARCH_WORD_SQL for _ in words)
        sql += SEARCH_ORDER_SQL
        # % and _ are wildcards in ILIKE, so a typed one is escaped to mean itself
        params = tuple("%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                       for word in words)
        with self.__connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self.__job(row["data"], row["position"]) for row in rows]
