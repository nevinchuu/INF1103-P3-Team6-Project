"""Tests for PostgresStorage (src/data_layer/postgres_storage.py) on a fake database: what the shared storage contract in
tests/test_storage.py cannot see (when the schema is made, what goes in the SQL text, how failures are reported)."""

import psycopg
import pytest
from fakes import FakePostgres, FlakyStorage, InMemoryStorage
from psycopg_pool import PoolTimeout

from data_layer import postgres_storage
from data_layer.data_manager import DataManager
from data_layer.data_store_error import DataStoreError
from data_layer.postgres_storage import COUNT_SQL, LOAD_ALL_SQL, PostgresStorage, SCHEMA_SQL, SEARCH_SQL


def make_job(number: int, **changes) -> dict:
    job = {"title": f"Data Analyst {number}", "company": "Acme Pte Ltd", "location": "Jurong",
           "job_url": f"https://www.mycareersfuture.gov.sg/job/it/data-analyst-{number:032x}"}
    job.update(changes)
    return job


@pytest.fixture
def database() -> FakePostgres:
    return FakePostgres()


@pytest.fixture
def storage(database) -> PostgresStorage:
    return PostgresStorage("fake", pool=database)


def test_the_schema_is_created_once_before_the_first_statement(storage, database):
    storage.load_all()
    storage.insert(make_job(1))
    storage.load_all()
    statements = [sql for sql, _ in database.statements]
    assert statements.count(SCHEMA_SQL) == 1 and statements[0] == SCHEMA_SQL
    assert database.opened


def test_values_are_parameters_never_part_of_the_sql_text(storage, database):
    attack = "'; DROP TABLE jobs; --"
    storage.insert(make_job(1, title=attack))
    storage.search(attack)
    storage.get(attack)
    storage.update(attack, {"note": attack})
    storage.delete(attack)
    for sql, params in database.statements:
        assert "DROP TABLE" not in sql
    searched = [params for sql, params in database.statements if sql.startswith(SEARCH_SQL)]
    assert searched == [tuple(f"%{word}%" for word in attack.split())]  # one parameter per search word


def test_search_words_have_their_wildcards_escaped(storage, database):
    storage.insert_many([make_job(1, title="100% sure"), make_job(2, title="Data_Analyst"), make_job(3, title="Plain")])
    assert [job["title"] for job in storage.search("100%")] == ["100% sure"]
    assert [job["title"] for job in storage.search("a_a")] == ["Data_Analyst"]
    assert [job["title"] for job in storage.search("back\\slash")] == []
    escaped = [params for sql, params in database.statements if sql.startswith(SEARCH_SQL)]
    assert escaped == [("%100\\%%",), ("%a\\_a%",), ("%back\\\\slash%",)]


def test_the_search_is_one_statement_with_one_condition_per_word(storage, database):
    storage.search("data analyst jurong")
    sql, params = database.statements[-1]
    assert sql.count("ILIKE") == 3 and len(params) == 3
    storage.search("")
    sql, params = database.statements[-1]
    assert "WHERE" not in sql and params == ()


def test_counting_is_done_by_the_database_and_loads_no_job(storage, database):
    storage.insert_many([make_job(1, status="Applied"), make_job(2)])
    database.statements.clear()
    assert storage.count_by_status_and_outcome() == {("Applied", ""): 1, ("", ""): 1}
    assert [sql for sql, _ in database.statements] == [COUNT_SQL]
    assert LOAD_ALL_SQL not in [sql for sql, _ in database.statements]


def test_the_columns_copy_the_fields_and_the_json_keeps_the_whole_job(storage, database):
    storage.insert(make_job(1, note=None, salary=4000, skills=["SQL"]))
    (row,) = database.rows
    assert row["title"] == "Data Analyst 1" and row["note"] is None and row["status"] is None
    assert row["data"]["salary"] == 4000 and "ID" not in row["data"]
    assert storage.load_all()[0]["skills"] == ["SQL"]


def test_json_text_from_the_database_is_read_like_a_dict(storage, database):
    storage.insert(make_job(1))
    database.rows[0]["data"] = '{"title": "As text", "job_url": "x"}'
    assert storage.load_all() == [{"title": "As text", "job_url": "x", "ID": 0}]


def test_a_job_that_json_cannot_hold_raises_type_error_and_sends_no_sql(storage, database):
    with pytest.raises(TypeError):
        storage.insert(make_job(1, salary=object()))
    assert database.statements == []


@pytest.mark.parametrize("error", [psycopg.OperationalError("down"), PoolTimeout("no connection in 5 seconds")])
def test_a_database_that_cannot_be_reached_raises_connection_error(storage, database, error):
    database.fail_with = error
    with pytest.raises(ConnectionError) as caught:
        storage.load_all()
    assert isinstance(caught.value, OSError) and caught.value.__cause__ is error
    assert type(error).__name__ in str(caught.value)
    assert storage.load_all() == []  # the next request tries again, and works


def test_a_database_that_is_down_becomes_a_data_store_error_through_the_data_manager(storage, database):
    data = DataManager(storage, portal=None)  # the portal is not used here
    database.fail_with = psycopg.OperationalError("down")
    with pytest.raises(DataStoreError) as caught:
        data.read_saved_jobs()
    assert isinstance(caught.value.__cause__, ConnectionError)


def test_a_sql_error_in_the_middle_of_a_save_is_a_connection_error(storage, database, monkeypatch):
    storage.load_all()
    monkeypatch.setattr(database, "execute", lambda sql, params=None: (_ for _ in ()).throw(
        psycopg.errors.UndefinedTable("no such table")))
    with pytest.raises(ConnectionError):
        storage.insert(make_job(1))


def test_without_a_pool_argument_a_real_pool_is_built_closed(monkeypatch):
    built = []

    class RecordingPool:
        def __init__(self, **options: object) -> None:
            built.append(options)

    monkeypatch.setattr(postgres_storage, "ConnectionPool", RecordingPool)
    PostgresStorage("postgresql://user@host/db")
    (options,) = built
    assert options["conninfo"] == "postgresql://user@host/db" and options["open"] is False
    assert options["max_size"] == postgres_storage.POOL_SIZE
    assert options["timeout"] == postgres_storage.POOL_TIMEOUT_SECONDS
