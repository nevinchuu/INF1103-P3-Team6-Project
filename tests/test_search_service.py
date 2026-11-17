"""Tests for SearchService (src/io_layer/search_service.py): the search steps, with a fake portal, a scripted AI and a storage."""

import pytest
from fakes import FakePortal, FlakyStorage, InMemoryStorage, ScriptedAIManager

from data_layer.data_manager import DataManager
from logic_layer.logic_manager import LogicManager
from io_layer.search_service import JOBS_PER_SEARCH, SearchService, TOP_N

PROFILE = {"candidate_name": "Sample Candidate", "highest_qualification": "Diploma",
           "qualification_detail": "Diploma in Financial Technology", "years_of_experience": 1,
           "internship_months": 6, "seniority_level": "Fresh/entry level", "core_skills": ["SQL", "Python"],
           "certifications": [], "search_keywords": ["Data Analyst"]}
FILTERS = {"min_salary": 0, "max_salary": 100000, "max_years_experience": 20, "job_type": "Full Time",
           "work_arrangement": "Any"}


def listing(number: int, **changes) -> dict:
    job = {"title": f"Job {number}", "company": "Acme", "location": "Jurong", "min_salary": 4000, "max_salary": 6000,
           "salary_period": "monthly", "employment_types": ["Full Time"], "position_levels": ["Executive"],
           "min_years_experience": 0, "flexible_work_arrangements": [], "listed_skills": ["SQL"],
           "description": "SQL reporting", "job_url": f"https://www.mycareersfuture.gov.sg/job/it/job-{number:032x}"}
    job.update(changes)
    return job


def answers(record: dict, task: str) -> dict:
    return {"jobs": [{"job_index": index, "required_skills": ["SQL"], "min_education": "Not specified",
                      "certifications_required": [], "work_arrangement": "Onsite",
                      "skill_matches": [{"job_skill": "SQL", "candidate_skill": "SQL"}],
                      "suitability_reason": "Matches SQL."} for index in range(len(record["jobs"]))]}


def service(listings, details=None) -> tuple[SearchService, FakePortal, FlakyStorage]:
    portal = FakePortal(listings, details=details)
    storage = FlakyStorage(InMemoryStorage())
    return SearchService(ScriptedAIManager(answers), LogicManager(), DataManager(storage, portal)), portal, storage


def preference_steps(portal: FakePortal) -> list[str]:
    """The search step of each portal search, leaving out the searches at suited levels."""
    return [filters["step"] for _, filters in portal.searches if "levels" not in filters]


def test_a_search_that_fits_nothing_falls_back_to_fewer_preferences():
    part_time = listing(1, employment_types=["Part Time"])
    search, portal, _ = service([part_time])
    jobs, summary = search.find_jobs(PROFILE, FILTERS)
    # "all" and "job type" filter the listing out (it is part time); "general" keeps it
    assert summary["search_step"] == "general" and [job["title"] for job in jobs] == ["Job 1"]
    assert preference_steps(portal) == ["all", "job type", "general"]


class LevelPortal(FakePortal):
    """Like the real portal's positionLevels filter: a search limited to levels lists only the jobs at those levels."""

    def __init__(self, listings: list[dict], at_levels: list[dict]) -> None:
        super().__init__(listings)
        self.__at_levels = at_levels

    def search(self, query: str, limit: int = 10, filters: dict | None = None) -> list[dict] | None:
        found = super().search(query, limit, filters)
        return [dict(job) for job in self.__at_levels] if "levels" in (filters or {}) else found


def level_search(listings: list[dict], at_levels: list[dict]) -> tuple[SearchService, LevelPortal, list[dict]]:
    """A service on a LevelPortal, and the records the AI is asked to read."""
    portal = LevelPortal(listings, at_levels)
    search = SearchService(ScriptedAIManager(answers), LogicManager(), DataManager(FlakyStorage(InMemoryStorage()), portal))
    read: list[dict] = []
    search._SearchService__ai._responder = lambda record, task: read.append(record) or answers(record, task)
    return search, portal, read


# Each title is searched at the levels that suit the candidate, and topped up to JOBS_PER_SEARCH from the search
# without the level filter: the AI reads no more than before, the suited jobs first, and the one they qualify for
# is ranked first even though the plain search listed none like it
def test_each_title_is_searched_at_the_candidates_levels_and_topped_up():
    senior = [listing(number, min_years_experience=5) for number in range(1, 13)]  # they have 1 year: Stretch
    search, portal, read = level_search(senior, at_levels=[listing(99, position_levels=["Fresh/entry level"])])
    jobs, summary = search.find_jobs(PROFILE, FILTERS)
    level_searches = [filters for _, filters in portal.searches if "levels" in filters]
    # a Fresh/entry level candidate: the three lowest levels, at the step the plain search found listings at
    assert level_searches == [{"step": "all", "levels": ["Fresh/entry level", "Non-executive", "Junior Executive"]}]
    assert summary["at_level"] == 1 and summary["listings_found"] == JOBS_PER_SEARCH == len(jobs)
    assert [job["title"] for job in read[0]["jobs"]] == ["Job 99"] + [f"Job {n}" for n in range(1, 10)]
    ranked = search.rank_and_save(PROFILE, jobs, FILTERS, "cv.docx")
    assert ranked["jobs"][0]["title"] == "Job 99" and ranked["jobs"][0]["match_outcome"] == "Strong match"
    assert {job["match_outcome"] for job in ranked["jobs"][1:]} == {"Stretch"}


