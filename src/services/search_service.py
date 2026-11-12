"""SearchService: the job search steps the console app and the web app share, built from the four managers."""

import logging
from collections.abc import Callable

from ai.ai_manager import AIManager
from persistence.data_manager import DataManager
from services.job_portal import JobPortal
from logic.logic_manager import LogicManager

TOP_N = 5  # jobs kept and saved per search
JOBS_PER_SEARCH = 10  # listings fetched per job title; more means more AI batches (slower, costs more)


class SearchService:
    """One job search through the portal, the AI, the logic rules and the storage."""

    def __init__(self, portal: JobPortal, ai: AIManager, logic: LogicManager, data: DataManager) -> None:
        # Association: the service works with each manager through its public methods and owns none of them
        self._portal = portal
        self._ai = ai
        self._logic = logic
        self._data = data

    def find_jobs(self, profile: dict, preferences: dict,
                  on_progress: Callable[[str, int, int], None] | None = None) -> tuple[list[dict], dict]:
        """Job listings (portal), then each one's requirements (AI), then its matched and missing skills (logic).
        Returns (jobs, summary): the summary says which search step found them and how many were found/analysed.

        The portal is searched with the preferences it can filter, and only listings that meet all of them are
        analysed; with none, it falls back to the job type only, then to no preferences (summary["search_step"]).
        """
        summary: dict = {}
        listings = self._portal.fetch_listings(
            profile["search_keywords"], self._logic.search_steps(preferences), preferences,
            limit_per_search=JOBS_PER_SEARCH, on_progress=on_progress, summary=summary,
            fits=lambda job, step: self._logic.passes_filters(job, self._logic.filters_for_step(preferences, step)))
        answers = self._ai.extract_requirements_for_listings(listings, profile, on_progress=on_progress,
                                                             summary=summary)
        jobs = self._logic.add_requirements(listings, answers, profile)
        logging.info(f"event=search stage=jobs summary={summary}")
        return jobs, summary

    def rank(self, profile: dict, jobs: list[dict], filters: dict, search_step: str, top_n: int) -> dict:
        """The best jobs and how many each filter removed. Only the preferences the search kept are applied:
        one it had to drop would empty the results again. Returns {"jobs", "removed"}."""
        applied_filters = self._logic.filters_for_step(filters, search_step)
        return {"jobs": self._logic.filter_and_rank(profile, jobs, applied_filters, top_n),
                "removed": self._logic.count_removed(jobs, applied_filters)}

    def save(self, top_jobs: list[dict], resume_name: str) -> int | None:
        """Saves the top jobs, skipping ones already saved. Returns how many were new, or None if the save
        failed (the caller still shows the results and keeps them for the session)."""
        if not top_jobs:
            return 0
        try:
            newly_saved = self._data.save_new_jobs(top_jobs, resume_name)
            logging.info(f"event=search stage=save outcome=ok new={newly_saved}")
            return newly_saved
        except Exception as error:
            logging.error(f"event=search stage=save outcome=failed error={type(error).__name__}: {error}")
            return None

    def rank_and_save(self, profile: dict, jobs: list[dict], filters: dict, search_step: str, resume_name: str,
                      top_n: int) -> dict:
        """rank, then save (a failed save still shows the jobs).
        Returns {"jobs", "removed", "save_failed", "newly_saved"} for the results page."""
        ranked = self.rank(profile, jobs, filters, search_step, top_n)
        newly_saved = self.save(ranked["jobs"], resume_name)
        return {**ranked, "save_failed": newly_saved is None, "newly_saved": newly_saved or 0}

    def job_details(self, job_url: str) -> dict | None:
        """One job's full description from the portal (for tailoring), or None if it can't be fetched."""
        return self._portal.details(job_url)