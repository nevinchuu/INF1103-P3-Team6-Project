"""Tests for TaskRegistry (src/services/task_registry.py): progress shared between the web app's threads and its pages."""

import threading

import pytest

from services.task_registry import MAX_FINISHED_TASKS, TaskRegistry


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
    tasks.update(task_id, percent=10)
    tasks.cancel(task_id)
    with pytest.raises(InterruptedError):
        tasks.update(task_id, percent=20)
    assert tasks.get(task_id)["percent"] == 10


def test_cancelling_a_finished_or_unknown_task_changes_nothing():
    tasks = TaskRegistry()
    task_id = tasks.start()
    tasks.update(task_id, status="done")
    tasks.cancel(task_id)
    tasks.cancel("no such id")
    assert tasks.get(task_id)["status"] == "done"


def test_find_recent_job_looks_in_finished_searches_only():
    tasks = TaskRegistry()
    running = tasks.start()
    tasks.update(running, result={"all_jobs": [{"job_url": "https://x/9"}], "resume": "r.pdf"})
    assert tasks.find_recent_job("https://x/9") is None
    done = tasks.start()
    tasks.update(done, status="done", result={"all_jobs": [{"job_url": "https://x/1", "title": "A"}], "resume": "cv.docx"})
    assert tasks.find_recent_job("https://x/1") == {"job_url": "https://x/1", "title": "A", "resume": "cv.docx"}
    assert tasks.find_recent_job("https://x/404") is None


def test_many_threads_can_update_one_task():
    tasks = TaskRegistry()
    task_id = tasks.start()
    threads = [threading.Thread(target=lambda n=n: tasks.update(task_id, percent=n)) for n in range(50)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert 0 <= tasks.get(task_id)["percent"] < 50


def test_old_finished_tasks_are_forgotten_but_running_ones_are_kept():
    tasks = TaskRegistry()
    running = tasks.start()
    first = tasks.start()
    tasks.update(first, status="done")
    for _ in range(MAX_FINISHED_TASKS + 5):
        tasks.update(tasks.start(), status="done")
    assert tasks.get(running)["status"] == "running" and tasks.get(first) is None
    with pytest.raises(InterruptedError):  # a forgotten task's thread stops at its next update
        tasks.update(first, percent=50)


def test_find_running_matches_every_field():
    tasks = TaskRegistry()
    task_id = tasks.start(kind="tailor", job_url="https://x/1")
    assert tasks.find_running(kind="tailor", job_url="https://x/1") == task_id
    assert tasks.find_running(kind="tailor", job_url="https://x/2") is None
    tasks.update(task_id, status="done")
    assert tasks.find_running(kind="tailor", job_url="https://x/1") is None
