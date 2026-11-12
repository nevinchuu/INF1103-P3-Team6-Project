"""Tests for the data layer (src/persistence/data_manager.py): saving, loading, querying and updating jobs.

DataManager is tested with both storage backends (JSON file and SQLite), each in its own temporary folder, so the
real data folder is never touched.
"""

import json
import os

import pytest

from persistence.data_manager import DataManager, JsonStorage, MAX_NOTE_CHARS, SqliteStorage, STATUSES
from fakes import FlakyStorage


def make_job(number: int, **changes) -> dict:
    job = {"title": f"Data Analyst {number}", "company": "Acme Pte Ltd", "location": "Jurong",
           "job_url": f"https://www.mycareersfuture.gov.sg/job/it/data-analyst-{number:032x}"}
    job.update(changes)
    return job


@pytest.fixture(params=["json", "sqlite"])
def data(request, tmp_path) -> DataManager:
    if request.param == "json":
        return DataManager(JsonStorage(str(tmp_path / "job_listings.json")))
    return DataManager(SqliteStorage(str(tmp_path / "jobs.db")))


def test_saving_keeps_jobs_between_runs_adds_only_new_links_notes_the_resume_and_numbers_the_ids(data):
    assert data.save_new_jobs([make_job(1), make_job(2)], "resume_a.pdf") == 2
    # Job 2 is already saved, so only job 3 is new; job 2 keeps the resume that first found it
    assert data.save_new_jobs([make_job(2), make_job(3)], "resume_b.pdf") == 1
    saved = data.read_saved_jobs()
    assert [job["title"] for job in saved] == ["Data Analyst 1", "Data Analyst 2", "Data Analyst 3"]
    assert [job["resume"] for job in saved] == ["resume_a.pdf", "resume_a.pdf", "resume_b.pdf"]
    assert [job["ID"] for job in saved] == [0, 1, 2]
    assert data.save_new_jobs([], "resume_a.pdf") == 0


def test_saving_does_not_change_the_callers_records(data):
    jobs = [make_job(1), make_job(2)]
    data.save_new_jobs(jobs, "resume.pdf")
    assert jobs == [make_job(1), make_job(2)]


def test_the_same_link_twice_in_one_save_counts_once(data):
    assert data.save_new_jobs([make_job(1), make_job(1, title="Data Analyst 1 again")], "resume.pdf") == 1
    assert [job["title"] for job in data.read_saved_jobs()] == ["Data Analyst 1"]


def test_jobs_without_a_link_are_not_saved(data):
    jobs = [{"title": "No link at all"}, make_job(1, job_url=""), make_job(2, job_url="N/A"), make_job(3)]
    assert data.save_new_jobs(jobs, "resume.pdf") == 1
    assert [job["job_url"] for job in data.read_saved_jobs()] == [make_job(3)["job_url"]]


def test_find_by_url_on_a_loaded_list_and_in_the_store(data):
    jobs = [make_job(1), make_job(2)]
    assert data.find_by_url(jobs, make_job(2)["job_url"])["title"] == "Data Analyst 2"
    assert data.find_by_url(jobs, "https://example.com/not-saved") is None
    data.save_new_jobs(jobs, "resume.pdf")
    assert data.get_saved_job(make_job(1)["job_url"])["title"] == "Data Analyst 1"
    assert data.get_saved_job("https://example.com/not-saved") is None


def test_search_jobs_on_a_list_and_search_saved_in_the_store_agree(data):
    jobs = [make_job(1, title="Senior Data Analyst"), make_job(2, title="Data Engineer"),
            make_job(3, title="Accountant", location="Data Centre, Jurong")]
    data.save_new_jobs(jobs, "resume.pdf")
    for keyword in ("data analyst", "JURONG data", "nurse", "accountant"):
        assert [job["title"] for job in data.search_jobs(jobs, keyword)] == \
               [job["title"] for job in data.search_saved(keyword)]
    assert [job["title"] for job in data.search_jobs(jobs, "data analyst")] == ["Senior Data Analyst"]


def test_remove_one_job_and_clear_all_renumber_the_ids(data):
    data.save_new_jobs([make_job(1), make_job(2), make_job(3)], "resume.pdf")
    assert data.remove_saved_job(make_job(2)["job_url"]) is True
    assert [(job["ID"], job["title"]) for job in data.read_saved_jobs()] == [(0, "Data Analyst 1"),
                                                                            (1, "Data Analyst 3")]
    assert data.remove_saved_job(make_job(2)["job_url"]) is False  # already gone: nothing happens, no error
    data.clear_saved_jobs()
    assert data.read_saved_jobs() == []


def test_a_status_and_a_note_can_be_set_on_a_saved_job(data):
    data.save_new_jobs([make_job(1)], "resume.pdf")
    url = make_job(1)["job_url"]
    assert data.update_saved_job(url, status="Applied", note="  Emailed the recruiter  ") is True
    job = data.get_saved_job(url)
    assert (job["status"], job["note"]) == ("Applied", "Emailed the recruiter")
    assert data.update_saved_job(url, note="Second note") is True  # only the note changes
    assert data.get_saved_job(url)["status"] == "Applied"