# More listings are fetched at their level than the AI reads; each one's posting gives the years it asks for, and
# the AI reads the ones asking the fewest years beyond the candidate's (portal order breaks ties)
def test_the_ai_reads_the_jobs_at_their_level_asking_the_fewest_years():
    two_years = [listing(number, min_years_experience=2) for number in range(1, 11)]
    no_years = [listing(number, min_years_experience=0) for number in range(11, 16)]
    search, _, read = level_search([listing(99)], at_levels=two_years + no_years)
    jobs, summary = search.find_jobs({**PROFILE, "years_of_experience": 0}, FILTERS)
    assert summary["at_level"] == JOBS_PER_SEARCH and len(jobs) == JOBS_PER_SEARCH
    assert [job["title"] for job in read[0]["jobs"]] == [f"Job {n}" for n in (11, 12, 13, 14, 15, 1, 2, 3, 4, 5)]


# The plain search decides the step: jobs at their level that miss a preference (here part time, when they want
# full time) don't make the search drop that preference
def test_the_level_filter_never_costs_a_preference():
    part_time_entry = listing(99, position_levels=["Fresh/entry level"], employment_types=["Part Time"])
    search, portal, _ = level_search([listing(1)], at_levels=[part_time_entry])
    jobs, summary = search.find_jobs(PROFILE, FILTERS)
    assert summary["search_step"] == "all" and summary["at_level"] == 0
    assert [job["title"] for job in jobs if not job.get("close_match")] == ["Job 1"]


def test_the_suited_levels_reach_one_above_the_candidates_own():
    search, portal, _ = service([listing(1, min_years_experience=9)])
    search.find_jobs({**PROFILE, "seniority_level": "Executive"}, FILTERS)
    levels = next(filters["levels"] for _, filters in portal.searches if "levels" in filters)
    assert levels == ["Fresh/entry level", "Non-executive", "Junior Executive", "Executive", "Senior Executive"]


# The portal's search results have no description, so each job's full posting is fetched before the AI reads it:
# the AI then judges the job's skills from the whole text, not only the employer's tags
def test_each_jobs_full_posting_is_read_before_the_ai_judges_it():
    bare = listing(1, description="", listed_skills=["Attention to Detail"])
    posting = {"description": "Good knowledge of Microsoft Excel, Pivot Tables and Power BI.",
               "listed_skills": ["Pivot Tables", "SQL"], "is_open": True}
    search, _, _ = service([bare], details=posting)
    seen = []
    search._SearchService__ai._responder = lambda record, task: seen.append(record) or answers(record, task)
    jobs, summary = search.find_jobs(PROFILE, FILTERS)
    assert seen[0]["jobs"][0]["description"] == posting["description"]
    assert seen[0]["jobs"][0]["listed_skills"] == ["Pivot Tables", "SQL"] and summary["descriptions_fetched"] == 1


# The years of experience come from the posting too: the search results say 0 for every job, so without this the
# experience filter never removed a job and the "missing a requirement" rule never saw a shortfall
def test_a_jobs_years_of_experience_come_from_its_posting_and_are_filtered_on():
    bare = listing(1, description="", min_years_experience=0)
    posting = {"description": "Minimum 2 years of experience.", "listed_skills": ["SQL"], "is_open": True,
               "min_years_experience": 2}
    search, _, _ = service([bare], details=posting)
    jobs, _ = search.find_jobs(PROFILE, FILTERS)
    assert jobs[0]["min_years_experience"] == 2
    one_year = {**FILTERS, "max_years_experience": 1}  # "Jobs that ask for up to 1 year"
    assert search.rank_and_save(PROFILE, jobs, one_year, "cv.docx")["jobs"] == []
    assert len(search.rank_and_save(PROFILE, jobs, FILTERS, "cv.docx")["jobs"]) == 1


def test_a_posting_that_cannot_be_fetched_keeps_what_the_search_gave():
    bare = listing(1, description="", listed_skills=["SQL"])
    search, _, _ = service([bare], details=None)  # the details call fails
    seen = []
    search._SearchService__ai._responder = lambda record, task: seen.append(record) or answers(record, task)
    jobs, summary = search.find_jobs(PROFILE, FILTERS)
    assert seen[0]["jobs"][0]["listed_skills"] == ["SQL"] and summary["descriptions_fetched"] == 0 and len(jobs) == 1


