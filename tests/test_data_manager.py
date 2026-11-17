"""Tests for the data layer (src/data_layer/data_manager.py): saving, loading, querying and updating jobs.

DataManager is tested on an in-memory storage, so no file, database or real data folder is touched.
"""

import pytest

from data_layer import data_manager
from data_layer.data_manager import DataManager, STATUSES
from data_layer.data_store_error import DataStoreError
from fakes import FakePortal, FlakyStorage, InMemoryStorage


def make_job(number: int, **changes) -> dict:
    job = {"title": f"Data Analyst {number}", "company": "Acme Pte Ltd", "location": "Jurong",
           "job_url": f"https://www.mycareersfuture.gov.sg/job/it/data-analyst-{number:032x}"}
    job.update(changes)
    return job


@pytest.fixture
def data() -> DataManager:
    return DataManager(InMemoryStorage(), FakePortal([]))


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


def test_a_saved_job_is_found_by_its_link(data):
    data.save_new_jobs([make_job(1), make_job(2)], "resume.pdf")
    assert data.get_saved_job(make_job(1)["job_url"])["title"] == "Data Analyst 1"
    assert data.get_saved_job("https://example.com/not-saved") is None


def test_search_saved_needs_every_word_in_the_title_company_or_location(data):
    data.save_new_jobs([make_job(1, title="Senior Data Analyst"), make_job(2, title="Data Engineer"),
                        make_job(3, title="Accountant", location="Data Centre, Jurong")], "resume.pdf")
    found = lambda keyword: [job["title"] for job in data.search_saved(keyword)]
    assert found("data analyst") == ["Senior Data Analyst"]
    assert found("CENTRE data") == ["Accountant"] and found("nurse") == []
    assert len(found("")) == 3


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


def test_every_listed_status_is_accepted(data):
    data.save_new_jobs([make_job(1)], "resume.pdf")
    for status in STATUSES:
        assert data.update_saved_job(make_job(1)["job_url"], status=status) is True


URL = make_job(1)["job_url"]
READS = {
    "read_saved_jobs": lambda data: data.read_saved_jobs(),
    "get_saved_job": lambda data: data.get_saved_job(URL),
    "search_saved": lambda data: data.search_saved("analyst"),
    "tracker_summary": lambda data: data.tracker_summary(),
}
WRITES = {
    "save_new_jobs": lambda data: data.save_new_jobs([make_job(1)], "resume.pdf"),
    "update_saved_job": lambda data: data.update_saved_job(URL, status="Applied"),
    "remove_saved_job": lambda data: data.remove_saved_job(URL),
    "clear_saved_jobs": lambda data: data.clear_saved_jobs(),
}


@pytest.mark.parametrize("method", list(READS) + list(WRITES))
def test_a_store_that_cannot_be_reached_is_reported_as_a_data_store_error(method):
    flaky = FlakyStorage(InMemoryStorage())
    data = DataManager(flaky, FakePortal([]))
    flaky.fail_reads, flaky.fail_writes = True, True
    with pytest.raises(DataStoreError):
        {**READS, **WRITES}[method](data)


def test_updating_with_nothing_to_change_reports_an_unreachable_store():
    flaky = FlakyStorage(InMemoryStorage())
    flaky.fail_reads = True
    with pytest.raises(DataStoreError):
        DataManager(flaky, FakePortal([])).update_saved_job(URL)


def test_the_original_error_is_kept_as_the_cause():
    flaky = FlakyStorage(InMemoryStorage())
    flaky.fail_reads = True
    with pytest.raises(DataStoreError) as caught:
        DataManager(flaky, FakePortal([])).read_saved_jobs()
    assert isinstance(caught.value.__cause__, OSError)
    assert "could not be reached" in str(caught.value)


# ---------- choosing the storage ----------

def test_from_environment_builds_postgres_storage_from_database_url(monkeypatch):
    built = []

    class RecordingStorage(InMemoryStorage):
        def __init__(self, dsn: str) -> None:
            super().__init__()
            built.append(dsn)

    monkeypatch.setattr(data_manager, "PostgresStorage", RecordingStorage)
    monkeypatch.setenv("DATABASE_URL", "postgresql://someone@db.example/jobs")
    manager = DataManager.from_environment()
    assert built == ["postgresql://someone@db.example/jobs"] and isinstance(manager, DataManager)
    monkeypatch.delenv("DATABASE_URL")
    DataManager.from_environment()
    assert built[-1] == data_manager.DEFAULT_DATABASE_URL


# A saved job from an older version or a hand-edited file still loads in a shape the pages can show
def test_saved_jobs_with_wrong_field_types_are_tidied_on_read(data):
    job = {**make_job(1), "required_skills": None, "matched_skills": "abc", "missing_skills": 5, "status": "Weird"}
    data.save_new_jobs([job], "resume.pdf")
    loaded = data.read_saved_jobs()[0]
    assert loaded["required_skills"] == [] and loaded["matched_skills"] == [] and loaded["missing_skills"] == []
    assert loaded["status"] == "Saved" and data.get_saved_job(job["job_url"])["matched_skills"] == []


# The application tracker: how many saved jobs are at each stage, counted by the storage (SQL GROUP BY on SQLite)
def test_the_tracker_counts_jobs_by_status_with_the_storage(data):
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


# ---------- the job portal, reached through the data layer ----------

def test_listings_and_details_come_from_the_portal_through_the_data_manager():
    portal = FakePortal([make_job(1), make_job(2)], details={"description": "Use SQL", "listed_skills": ["SQL"]})
    data = DataManager(FlakyStorage(InMemoryStorage()), portal)
    listings = data.fetch_listings(["Data Analyst"], ["all"], {}, limit=5)
    assert [job["title"] for job in listings] == ["Data Analyst 1", "Data Analyst 2"]
    assert portal.searches == [("Data Analyst", {"step": "all"})]
    assert data.portal_job_details("https://example.com/job")["listed_skills"] == ["SQL"]
    assert data.portal_filters({}, "job type") == {"step": "job type"}


def test_a_search_in_the_store_gives_jobs_the_pages_can_always_show(data):
    data.save_new_jobs([make_job(1, required_skills=None, matched_skills="abc", status="Hired")], "resume.pdf")
    found = data.search_saved("analyst")[0]
    assert found["required_skills"] == [] and found["matched_skills"] == [] and found["status"] == "Saved"
