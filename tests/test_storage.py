"""Tests for the storage backends (src/data_layer/storage.py, postgres_storage.py). One suite runs against an in-memory storage and
PostgresStorage (on a fake database), which also proves they can be substituted for each other (Liskov): the same calls
must give the same results."""

import threading

import pytest

from data_layer.data_manager import Storage
from fakes import FakePostgres, InMemoryStorage
from data_layer.postgres_storage import PostgresStorage


def make_job(number: int, **changes) -> dict:
    job = {"title": f"Data Analyst {number}", "company": "Acme Pte Ltd", "location": "Jurong",
           "job_url": f"https://www.mycareersfuture.gov.sg/job/it/data-analyst-{number:032x}"}
    job.update(changes)
    return job


def make_storage(kind: str) -> Storage:
    if kind == "memory":
        return InMemoryStorage()
    return PostgresStorage("fake", pool=FakePostgres())  # the real class and SQL strings, on a stand-in pool


@pytest.fixture(params=["memory", "postgres"])
def kind(request):
    return request.param


@pytest.fixture
def storage(kind) -> Storage:
    return make_storage(kind)


# ---------- the shared contract ----------

def test_a_new_store_is_empty(storage):
    assert storage.load_all() == []


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