def test_enough_jobs_that_fit_stop_the_search_at_the_first_step():
    search, portal, _ = service([listing(number) for number in range(TOP_N)])
    jobs, summary = search.find_jobs(PROFILE, FILTERS)
    assert summary["search_step"] == "all" and preference_steps(portal) == ["all"]
    assert len(jobs) == TOP_N and not any(job.get("close_match") for job in jobs)


# Fewer than TOP_N jobs fit every preference: the wider steps are searched too, and the jobs only they find are
# close matches, ranked after the exact ones with the filter each one misses
def test_too_few_jobs_that_fit_are_topped_up_with_close_matches():
    part_time = listing(2, employment_types=["Part Time"])
    search, portal, _ = service([listing(1), part_time])
    jobs, summary = search.find_jobs(PROFILE, FILTERS)
    assert summary["search_step"] == "all" and summary["close_matches"] == 1
    assert [(job["title"], job.get("close_match", False)) for job in jobs] == [("Job 1", False), ("Job 2", True)]
    # "job type" still filters the part-time job out and adds nothing new, so "general" is the step that finds it
    assert preference_steps(portal) == ["all", "job type", "general"]
    ranked = search.rank_and_save(PROFILE, jobs, FILTERS, "cv.docx")
    assert [(job["title"], job.get("close_reason")) for job in ranked["jobs"]] == [("Job 1", None), ("Job 2", "job type")]
    assert ranked["newly_saved"] == 2


def test_close_matches_are_capped_so_the_ai_reads_only_a_few_more_listings():
    wide = [listing(1)] + [listing(number, employment_types=["Part Time"]) for number in range(2, 20)]
    search, _, _ = service(wide)
    jobs, summary = search.find_jobs(PROFILE, FILTERS)
    assert summary["close_matches"] == JOBS_PER_SEARCH and len(jobs) == 1 + JOBS_PER_SEARCH


def test_close_matches_never_push_out_an_exact_match():
    search, _, _ = service([listing(number) for number in range(3)] + [listing(9, employment_types=["Part Time"])])
    jobs, _ = search.find_jobs(PROFILE, FILTERS)
    ranked = search.rank_and_save(PROFILE, jobs, FILTERS, "cv.docx", top_n=3)
    assert [job["title"] for job in ranked["jobs"]] == ["Job 0", "Job 1", "Job 2"]  # the list is full without them


def test_saving_the_top_jobs_says_how_many_were_new_and_a_failed_save_gives_none():
    search, _, storage = service([listing(1), listing(2)])
    jobs, summary = search.find_jobs(PROFILE, FILTERS)
    saved = search.rank_and_save(PROFILE, jobs, FILTERS, "resume.pdf", 5)
    assert saved["save_failed"] is False and saved["newly_saved"] == 2
    nothing_fits = {**FILTERS, "min_salary": 90000}  # no job to keep, so nothing is saved and nothing fails
    empty = search.rank_and_save(PROFILE, jobs, nothing_fits, "resume.pdf", 5)
    assert empty["jobs"] == [] and empty["newly_saved"] == 0 and empty["save_failed"] is False
    storage.fail_writes = True
    failed = search.rank_and_save(PROFILE, jobs, FILTERS, "resume.pdf", 5)
    assert failed["save_failed"] is True and failed["newly_saved"] == 0 and len(failed["jobs"]) == 2


def missing_profile() -> dict:
    return {"highest_qualification": "None", "qualification_detail": "", "core_skills": [],
            "search_keywords": ["Data Analyst"]}


def reading(profile: dict | None) -> SearchService:
    return SearchService(ScriptedAIManager(lambda record, task: profile), LogicManager(),
                         DataManager(InMemoryStorage(), FakePortal([])))


def test_missing_details_come_from_the_form_fields():
    filled = reading(missing_profile()).read_profile("resume text", {"qualification": "Diploma", "skills": ["SQL", "Python"]})
    assert filled["highest_qualification"] == "Diploma" and filled["qualification_detail"] == "Diploma"
    assert filled["core_skills"] == ["SQL", "Python"]


def test_form_fields_never_overwrite_what_the_ai_found_or_accept_unknown_levels():
    found = {"highest_qualification": "Master's", "qualification_detail": "MSc", "core_skills": ["Go"],
             "search_keywords": ["Data Analyst"]}
    assert reading(dict(found)).read_profile("text", {"qualification": "Diploma", "skills": ["SQL"]}) == found
    assert reading(missing_profile()).read_profile("text", {"qualification": "Wizard", "skills": []}
                                                    )["highest_qualification"] == "None"
    assert reading(missing_profile()).read_profile("text")["core_skills"] == []  # no fallback at all


def test_a_profile_the_ai_could_not_read_is_none():
    assert reading(None).read_profile("resume text", {"qualification": "Diploma", "skills": ["SQL"]}) is None


def test_the_default_number_of_jobs_kept_is_the_top_n():
    search, _, _ = service([listing(number) for number in range(1, 8)])
    jobs, _ = search.find_jobs(PROFILE, FILTERS)
    assert len(search.rank_and_save(PROFILE, jobs, FILTERS, "resume.pdf")["jobs"]) == TOP_N
