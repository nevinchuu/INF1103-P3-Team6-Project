"""Tests for the data layer (src/data_manager.py): saving, loading and querying jobs in a JSON file.

Plain functions only (the project is fully procedural). Run from the project root:

    python tests/test_data_manager.py

Each test works on its own file in a temporary folder, so the real data/job_listings.json is never
touched. The runner at the bottom calls every test_* function; they also run under pytest.
"""

import glob
import json
import os
import sys
import tempfile

# The project's modules live in src/, so add it to the import path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import data_manager  # noqa: E402
import io_manager  # noqa: E402  (all output goes through the input/output layer)

REAL_DATABASE_PATH = data_manager.DATABASE_PATH


def use_temporary_database(folder: str) -> str:
    """Points the data layer at a job_listings.json inside folder, and returns that path."""
    data_manager.DATABASE_PATH = os.path.join(folder, "data", "job_listings.json")
    return data_manager.DATABASE_PATH


def write_raw(path: str, text: str) -> None:
    """Writes text to path as it is, e.g. a corrupt file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        file.write(text)


def make_job(number: int, **changes: object) -> dict:
    """A job record like the logic layer returns, with its own link."""
    job = {"title": f"Data Analyst {number}", "company": "Acme Pte Ltd", "location": "Jurong",
           "job_url": f"https://www.mycareersfuture.gov.sg/job/it/data-analyst-{number:032x}"}
    job.update(changes)
    return job


# 1. Load on startup: a missing file gives an empty list and is created
def test_missing_file_is_created_empty():
    with tempfile.TemporaryDirectory() as folder:
        path = use_temporary_database(folder)
        assert data_manager.read_saved_jobs() == []
        with open(path, encoding="utf-8") as file:
            assert json.load(file) == []


# 2. A corrupt file doesn't crash: it is kept as a dated backup and replaced by an empty list
def test_corrupt_file_is_backed_up_not_crashed():
    with tempfile.TemporaryDirectory() as folder:
        path = use_temporary_database(folder)
        write_raw(path, '[{"title": "half a reco')
        assert data_manager.read_saved_jobs() == []
        backups = glob.glob(path + ".*.bak")
        assert len(backups) == 1
        with open(backups[0], encoding="utf-8") as file:
            assert file.read() == '[{"title": "half a reco'  # nothing the user had is lost


# 3. Valid JSON that isn't a list of jobs (e.g. edited by hand) is treated the same as corrupt
def test_file_that_is_not_a_list_is_backed_up():
    with tempfile.TemporaryDirectory() as folder:
        path = use_temporary_database(folder)
        for text in ('{"jobs": []}', "null", "42"):
            write_raw(path, text)
            assert data_manager.read_saved_jobs() == []
        assert len(glob.glob(path + ".*.bak")) >= 1


# 4. Loading skips anything in the list that isn't a job record
def test_loading_skips_entries_that_are_not_jobs():
    with tempfile.TemporaryDirectory() as folder:
        path = use_temporary_database(folder)
        write_raw(path, json.dumps([make_job(1), "not a job", 7, None]))
        assert [job["title"] for job in data_manager.read_saved_jobs()] == ["Data Analyst 1"]


# 5. Saving keeps jobs between runs, adds only new links, notes the resume and numbers the IDs
def test_save_new_jobs_skips_duplicates_and_persists():
    with tempfile.TemporaryDirectory() as folder:
        use_temporary_database(folder)
        assert data_manager.save_new_jobs([make_job(1), make_job(2)], "resume_a.pdf") == 2
        # Job 2 is already saved, so only job 3 is new; job 2 keeps the resume that first found it
        assert data_manager.save_new_jobs([make_job(2), make_job(3)], "resume_b.pdf") == 1
        saved = data_manager.read_saved_jobs()  # read back from the file, as a new run would
        assert [job["title"] for job in saved] == ["Data Analyst 1", "Data Analyst 2", "Data Analyst 3"]
        assert [job["resume"] for job in saved] == ["resume_a.pdf", "resume_a.pdf", "resume_b.pdf"]
        assert [job["ID"] for job in saved] == [0, 1, 2]
        assert data_manager.save_new_jobs([], "resume_a.pdf") == 0


# 6. Query: every word must appear in the title, company or location, ignoring case
def test_search_jobs_matches_every_word_in_title_company_or_location():
    jobs = [make_job(1, title="Senior Data Analyst"), make_job(2, title="Data Engineer"),
            make_job(3, title="Accountant", location="Data Centre, Jurong")]
    assert [job["title"] for job in data_manager.search_jobs(jobs, "data analyst")] == ["Senior Data Analyst"]
    assert [job["title"] for job in data_manager.search_jobs(jobs, "JURONG data")] == [
        "Senior Data Analyst", "Data Engineer", "Accountant"]
    assert data_manager.search_jobs(jobs, "nurse") == []


# 7. Query: one job by its link
def test_find_by_url():
    jobs = [make_job(1), make_job(2)]
    assert data_manager.find_by_url(jobs, make_job(2)["job_url"])["title"] == "Data Analyst 2"
    assert data_manager.find_by_url(jobs, "https://example.com/not-saved") is None


# 8. Removing one job or all of them; the IDs are numbered again afterwards
def test_remove_one_job_and_clear_all():
    with tempfile.TemporaryDirectory() as folder:
        use_temporary_database(folder)
        data_manager.save_new_jobs([make_job(1), make_job(2), make_job(3)], "resume.pdf")
        data_manager.remove_saved_job(make_job(2)["job_url"])
        saved = data_manager.read_saved_jobs()
        assert [(job["ID"], job["title"]) for job in saved] == [(0, "Data Analyst 1"), (1, "Data Analyst 3")]
        data_manager.clear_saved_jobs()
        assert data_manager.read_saved_jobs() == []


# 9. A save that fails part way leaves the old file as it was, and no temporary file behind
def test_failed_save_keeps_the_old_file():
    with tempfile.TemporaryDirectory() as folder:
        path = use_temporary_database(folder)
        data_manager.save_new_jobs([make_job(1)], "resume.pdf")
        try:
            data_manager.write_database([make_job(2, salary=object())])  # can't be written as JSON
            raise AssertionError("expected the save to fail")
        except TypeError:
            pass
        assert [job["title"] for job in data_manager.read_saved_jobs()] == ["Data Analyst 1"]
        assert glob.glob(os.path.join(os.path.dirname(path), "*.tmp")) == []


# 10. Two broken files in the same second each keep their own backup, so nothing is lost
def test_backups_in_the_same_second_are_all_kept():
    with tempfile.TemporaryDirectory() as folder:
        path = use_temporary_database(folder)
        for text in ("{first broken file", "{second broken file", "{third broken file"):
            write_raw(path, text)
            assert data_manager.read_saved_jobs() == []
        contents = []
        for backup in glob.glob(path + ".*.bak"):
            with open(backup, encoding="utf-8") as file:
                contents.append(file.read())
        assert sorted(contents) == ["{first broken file", "{second broken file", "{third broken file"]


# 11. Saving doesn't change the caller's records (the saved copies get "resume" and "ID")
def test_saving_leaves_the_callers_jobs_unchanged():
    with tempfile.TemporaryDirectory() as folder:
        use_temporary_database(folder)
        jobs = [make_job(1), make_job(2)]
        data_manager.save_new_jobs(jobs, "resume.pdf")
        assert jobs == [make_job(1), make_job(2)]
        assert [job["resume"] for job in data_manager.read_saved_jobs()] == ["resume.pdf", "resume.pdf"]


# 12. The same link twice in one save is saved, and counted, once
def test_the_same_link_twice_in_one_save_counts_once():
    with tempfile.TemporaryDirectory() as folder:
        use_temporary_database(folder)
        assert data_manager.save_new_jobs([make_job(1), make_job(1, title="Data Analyst 1 again")], "resume.pdf") == 1
        assert [job["title"] for job in data_manager.read_saved_jobs()] == ["Data Analyst 1"]


# 13. A saved file with a stray entry still takes new jobs, and the stray entry is dropped
def test_a_stray_entry_in_the_file_does_not_stop_saving():
    with tempfile.TemporaryDirectory() as folder:
        path = use_temporary_database(folder)
        write_raw(path, json.dumps([1, make_job(1)]))
        assert data_manager.save_new_jobs([make_job(2)], "resume.pdf") == 1
        assert [job["job_url"] for job in data_manager.read_saved_jobs()] == [make_job(1)["job_url"],
                                                                              make_job(2)["job_url"]]
        with open(path, encoding="utf-8") as file:
            assert all(isinstance(entry, dict) for entry in json.load(file))


# 14. A saved job with no link is skipped, so it can't break the duplicate check
def test_a_saved_job_without_a_link_does_not_stop_saving():
    with tempfile.TemporaryDirectory() as folder:
        path = use_temporary_database(folder)
        write_raw(path, json.dumps([{"title": "Hand-made entry"}, make_job(1)]))
        assert data_manager.save_new_jobs([make_job(2)], "resume.pdf") == 1
        assert [job["job_url"] for job in data_manager.read_saved_jobs()] == [make_job(1)["job_url"],
                                                                              make_job(2)["job_url"]]


# 15. A new job without a link is not saved, whether the link is missing, empty or the portal's "N/A"
def test_jobs_without_a_link_are_not_saved():
    with tempfile.TemporaryDirectory() as folder:
        use_temporary_database(folder)
        no_link = {"title": "No link at all"}
        jobs = [no_link, make_job(1, job_url=""), make_job(2, job_url="N/A"), make_job(3)]
        assert data_manager.save_new_jobs(jobs, "resume.pdf") == 1
        assert [job["job_url"] for job in data_manager.read_saved_jobs()] == [make_job(3)["job_url"]]


def run_all_tests() -> int:
    """Runs every test_* function in this file. Returns how many failed."""
    tests = [function for name, function in globals().items() if name.startswith("test_") and callable(function)]
    failed = 0
    for test in tests:
        try:
            test()
            io_manager.display_message(f"PASS  {test.__name__}")
        except AssertionError as error:
            failed += 1
            io_manager.display_error(f"FAIL  {test.__name__} {error}")
        finally:
            data_manager.DATABASE_PATH = REAL_DATABASE_PATH
    io_manager.display_message(f"\n{len(tests) - failed} of {len(tests)} tests passed.")
    return failed


if __name__ == "__main__":
    sys.exit(1 if run_all_tests() else 0)  # a non-zero exit code tells CI that a test failed
