"""Tests for the storage backends (src/persistence/storage.py, json_storage.py, sqlite_storage.py). One suite runs against
JsonStorage and SqliteStorage, which also proves they can be substituted for each other (Liskov): the same calls
must give the same results."""

import glob
import json
import os
import sqlite3
import threading

import pytest

from persistence.data_manager import JsonStorage, SqliteStorage, Storage


def make_job(number: int, **changes) -> dict:
    job = {"title": f"Data Analyst {number}", "company": "Acme Pte Ltd", "location": "Jurong",
           "job_url": f"https://www.mycareersfuture.gov.sg/job/it/data-analyst-{number:032x}"}
    job.update(changes)
    return job


def make_storage(kind: str, folder) -> Storage:
    if kind == "json":
        return JsonStorage(str(folder / "data" / "job_listings.json"))
    return SqliteStorage(str(folder / "data" / "jobs.db"))


@pytest.fixture(params=["json", "sqlite"])
def kind(request):
    return request.param


@pytest.fixture
def storage(kind, tmp_path) -> Storage:
    return make_storage(kind, tmp_path)


# ---------- the shared contract ----------

def test_a_missing_store_is_created_empty(storage):
    assert storage.load_all() == []
    assert os.path.exists(storage.path)


def test_insert_saves_a_job_and_numbers_the_ids(storage):
    assert storage.insert(make_job(1)) is True
    assert storage.insert(make_job(2)) is True
    saved = storage.load_all()
    assert [(job["ID"], job["title"]) for job in saved] == [(0, "Data Analyst 1"), (1, "Data Analyst 2")]


def test_inserting_a_duplicate_link_is_refused_and_changes_nothing(storage):
    storage.insert(make_job(1, resume="first.pdf"))
    assert storage.insert(make_job(1, resume="second.pdf", title="Changed")) is False
    saved = storage.load_all()
    assert len(saved) == 1 and saved[0]["resume"] == "first.pdf" and saved[0]["title"] == "Data Analyst 1"


@pytest.mark.parametrize("bad", [{"title": "no link"}, make_job(1, job_url=""), make_job(1, job_url="N/A"),
                                 make_job(1, job_url=None)])
def test_inserting_a_job_without_a_usable_link_raises_value_error(storage, bad):
    with pytest.raises(ValueError):
        storage.insert(bad)
    assert storage.load_all() == []


def test_insert_many_counts_only_new_jobs_and_a_repeat_in_the_same_batch_once(storage):
    storage.insert(make_job(1))
    assert storage.insert_many([make_job(1), make_job(2), make_job(2, title="again"), make_job(3)]) == 2
    assert [job["title"] for job in storage.load_all()] == ["Data Analyst 1", "Data Analyst 2", "Data Analyst 3"]
    assert storage.insert_many([]) == 0


def test_get_finds_a_job_by_link(storage):
    storage.insert(make_job(1))
    assert storage.get(make_job(1)["job_url"])["title"] == "Data Analyst 1"
    assert storage.get("https://example.com/not-saved") is None


def test_update_changes_fields_and_keeps_the_rest(storage):
    storage.insert(make_job(1))
    assert storage.update(make_job(1)["job_url"], {"note": "Call on Monday", "status": "Applied"}) is True
    job = storage.get(make_job(1)["job_url"])
    assert job["note"] == "Call on Monday" and job["status"] == "Applied" and job["title"] == "Data Analyst 1"
    assert storage.update(make_job(1)["job_url"], {"note": ""}) is True
    assert storage.get(make_job(1)["job_url"])["note"] == ""


def test_update_of_a_missing_job_gives_false(storage):
    assert storage.update("https://example.com/missing", {"note": "x"}) is False
    assert storage.load_all() == []


def test_update_may_not_change_the_link(storage):
    storage.insert(make_job(1))
    with pytest.raises(ValueError):
        storage.update(make_job(1)["job_url"], {"job_url": "https://example.com/other"})


def test_update_ignores_an_attempt_to_set_the_id(storage):
    storage.insert_many([make_job(1), make_job(2)])
    storage.update(make_job(2)["job_url"], {"ID": 99})
    assert [job["ID"] for job in storage.load_all()] == [0, 1]


