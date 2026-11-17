"""SearchService: the job search steps behind the web app, built from the four managers."""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from ai_layer.ai_manager import AIManager
from data_layer.data_manager import DataManager
from data_layer.data_store_error import DataStoreError
from logic_layer.logic_manager import EDUCATION_LEVELS, SENIORITY_LEVELS, LogicManager

TOP_N = 5  # jobs kept and saved per search
JOBS_PER_SEARCH = 10  # listings per job title the AI reads; more means more AI batches (slower, costs more)
# Listings per title fetched at the candidate's level. Their postings say the years of experience each asks for, and
# the JOBS_PER_SEARCH asking the fewest years beyond the candidate's are the ones the AI reads (no AI cost: postings)
AT_LEVEL_PER_SEARCH = 20
# Job postings fetched at once (the search results have no description or years of experience). The portal answers
# "too many requests" when many come at once; JobPortal.details waits and retries, and fewer at once trips it less
DETAIL_WORKERS = 4
# The levels each title is searched at: the lowest ones, up to one above the candidate's own (at least the first
# three: Fresh/entry level, Non-executive, Junior Executive). The portal can filter by level, not by years or education
MIN_SUITED_LEVELS = 3


class SearchService:
    """One job search through the data layer, the AI, the logic rules and the storage."""

    def __init__(self, ai: AIManager, logic: LogicManager, data: DataManager) -> None:
        # Association: the service works with each manager through its public methods and owns none of them
        self.__ai = ai
        self.__logic = logic
        self.__data = data

    def read_profile(self, resume_text: str, fallback: dict | None = None) -> dict | None:
        """The candidate's profile from the resume (AI), or None if the AI could not read it. What it missed is filled
        in from the fallback the person typed: {"qualification": str, "skills": list[str]}, e.g.
        {"qualification": "Diploma", "skills": ["Python", "Excel"]}."""
        profile = self.__ai.extract_candidate_profile(resume_text)
        if profile is None:
            return None
        fallback = fallback or {}
        qualification = fallback.get("qualification", "")
        skills = fallback.get("skills", [])
        if profile["highest_qualification"] == "None" and qualification in EDUCATION_LEVELS:
            profile["highest_qualification"] = qualification
            profile["qualification_detail"] = profile["qualification_detail"] or qualification
        if not profile["core_skills"] and skills:
            profile["core_skills"] = skills
        return profile

    def find_jobs(self, profile: dict, preferences: dict,
                  on_progress: Callable[[str, int, int], None] | None = None) -> tuple[list[dict], dict]:
        """Job listings (portal), then each one's requirements (AI), then its matched and missing skills (logic).
        Returns (jobs, summary): the summary says which search step found them, how many were at the candidate's
        level (at_level) and how many were found and analysed.

        Each job title is searched at the levels that suit the candidate (suited_levels: the portal's one filter
        that fits a profile; it has none for years of experience, education or skills), AT_LEVEL_PER_SEARCH listings
        per title. Their full postings give the years each asks for, and the JOBS_PER_SEARCH per title asking the
        fewest years beyond the candidate's are kept (years_short). A title with fewer than that at their level is
        topped up from the same title's search without the level filter, so the AI reads up to JOBS_PER_SEARCH per
        title, the suited ones first.
        The portal (reached through the DataManager) is searched with the preferences it can filter, and only
        listings that meet all of them are kept; with none, it falls back to the job type only, then to no
        preferences (summary["search_step"]). When some but fewer than TOP_N listings meet every preference, the
        wider steps are searched too, closest first, until there are TOP_N; their new listings are added as close
        matches (close_match=True), so the results page can still fill its list.
        on_progress(stage, done, total) reports "search", "postings" and "analyse".
        """
        summary: dict = {}
        steps = self.__logic.search_steps(preferences)
        keywords = profile["search_keywords"]

        def fits(job: dict, step: str) -> bool:
            return self.__logic.passes_filters(job, self.__logic.filters_for_step(preferences, step))
        # The search without the level filter decides the step, so the level filter never costs a preference
        others = self.__data.fetch_listings(keywords, steps, preferences, limit=JOBS_PER_SEARCH,
                                            on_progress=on_progress, summary=summary, fits=fits)
        at_level = self.__data.fetch_listings(keywords, [summary["search_step"]],
                                              {**preferences, "position_levels": self.__suited_levels(profile)},
                                              limit=AT_LEVEL_PER_SEARCH, fits=fits) if others else []
        # Of each title's listings at their level, the ones asking the fewest years beyond theirs (the portal's
        # order breaks ties: sorted() keeps it)
        at_level = self.__with_postings(at_level, summary, on_progress)
        by_title: dict[str, list[dict]] = {}
        for job in at_level:
            by_title.setdefault(job["found_by"], []).append(job)
        at_level = [job for jobs in by_title.values()
                    for job in sorted(jobs, key=lambda job: self.__years_short(profile, job))[:JOBS_PER_SEARCH]]
        # Each title: its listings at their level first, topped up to JOBS_PER_SEARCH from the search without
        listings = [{**job, "at_level": True} for job in at_level]
        known = {job["job_url"] for job in listings}
        per_title: dict[str, int] = {}
        for job in listings:
            per_title[job["found_by"]] = per_title.get(job["found_by"], 0) + 1
        for job in others:
            if job["job_url"] not in known and per_title.get(job["found_by"], 0) < JOBS_PER_SEARCH:
                known.add(job["job_url"])
                per_title[job["found_by"]] = per_title.get(job["found_by"], 0) + 1
                listings.append(job)
        summary["at_level"] = len(at_level)

        if 0 < len(listings) < TOP_N and summary.get("search_step") == steps[0]:
            close: list[dict] = []
            for step in steps[1:]:  # closest first (job type kept), widest last, until the list can be filled
                if len(listings) + len(close) >= TOP_N:
                    break
                for job in self.__data.fetch_listings(keywords, [step], preferences, limit=JOBS_PER_SEARCH, fits=fits):
                    # Enough to pick the best few from: every close match is another listing for the AI to read
                    if job["job_url"] not in known and len(close) < JOBS_PER_SEARCH:
                        known.add(job["job_url"])
                        close.append({**job, "close_match": True})
            summary["close_matches"] = len(close)
            listings = listings + close
        summary["listings_found"] = len(listings)
        # The top-ups' postings (the ones at their level are read): few, so they report no progress of their own
        jobs = self.__analyse(profile, self.__with_postings(listings, summary, None), summary, on_progress)
        logging.info(f"event=search stage=jobs summary={summary}")
        return jobs, summary

    @staticmethod
    def __suited_levels(profile: dict) -> list[str]:
        """The job levels that suit the candidate: the lowest ones, up to one above their own."""
        level = profile.get("seniority_level")
        own = SENIORITY_LEVELS.index(level) if level in SENIORITY_LEVELS else 0
        return SENIORITY_LEVELS[:max(MIN_SUITED_LEVELS, own + 2)]

    @staticmethod
    def __years_short(profile: dict, job: dict) -> float:
        """How many more years of experience the job asks for than the candidate has (0 if they have enough)."""
        return max(0, (job.get("min_years_experience") or 0) - (profile.get("years_of_experience") or 0))

    def __with_postings(self, listings: list[dict], summary: dict,
                        on_progress: Callable[[str, int, int], None] | None) -> list[dict]:
        """The listings with each one's full posting added (several fetched at a time).
        MyCareersFuture's search results carry no description and no years of experience, only the title and the
        employer's skill tags. Without them the AI would judge each job's skills from the tags alone, and every job
        would seem to need no experience (so the experience filter and rule never applied, and the shortlist could
        not pick the ones the candidate qualifies for). One whose posting can't be fetched keeps what it had."""
        without = [index for index, job in enumerate(listings) if not job.get("description")]
        if not without:
            return listings
        listings = list(listings)
        fetched = 0
        with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as workers:
            postings = workers.map(self.__data.portal_job_details, [listings[i]["job_url"] for i in without])
            for done, (index, posting) in enumerate(zip(without, postings), start=1):
                if on_progress:
                    on_progress("postings", done, len(without))
                if not posting:
                    continue
                update: dict = {}
                if posting.get("description"):
                    update["description"] = posting["description"]
                    update["listed_skills"] = posting.get("listed_skills") or listings[index].get("listed_skills", [])
                    fetched += 1
                if posting.get("min_years_experience") is not None:
                    update["min_years_experience"] = posting["min_years_experience"]
                listings[index] = {**listings[index], **update}
        summary["descriptions_fetched"] = summary.get("descriptions_fetched", 0) + fetched
        return listings

    def __analyse(self, profile: dict, listings: list[dict], summary: dict,
                  on_progress: Callable[[str, int, int], None] | None) -> list[dict]:
        """Each listing's requirements (AI), then its matched and missing skills (logic). A second round adds its
        counts to the first round's in the summary."""
        if not listings:
            return []
        counts: dict = {}
        answers = self.__ai.extract_requirements_for_listings(listings, profile, on_progress=on_progress, summary=counts)
        for key, value in counts.items():
            summary[key] = summary.get(key, 0) + value if isinstance(value, int) else value
        return self.__logic.add_requirements(listings, answers, profile)

    def __rank(self, profile: dict, jobs: list[dict], filters: dict, top_n: int) -> dict:
        """The best jobs and how many each filter removed. Returns {"jobs", "removed"}."""
        return {"jobs": self.__logic.filter_and_rank(profile, jobs, filters, top_n),
                "removed": self.__logic.count_removed(jobs, filters)}

    def __save(self, top_jobs: list[dict], resume_name: str) -> int | None:
        """Saves the top jobs, skipping ones already saved. Returns how many were new, or None if the save
        failed (the caller still shows the results and keeps them for the session)."""
        if not top_jobs:
            return 0
        try:
            newly_saved = self.__data.save_new_jobs(top_jobs, resume_name)
            logging.info(f"event=search stage=save outcome=ok new={newly_saved}")
            return newly_saved
        except DataStoreError as error:
            logging.error(f"event=search stage=save outcome=failed error={type(error).__name__}: {error}")
            return None

    def rank_and_save(self, profile: dict, jobs: list[dict], filters: dict, resume_name: str,
                      top_n: int = TOP_N) -> dict:
        """Ranks the jobs, then saves the best (a failed save still shows the jobs). filters are the preferences the
        search kept: one it had to drop would empty the results again (LogicManager.filters_for_step).
        Exact matches come first; if they don't fill top_n, the close matches (find_jobs) fill the rest, best first,
        each with close_reason: the filter it misses, e.g. "job type".
        Returns {"jobs", "removed", "save_failed", "newly_saved"} for the results page."""
        ranked = self.__rank(profile, [job for job in jobs if not job.get("close_match")], filters, top_n)
        close = [job for job in jobs if job.get("close_match")]
        room = top_n - len(ranked["jobs"])
        if close and room > 0:
            fill = self.__logic.filter_and_rank(profile, close, self.__logic.filters_for_step(filters, "general"), room)
            for job in fill:
                job["close_reason"] = next(iter(self.__logic.count_removed([job], filters)), "")
            ranked["jobs"] = ranked["jobs"] + fill
        newly_saved = self.__save(ranked["jobs"], resume_name)
        return {**ranked, "save_failed": newly_saved is None, "newly_saved": newly_saved or 0}