def test_updating_a_job_that_is_not_saved_gives_false(data):
    assert data.update_saved_job("https://example.com/missing", status="Applied") is False
    assert data.update_saved_job("https://example.com/missing") is False


def test_updating_with_nothing_to_change_only_checks_the_job_exists(data):
    data.save_new_jobs([make_job(1)], "resume.pdf")
    assert data.update_saved_job(make_job(1)["job_url"]) is True


@pytest.mark.parametrize("changes", [{"status": "Hired by magic"}, {"status": ""}, {"note": "x" * (MAX_NOTE_CHARS + 1)}])
def test_an_invalid_status_or_note_is_refused(data, changes):
    data.save_new_jobs([make_job(1)], "resume.pdf")
    with pytest.raises(ValueError):
        data.update_saved_job(make_job(1)["job_url"], **changes)
    assert "status" not in data.get_saved_job(make_job(1)["job_url"])


def test_every_listed_status_is_accepted(data):
    data.save_new_jobs([make_job(1)], "resume.pdf")
    for status in STATUSES:
        assert data.update_saved_job(make_job(1)["job_url"], status=status) is True


def test_an_unreachable_store_raises_an_os_error_the_apps_already_handle(tmp_path):
    flaky = FlakyStorage(JsonStorage(str(tmp_path / "job_listings.json")))
    data = DataManager(flaky)
    flaky.fail_reads = True
    with pytest.raises(OSError):
        data.read_saved_jobs()
    flaky.fail_reads, flaky.fail_writes = False, True
    with pytest.raises(OSError):
        data.save_new_jobs([make_job(1)], "resume.pdf")


# ---------- choosing the backend ----------

def test_the_json_backend_is_the_default(monkeypatch, tmp_path):
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    monkeypatch.setenv("JOBS_DB_PATH", str(tmp_path / "saved.json"))
    manager = DataManager.from_environment()
    assert isinstance(manager.storage, JsonStorage)
    manager.save_new_jobs([make_job(1)], "resume.pdf")
    with open(tmp_path / "saved.json", encoding="utf-8") as file:
        assert json.load(file)[0]["title"] == "Data Analyst 1"


def test_the_sqlite_backend_is_chosen_by_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_BACKEND", " SQLite ")
    monkeypatch.setenv("JOBS_DB_PATH", str(tmp_path / "nested" / "jobs.db"))
    manager = DataManager.from_environment()
    assert isinstance(manager.storage, SqliteStorage)
    assert manager.read_saved_jobs() == []
    assert os.path.exists(tmp_path / "nested" / "jobs.db")  # created, with its folder


# A saved job from an older version or a hand-edited file still loads in a shape the pages can show
def test_saved_jobs_with_wrong_field_types_are_tidied_on_read(data):
    job = {**make_job(1), "required_skills": None, "matched_skills": "abc", "missing_skills": 5, "status": "Weird"}
    data.save_new_jobs([job], "resume.pdf")
    loaded = data.read_saved_jobs()[0]
    assert loaded["required_skills"] == [] and loaded["matched_skills"] == [] and loaded["missing_skills"] == []
    assert loaded["status"] == "Saved" and data.get_saved_job(job["job_url"])["matched_skills"] == []


# The application tracker: how many saved jobs are at each stage, counted by the storage (SQL GROUP BY on SQLite)
def test_the_tracker_counts_jobs_by_status_the_same_way_on_both_backends(data):
    data.save_new_jobs([make_job(1, match_outcome="Strong match"), make_job(2, match_outcome="Strong match"),
                        make_job(3, match_outcome="Fair match"), make_job(4)], "resume.pdf")
    data.update_saved_job(make_job(2)["job_url"], status="Applied")
    data.update_saved_job(make_job(3)["job_url"], status="Interviewing")
    summary = data.tracker_summary()
    assert summary["total"] == 4
    assert summary["by_status"] == {"Saved": 2, "Applied": 1, "Interviewing": 1, "Offer": 0, "Rejected": 0}
    assert list(summary["by_status"]) == STATUSES  # always every stage, in pipeline order
    assert summary["strong_not_applied"] == 1  # job 1: a strong match still only saved


def test_the_tracker_of_an_empty_store_is_all_zeros(data):
    assert data.tracker_summary() == {"total": 0, "by_status": {status: 0 for status in STATUSES},
                                      "strong_not_applied": 0}


def test_sqlite_counts_in_the_database_and_agrees_with_the_shared_python_count(tmp_path):
    sqlite = SqliteStorage(str(tmp_path / "jobs.db"))
    for number, outcome in enumerate(["Strong match", "Strong match", "Stretch", None]):
        sqlite.insert(make_job(number, match_outcome=outcome) if outcome else make_job(number))
    sqlite.update(make_job(1)["job_url"], {"status": "Applied"})
    counted_by_sql = sqlite.count_by_status_and_outcome()
    counted_in_python = super(SqliteStorage, sqlite).count_by_status_and_outcome()  # Storage's shared version
    assert counted_by_sql == counted_in_python == {("", "Strong match"): 1, ("Applied", "Strong match"): 1,
                                                   ("", "Stretch"): 1, ("", ""): 1}
