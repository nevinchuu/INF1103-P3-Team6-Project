"""Tests for TaskRegistry (src/io_layer/task_registry.py): progress shared between the web app's threads and its pages."""

import threading

import pytest

from io_layer.task_registry import MAX_FINISHED_TASKS, TaskRegistry


def test_a_new_task_is_running_with_its_extra_fields():
    tasks = TaskRegistry()
    task_id = tasks.start(job_url="https://x/1")
    task = tasks.get(task_id)
    assert task["status"] == "running" and task["percent"] == 0 and task["job_url"] == "https://x/1"


def test_an_unknown_id_gives_none():
    assert TaskRegistry().get("no such id") is None


def test_get_gives_a_copy_so_callers_cannot_change_the_task():
    tasks = TaskRegistry()
    task_id = tasks.start()
    tasks.get(task_id)["percent"] = 99
    assert tasks.get(task_id)["percent"] == 0


def test_a_cancelled_task_stops_at_its_next_update():
    tasks = TaskRegistry()
    task_id = tasks.start()
    tasks.update(task_id, 10, "Working")
    tasks.cancel(task_id)
    with pytest.raises(InterruptedError):
        tasks.update(task_id, 20, "Working")
    assert tasks.get(task_id)["percent"] == 10


# A search that tries again with fewer preferences, or searches again for jobs at the candidate's level, reports
# a smaller percent: the bar keeps its place and only the message changes
def test_the_percent_never_goes_down():
    tasks = TaskRegistry()
    task_id = tasks.start()
    tasks.update(task_id, 60, "Reading job requirements")
    tasks.update(task_id, 30, "Searching again")
    assert tasks.get(task_id)["percent"] == 60 and tasks.get(task_id)["message"] == "Searching again"
    tasks.update(task_id, 70, "Reading more")
    assert tasks.get(task_id)["percent"] == 70


def test_cancelling_a_finished_or_unknown_task_changes_nothing():
    tasks = TaskRegistry()
    task_id = tasks.start()
    tasks.finish(task_id, {})
    tasks.cancel(task_id)
    tasks.cancel("no such id")
    assert tasks.get(task_id)["status"] == "done"


def test_find_recent_job_looks_in_finished_searches_only():
    tasks = TaskRegistry()
    tasks.start(result={"all_jobs": [{"job_url": "https://x/9"}], "resume": "r.pdf"})  # still running
    assert tasks.find_recent_job("https://x/9") is None
    done = tasks.start()
    tasks.finish(done, {"all_jobs": [{"job_url": "https://x/1", "title": "A"}], "resume": "cv.docx"})
    assert tasks.find_recent_job("https://x/1") == {"job_url": "https://x/1", "title": "A", "resume": "cv.docx"}
    assert tasks.find_recent_job("https://x/404") is None


def test_many_threads_can_update_one_task():
    tasks = TaskRegistry()
    task_id = tasks.start()
    threads = [threading.Thread(target=lambda n=n: tasks.update(task_id, n, "Working")) for n in range(50)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert 0 <= tasks.get(task_id)["percent"] < 50


def test_find_running_matches_every_field():
    tasks = TaskRegistry()
    task_id = tasks.start(kind="tailor", job_url="https://x/1")
    assert tasks.find_running(kind="tailor", job_url="https://x/1") == task_id
    assert tasks.find_running(kind="tailor", job_url="https://x/2") is None
    tasks.finish(task_id, {})
    assert tasks.find_running(kind="tailor", job_url="https://x/1") is None


def test_finish_marks_a_task_done_with_its_result_and_fail_keeps_the_error():
    tasks = TaskRegistry()
    done, failed = tasks.start(), tasks.start()
    tasks.update(done, 40, "Halfway")
    tasks.finish(done, {"all_jobs": []})
    tasks.fail(failed, "It went wrong.")
    assert tasks.get(done) == {"status": "done", "percent": 100, "message": "Done", "result": {"all_jobs": []},
                               "error": None}
    assert tasks.get(failed)["status"] == "error" and tasks.get(failed)["error"] == "It went wrong."
    assert tasks.get(failed)["result"] is None


def test_finish_and_fail_stop_the_thread_of_a_cancelled_or_forgotten_task():
    tasks = TaskRegistry()
    cancelled = tasks.start()
    tasks.cancel(cancelled)
    for call in (lambda: tasks.finish(cancelled, {}), lambda: tasks.fail(cancelled, "x"),
                 lambda: tasks.finish("no such id", {}), lambda: tasks.fail("no such id", "x")):
        with pytest.raises(InterruptedError):
            call()
    assert tasks.get(cancelled)["status"] == "cancelled"