def test_delete_removes_one_job_and_renumbers_the_ids(storage):
    storage.insert_many([make_job(1), make_job(2), make_job(3)])
    assert storage.delete(make_job(2)["job_url"]) is True
    assert [(job["ID"], job["title"]) for job in storage.load_all()] == [(0, "Data Analyst 1"), (1, "Data Analyst 3")]


def test_delete_of_a_missing_job_gives_false(storage):
    storage.insert(make_job(1))
    assert storage.delete("https://example.com/missing") is False
    assert len(storage.load_all()) == 1


def test_clear_removes_everything_and_the_store_still_works(storage):
    storage.insert_many([make_job(1), make_job(2)])
    storage.clear()
    assert storage.load_all() == []
    assert storage.insert(make_job(1)) is True


def test_saved_jobs_are_still_there_in_a_new_run(kind, tmp_path):
    first = make_storage(kind, tmp_path)
    first.insert_many([make_job(1), make_job(2)])
    first.update(make_job(1)["job_url"], {"note": "kept"})
    second = make_storage(kind, tmp_path)  # a new object on the same file, as after restarting the program
    saved = second.load_all()
    assert [job["title"] for job in saved] == ["Data Analyst 1", "Data Analyst 2"]
    assert saved[0]["note"] == "kept"


def test_the_callers_job_is_not_changed_by_saving(storage):
    job = make_job(1)
    storage.insert(job)
    assert job == make_job(1)  # no "ID" added to it


def test_search_matches_every_word_ignoring_case(storage):
    storage.insert_many([make_job(1, title="Senior Data Analyst"), make_job(2, title="Data Engineer"),
                         make_job(3, title="Accountant", location="Data Centre, Jurong")])
    assert [job["title"] for job in storage.search("data analyst")] == ["Senior Data Analyst"]
    assert [job["title"] for job in storage.search("JURONG data")] == ["Senior Data Analyst", "Data Engineer",
                                                                      "Accountant"]
    assert storage.search("nurse") == []
    assert len(storage.search("")) == 3  # no words: everything matches


def test_search_ids_are_the_positions_in_the_whole_list(storage):
    storage.insert_many([make_job(1, title="Cook"), make_job(2, title="Data Engineer")])
    assert storage.search("engineer")[0]["ID"] == 1


@pytest.mark.parametrize("keyword", ["'; DROP TABLE jobs; --", "%", "_", "a' OR '1'='1", '"quoted"', "ünïcode"])
def test_odd_search_words_are_treated_as_plain_text(storage, keyword):
    storage.insert(make_job(1))
    assert storage.search(keyword) == []
    assert len(storage.load_all()) == 1  # nothing was dropped or matched by mistake


def test_a_link_that_looks_like_sql_is_saved_and_found_as_plain_text(storage):
    url = "https://www.mycareersfuture.gov.sg/job/x'); DROP TABLE jobs; --"
    storage.insert(make_job(1, job_url=url))
    assert storage.get(url)["job_url"] == url
    assert storage.delete(url) is True


def test_unicode_survives_a_round_trip(storage):
    storage.insert(make_job(1, title="Données analyste – 数据分析师"))
    assert storage.load_all()[0]["title"] == "Données analyste – 数据分析师"


