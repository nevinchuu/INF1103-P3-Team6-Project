"""TaskRegistry: the progress of the web app's background searches and tailoring runs."""

import threading
import uuid

MAX_FINISHED_TASKS = 30  # finished tasks kept for their pages; older ones are forgotten so memory doesn't grow


class TaskRegistry:
    """Progress of background tasks by id, safe to use from several threads."""

    def __init__(self) -> None:
        self.__tasks: dict[str, dict] = {}
        self.__lock = threading.Lock()

    def start(self, **extra: object) -> str:
        """Add a running task and return its id. extra: e.g. kind="tailor" and job_url for tailoring.
        Only the newest MAX_FINISHED_TASKS finished tasks are kept; running ones are never dropped."""
        task_id = uuid.uuid4().hex
        with self.__lock:
            finished = [old_id for old_id, task in self.__tasks.items() if task["status"] != "running"]
            for old_id in finished[:max(0, len(finished) - MAX_FINISHED_TASKS + 1)]:  # oldest first
                del self.__tasks[old_id]
            self.__tasks[task_id] = {"status": "running", "percent": 0, "message": "Starting...",
                                    "result": None, "error": None, **extra}
        return task_id

    def find_running(self, **fields: object) -> str | None:
        """The id of a running task whose fields all equal these (e.g. the same job and resume), or None."""
        with self.__lock:
            for task_id, task in self.__tasks.items():
                if task["status"] == "running" and all(task.get(key) == value for key, value in fields.items()):
                    return task_id
        return None

    def update(self, task_id: str, **changes: object) -> None:
        """Update a task's progress. Raises InterruptedError (built into Python) if the user
        cancelled it, so the background thread stops at its next update."""
        with self.__lock:
            task = self.__tasks.get(task_id)
            if task is None or task["status"] == "cancelled":
                raise InterruptedError("cancelled by the user")
            task.update(changes)

    def get(self, task_id: str) -> dict | None:
        """A copy of one task, or None if there is no task with that id."""
        with self.__lock:
            task = self.__tasks.get(task_id)
            return dict(task) if task is not None else None

    def cancel(self, task_id: str) -> None:
        """Mark a running task as cancelled; its thread stops at its next update."""
        with self.__lock:
            task = self.__tasks.get(task_id)
            if task is not None and task["status"] == "running":
                task["status"] = "cancelled"

    def find_recent_job(self, job_url: str) -> dict | None:
        """A job by its link from a finished search still in memory, with the resume that found it, or None."""
        with self.__lock:
            results = [task["result"] for task in self.__tasks.values()
                       if task["status"] == "done" and "all_jobs" in task["result"]]
        for result in results:
            for job in result["all_jobs"]:
                if job["job_url"] == job_url:
                    return {**job, "resume": result["resume"]}
        return None