def test_many_threads_saving_at_once_lose_nothing(storage):
    errors = []

    def save(number: int) -> None:
        try:
            storage.insert(make_job(number))
            storage.insert(make_job(number))  # a duplicate from the same thread, refused
        except Exception as error:  # pragma: no cover - the test fails below if this ever happens
            errors.append(error)

    threads = [threading.Thread(target=save, args=(number,)) for number in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    saved = storage.load_all()
    assert len(saved) == 20 and len({job["job_url"] for job in saved}) == 20
    assert sorted(job["ID"] for job in saved) == list(range(20))


def test_a_corrupt_file_is_backed_up_not_crashed(kind, tmp_path):
    storage = make_storage(kind, tmp_path)
    os.makedirs(os.path.dirname(storage.path), exist_ok=True)
    broken = b'[{"title": "half a reco' if kind == "json" else b"this is definitely not a SQLite database " * 40
    with open(storage.path, "wb") as file:
        file.write(broken)
    assert storage.load_all() == []  # starts again with an empty list...
    backups = glob.glob(storage.path + ".*.bak")
    assert len(backups) == 1
    with open(backups[0], "rb") as file:
        assert file.read() == broken  # ...and nothing the user had is lost
    assert storage.insert(make_job(1)) is True  # and the new store works


def test_two_corrupt_files_in_the_same_second_each_keep_a_backup(kind, tmp_path):
    storage = make_storage(kind, tmp_path)
    os.makedirs(os.path.dirname(storage.path), exist_ok=True)
    for number in range(3):
        with open(storage.path, "wb") as file:
            file.write(b"{broken %d " % number + b"x" * 200)
        assert storage.load_all() == []
    assert len(glob.glob(storage.path + ".*.bak")) == 3


def test_a_store_that_cannot_be_opened_raises_storage_error_which_is_an_os_error(kind, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a folder is needed")
    path = str(blocker / "sub" / ("jobs.db" if kind == "sqlite" else "job_listings.json"))
    storage = SqliteStorage(path) if kind == "sqlite" else JsonStorage(path)
    with pytest.raises(OSError) as caught:
        storage.load_all()
    assert isinstance(caught.value, OSError)


# ---------- JSON-only behaviour ----------

def write_raw(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        file.write(text)


@pytest.mark.parametrize("text", ['{"jobs": []}', "null", "42"])
def test_json_that_is_not_a_list_is_treated_as_corrupt(tmp_path, text):
    storage = make_storage("json", tmp_path)
    write_raw(storage.path, text)
    assert storage.load_all() == []
    assert len(glob.glob(storage.path + ".*.bak")) == 1


def test_json_entries_that_are_not_jobs_are_skipped_and_dropped_on_the_next_save(tmp_path):
    storage = make_storage("json", tmp_path)
    write_raw(storage.path, json.dumps([1, "x", None, {"title": "no link"}, make_job(1)]))
    assert [job["title"] for job in storage.load_all()] == ["Data Analyst 1"]
    assert storage.insert(make_job(2)) is True
    with open(storage.path, encoding="utf-8") as file:
        assert all(isinstance(entry, dict) and "job_url" in entry for entry in json.load(file))


def test_a_save_that_fails_leaves_the_old_data_and_no_temporary_file(storage):
    storage.insert(make_job(1))
    with pytest.raises(TypeError):
        storage.insert(make_job(2, salary=object()))  # a value that cannot be stored
    assert [job["title"] for job in storage.load_all()] == ["Data Analyst 1"]
    assert glob.glob(os.path.join(os.path.dirname(storage.path), "*.tmp")) == []


# ---------- SQLite-only behaviour ----------

def test_sqlite_has_a_unique_key_on_the_link(tmp_path):
    storage = make_storage("sqlite", tmp_path)
    storage.insert(make_job(1))
    connection = sqlite3.connect(storage.path)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO jobs (job_url, data) VALUES (?, ?)", (make_job(1)["job_url"], "{}"))
    connection.close()


def test_sqlite_recreates_a_database_deleted_while_running(tmp_path):
    storage = make_storage("sqlite", tmp_path)
    storage.insert(make_job(1))
    os.remove(storage.path)
    assert storage.load_all() == []
    assert storage.insert(make_job(1)) is True


def test_a_locked_sqlite_database_raises_storage_error_instead_of_crashing(tmp_path):
    storage = SqliteStorage(str(tmp_path / "jobs.db"), busy_seconds=0.2)
    storage.insert(make_job(1))
    holder = sqlite3.connect(storage.path)
    holder.execute("BEGIN EXCLUSIVE")  # another program has the database locked
    try:
        with pytest.raises(OSError):
            storage.insert(make_job(2))
        with pytest.raises(OSError):
            storage.load_all()
    finally:
        holder.rollback()
        holder.close()
    assert [job["title"] for job in storage.load_all()] == ["Data Analyst 1"]  # fine again once it is released
    assert glob.glob(storage.path + ".*.bak") == []  # a lock is not corruption: nothing was backed up


def test_sqlite_is_not_slow_for_a_few_hundred_jobs(tmp_path):
    storage = make_storage("sqlite", tmp_path)
    assert storage.insert_many([make_job(number) for number in range(300)]) == 300
    assert len(storage.search("analyst 29")) == 13  # 29, 129, 229 and 290-299
