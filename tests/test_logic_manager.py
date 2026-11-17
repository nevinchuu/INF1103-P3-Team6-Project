"""Tests for the logic layer (src/logic_layer/logic_manager.py, tailoring_guard.py): filtering, ranking rules, outcomes and
tailoring checks.

Run with python -m pytest. No AI, network or files are involved: jobs are hard-coded sample AI records.
"""

import pytest

from logic_layer import logic_manager
from logic_layer.logic_manager import LogicManager

LOGIC = logic_manager.LogicManager()

GOOD_URL = "https://www.mycareersfuture.gov.sg/job/it/data-analyst-acme-5365191b6ddd018b70f7b8149c0a20ca"

# Filters that let everything through, in the shape io_manager.prompt_filters returns
NO_FILTERS = {
    "min_salary": 0,
    "max_salary": 50000,
    "max_years_experience": 60,
    "job_type": "Any",
    "work_arrangement": "Any",
}

PROFILE = {"highest_qualification": "Diploma", "years_of_experience": 1}


def make_job(**changes):
    """A job record like ai_manager returns, with only the given fields changed."""
    job = {
        "title": "Data Analyst",
        "job_url": GOOD_URL,
        "min_salary": 4000,
        "max_salary": 6000,
        "min_years_experience": 0,
        "employment_types": ["Full Time"],
        "work_arrangement": "Onsite",
        "min_education": "Not specified",
        "required_skills": ["Python", "SQL", "Excel", "Tableau"],
        "matched_skills": [],
    }
    job.update(changes)
    return job


def matched(*skills):
    """matched_skills entries for the given job skills."""
    return [{"job_skill": skill, "candidate_skill": skill} for skill in skills]


def reason_removed(job: dict, filters: dict) -> str:
    """Why the job is removed, e.g. "salary", or "" if it is kept (through the public count_removed)."""
    counts = LOGIC.count_removed([job], filters)
    assert LOGIC.passes_filters(job, filters) == (not counts)
    return next(iter(counts), "")


# 1. A job with a link that is not an https MyCareersFuture link is removed (the link rules are tested with the
# InputValidator, in test_input_validator.py)
def test_a_job_with_a_bad_link_is_removed():
    assert reason_removed(make_job(), NO_FILTERS) == ""
    assert reason_removed(make_job(job_url="N/A"), NO_FILTERS) == "bad link"
    assert reason_removed(make_job(job_url=GOOD_URL.replace("https", "http")), NO_FILTERS) == "bad link"
    # Only an https MyCareersFuture link counts: not a look-alike host, a missing link or one that can't be read
    for bad in ("https://mycareersfuture.gov.sg.evil.com/job/1", None, "https://[bad"):
        assert reason_removed(make_job(job_url=bad), NO_FILTERS) == "bad link"


# 2. Salary: a job is removed only if its whole pay range is outside the user's range
def test_salary_filter_keeps_overlapping_and_unstated_pay():
    filters = {**NO_FILTERS, "min_salary": 5000, "max_salary": 8000}
    assert reason_removed(make_job(min_salary=4000, max_salary=6000), filters) == ""
    assert reason_removed(make_job(min_salary=3000, max_salary=4500), filters) == "salary"
    assert reason_removed(make_job(min_salary=9000, max_salary=12000), filters) == "salary"
    # 0 means the job doesn't state a salary, so it is kept
    assert reason_removed(make_job(min_salary=0, max_salary=0), filters) == ""


# 3. Job type and work arrangement, including the special cases
def test_job_type_and_work_arrangement_filters():
    full_time = {**NO_FILTERS, "job_type": "Full Time"}
    # "Permanent" jobs on MyCareersFuture are full-time
    assert reason_removed(make_job(employment_types=["Permanent"]), full_time) == ""
    assert reason_removed(make_job(employment_types=["Contract"]), full_time) == "job type"

    remote = {**NO_FILTERS, "work_arrangement": "Remote"}
    assert reason_removed(make_job(work_arrangement="Remote"), remote) == ""
    assert reason_removed(make_job(work_arrangement="Onsite"), remote) == "work arrangement"
    # A job that doesn't say is kept rather than wrongly removed
    assert reason_removed(make_job(work_arrangement="Not specified"), remote) == ""


# 4. count_removed counts each removed job once, under the first filter it fails
def test_count_removed_groups_jobs_by_first_failed_filter():
    filters = {**NO_FILTERS, "min_salary": 5000, "max_years_experience": 2}
    jobs = [
        make_job(),  # kept
        make_job(job_url="N/A"),  # bad link
        make_job(max_salary=4000),  # salary
        make_job(min_years_experience=5),  # experience
        make_job(max_salary=4000, min_years_experience=5),  # fails both: counted under salary only
    ]
    assert LOGIC.count_removed(jobs, filters) == {"bad link": 1, "salary": 2, "experience": 1}
    assert LOGIC.count_removed([make_job()], filters) == {}


# 5. Ranking: jobs the candidate qualifies for come first, then by number of skills matched
def test_filter_and_rank_orders_best_matches_first():
    jobs = [
        make_job(title="Too senior", min_years_experience=5,
                 matched_skills=matched("Python", "SQL", "Excel", "Tableau")),
        make_job(title="Needs a degree", min_education="Bachelor's", matched_skills=matched("Python", "SQL", "Excel")),
        make_job(title="Half match", matched_skills=matched("Python", "SQL")),
        make_job(title="Best match", matched_skills=matched("Python", "SQL", "Excel")),
        make_job(title="Filtered out", employment_types=["Contract"],
                 matched_skills=matched("Python", "SQL", "Excel", "Tableau")),
    ]
    filters = {**NO_FILTERS, "job_type": "Full Time"}

    ranked = LOGIC.filter_and_rank(PROFILE, jobs, filters, top_n=4)
    assert [job["title"] for job in ranked] == ["Best match", "Half match", "Needs a degree", "Too senior"]

    # top_n limits how many are returned
    top_two = LOGIC.filter_and_rank(PROFILE, jobs, filters, top_n=2)
    assert [job["title"] for job in top_two] == ["Best match", "Half match"]


# 6. Seniority: a job far above the candidate's level (FAR_ABOVE_LEVELS or more) ranks below jobs
#    nearer it, even with more skills matched; a smaller gap only breaks ties
def test_job_above_candidate_level_ranks_lower():
    profile = {**PROFILE, "seniority_level": "Fresh/entry level"}
    jobs = [
        make_job(title="Senior role", position_levels=["Senior Executive"], matched_skills=matched("Python", "SQL")),
        make_job(title="Entry role", position_levels=["Fresh/entry level", "Junior Executive"],
                 matched_skills=matched("Python", "SQL")),
        make_job(title="Better match, senior", position_levels=["Manager"],
                 matched_skills=matched("Python", "SQL", "Excel")),
        make_job(title="Better match, junior", position_levels=["Junior Executive"],
                 matched_skills=matched("Python", "SQL", "Excel")),
    ]
    ranked = LOGIC.filter_and_rank(profile, jobs, NO_FILTERS, top_n=4)
    # Junior Executive is 2 levels up: near enough, so its extra skill puts it first.
    # Senior Executive and Manager are 4+ levels up, so they come after both nearer jobs
    assert [job["title"] for job in ranked] == ["Better match, junior", "Entry role", "Better match, senior", "Senior role"]
    # Unknown or missing levels count as no gap
    executive = {**PROFILE, "seniority_level": "Executive"}
    assert LogicManager.job_fit(executive, make_job(position_levels=["Unknown"]))["level_gap"] == 0
    assert LogicManager.job_fit(executive, make_job())["level_gap"] == 0


# 7. Within the same outcome, more matched skills ranks first, even when a job with a short skill list has a higher
# share (both Fair here; a Strong match always ranks above a Fair one, see test_a_strong_match_always_ranks_above_a_fair_one)
def test_within_the_same_outcome_more_matched_skills_beats_a_higher_share():
    jobs = [
        make_job(title="2 of 4", required_skills=["Python", "SQL", "Excel", "Java"], matched_skills=matched("Python", "SQL")),
        make_job(title="3 of 7", required_skills=["Python", "SQL", "Excel", "Tableau", "R", "Git", "AWS"],
                 matched_skills=matched("Python", "SQL", "Excel")),
    ]
    ranked = LOGIC.filter_and_rank(PROFILE, jobs, NO_FILTERS, top_n=2)
    assert [(job["title"], job["match_outcome"]) for job in ranked] == [("3 of 7", "Fair match"), ("2 of 4", "Fair match")]


# 8. Jobs that match none of the candidate's skills are not shown
def test_jobs_with_no_matched_skills_are_hidden():
    jobs = [make_job(title="Match", matched_skills=matched("Python")), make_job(title="No match")]
    assert [job["title"] for job in LOGIC.filter_and_rank(PROFILE, jobs, NO_FILTERS, top_n=5)] == ["Match"]


# 9. A tailored resume shows a matched skill if the AI's quote is really in it, or else if the
#    candidate's own skill name is; a made-up quote alone doesn't count
def test_the_skills_a_tailored_resume_shows_are_checked_by_quote_and_falls_back_to_skill_names():
    card = [{"job_skill": "Spreadsheets", "candidate_skill": "Microsoft Excel"},
            {"job_skill": "SQL", "candidate_skill": "SQL"},
            {"job_skill": "Python", "candidate_skill": "Python"}]
    parts = ["Technical: Microsoft Excel, SQL", "Wrote SQL queries to reconcile payments"]
    evidence = [{"job_skill": "SQL", "evidence": "Wrote SQL queries"},
                {"job_skill": "Spreadsheets", "evidence": "a quote that is not in the resume"},
                {"job_skill": "Python", "evidence": "Built Python scripts"}]
    job = make_job(resume="cv.pdf", matched_skills=card)
    tailored = {"removed_skills": [], "skill_evidence": evidence, "parts": parts}
    assert LOGIC.tailoring_skills(job, True, [], tailored)["shown"] == {"SQL": "Wrote SQL queries",
                                                                        "Spreadsheets": "Microsoft Excel"}


# 10. Match outcome: a job the candidate misses a requirement for is a stretch, whatever the skills;
#     otherwise STRONG_MATCH_SHARE of the key skills (3 of 4 here) makes a strong match
def test_match_outcome_combines_requirements_and_skills():
    profile = {**PROFILE, "seniority_level": "Fresh/entry level"}
    strong = make_job(matched_skills=matched("Python", "SQL", "Excel"))
    fair = make_job(matched_skills=matched("Python"))
    assert LOGIC.match_outcome(profile, strong)[0] == logic_manager.OUTCOME_STRONG
    assert LOGIC.match_outcome(profile, fair)[0] == logic_manager.OUTCOME_FAIR

    every_skill = matched("Python", "SQL", "Excel", "Tableau")
    short_on_experience = LOGIC.match_outcome(profile, make_job(min_years_experience=3, matched_skills=every_skill))
    assert short_on_experience == (logic_manager.OUTCOME_STRETCH, "This job needs 2 more years of experience.")
    needs_degree = make_job(min_education="Bachelor's", matched_skills=every_skill)
    assert LOGIC.match_outcome(profile, needs_degree)[0] == logic_manager.OUTCOME_STRETCH
    far_above = make_job(position_levels=["Manager"], matched_skills=every_skill)
    assert LOGIC.match_outcome(profile, far_above)[0] == logic_manager.OUTCOME_STRETCH
    # Several gaps are all named
    both = make_job(min_years_experience=2, min_education="Bachelor's", matched_skills=every_skill)
    assert LOGIC.match_outcome(profile, both)[1] == ("This job needs 1 more year of experience "
                                                             "and asks for Bachelor's or higher.")

    # filter_and_rank gives every job it returns an outcome and a reason
    ranked = LOGIC.filter_and_rank(profile, [strong, fair], NO_FILTERS, top_n=2)
    assert [job["match_outcome"] for job in ranked] == [logic_manager.OUTCOME_STRONG, logic_manager.OUTCOME_FAIR]
    assert all(job["outcome_reason"] for job in ranked)


# 11. Search steps: every preference first, then the job type only (unless it is Any), then none
def test_search_steps_loosen_preferences_in_order():
    assert LOGIC.search_steps({**NO_FILTERS, "job_type": "Full Time"}) == ["all", "job type", "general"]
    assert LOGIC.search_steps(NO_FILTERS) == ["all", "general"]
    assert LOGIC.search_steps(None) == ["all", "general"]


# 12. Job keywords for tailoring: a skill the resume shows (word for word, or matched under another
#     name) may be used; one it doesn't show may not. Each skill is listed once
def test_job_keywords_split_shown_and_missing_skills():
    job = make_job(required_skills=["SQL", "Business Data Analysis", "Scala", "Java"],
                   listed_skills=["SQL", "Tableau"],
                   matched_skills=[{"job_skill": "Business Data Analysis", "candidate_skill": "Data Analysis"}])
    use, avoid = LOGIC.job_keywords(job, "Wrote SQL queries in JavaScript and Data Analysis")
    assert use == ["SQL", "Business Data Analysis"]
    assert avoid == ["Scala", "Java", "Tableau"]  # "Java" is not found inside "JavaScript"


def make_paragraph(paragraph_id, text, kind="bullet", chars_per_line=50):
    """A paragraph like io_manager.read_paragraphs returns."""
    return {"id": paragraph_id, "text": text, "kind": kind, "editable": True, "chars_per_line": chars_per_line}


# 13. Tailoring never empties a job's points: if every bullet under a heading would be removed,
#     none of them are, and a note says why; removing some of them is fine
def test_the_word_edits_never_empty_a_heading():
    paragraphs = [make_paragraph(0, "Data Analyst Intern\t2024", kind="entry"), make_paragraph(1, "Built dashboards"),
                  make_paragraph(2, "Wrote SQL"), make_paragraph(3, "Cashier\t2023", kind="entry"),
                  make_paragraph(4, "Served customers"), make_paragraph(5, "Handled payments")]
    edits = [{"paragraph": 1, "text": "", "reason": "off topic"}, {"paragraph": 2, "text": "", "reason": "off topic"},
             {"paragraph": 4, "text": "", "reason": "off topic"},
             {"paragraph": 5, "text": "Took card and cash payments", "reason": "clearer"}]
    kept, reasons, removed_skills, notes = LOGIC.ground_docx_edits(edits, paragraphs, "Took cash", [])
    assert kept == {4: "", 5: "Took card and cash payments"}
    assert notes == [{"kind": "bullet_group_kept", "heading": "Data Analyst Intern"}]
    assert removed_skills == [] and reasons[5] == "clearer"


# 14. Tailoring keeps the number of pages: a rewrite needing extra lines is kept only if shorter
#     rewrites free at least as many lines
def test_the_word_edits_keep_the_number_of_lines():
    paragraphs = [make_paragraph(1, "x" * 100), make_paragraph(2, "y" * 40)]
    longer = [{"paragraph": 2, "text": "z" * 120, "reason": "more detail"}]  # 1 line becomes 3, and nothing is freed
    kept, _, _, notes = LOGIC.ground_docx_edits(longer, paragraphs, "", [])
    assert kept == {} and notes == [{"kind": "edit_too_long", "start": "y" * 40}]
    # Removing the 2-line paragraph frees enough lines for the longer one
    both = [{"paragraph": 1, "text": "", "reason": "off topic"}, {"paragraph": 2, "text": "z" * 80, "reason": "more detail"}]
    kept, _, _, notes = LOGIC.ground_docx_edits(both, paragraphs, "", [])
    assert kept == {1: "", 2: "z" * 80} and notes == []


# 15. After a search step drops preferences, only the ones it kept still apply
def test_filters_for_step_keeps_only_the_preferences_still_applied():
    preferences = {**NO_FILTERS, "min_salary": 4000, "max_salary": 6000, "job_type": "Full Time", "work_arrangement": "Remote"}
    assert LOGIC.filters_for_step(preferences, "all") == preferences
    assert LOGIC.filters_for_step(preferences, "job type") == {**NO_FILTERS, "job_type": "Full Time"}
    assert LOGIC.filters_for_step(preferences, "general") == NO_FILTERS


# 16. Skills worth building: missing from 2 or more jobs, counted once per job, "SQL" and "sql" as one
def test_find_skill_gaps_counts_skills_missing_from_several_jobs():
    jobs = [make_job(missing_skills=["SQL", "sql ", "Excel"]), make_job(missing_skills=["SQL", "Tableau"]),
            make_job(missing_skills=["Excel"]), make_job()]
    # Equal counts keep the order the skills first appear in, so every run gives the same list
    assert LOGIC.find_skill_gaps(jobs) == [("SQL", 2), ("Excel", 2)]
    assert LOGIC.find_skill_gaps([make_job(missing_skills=["R"])]) == []


# 17. Every piece of text in a tailored resume, for checking which skills it shows
def test_resume_parts_lists_every_piece_of_text():
    resume = {"sections": [
        {"text": "Analyst aiming at data roles", "skill_groups": [{"label": "Technical", "skills": ["SQL", "Python"]}],
         "entries": []},
        {"text": "", "skill_groups": [], "entries": [
            {"title": "Intern", "organisation": "Acme", "subtitle": "Singapore", "bullets": ["Wrote SQL", "Built reports"]}]}]}
    assert LOGIC.resume_parts(resume) == ["Analyst aiming at data roles", "SQL", "Python", "",
                                                  "Intern", "Acme", "Singapore", "Wrote SQL", "Built reports"]


# 18. Ranking gives back copies with the outcome added, and leaves the caller's job records unchanged
def test_filter_and_rank_leaves_the_callers_jobs_unchanged():
    jobs = [make_job(matched_skills=matched("Python", "SQL", "Excel"))]
    ranked = LOGIC.filter_and_rank(PROFILE, jobs, NO_FILTERS, top_n=1)
    assert ranked[0]["match_outcome"] == logic_manager.OUTCOME_STRONG
    assert "match_outcome" not in jobs[0] and "outcome_reason" not in jobs[0]


# 19. An education level the logic layer doesn't know asks for nothing (no crash), and a job with
#     no key skills gets a plain reason instead of "0 of 0"
def test_match_outcome_handles_unknown_education_and_no_key_skills():
    profile = {**PROFILE, "seniority_level": "Fresh/entry level"}
    outcome = LOGIC.match_outcome(profile, make_job(min_education="Degree", matched_skills=matched("Python", "SQL", "Excel")))
    assert outcome[0] == logic_manager.OUTCOME_STRONG
    assert LOGIC.match_outcome(profile, make_job(required_skills=[])) == (logic_manager.OUTCOME_FAIR,
                                                                                  "You meet its requirements.")


# 20. Values the logic layer copies (so it never imports another layer) still match the other layers
def test_copied_values_still_match_the_other_layers():
    from ai_layer import ai_manager  # imported here: only this test needs the AI layer's lists
    assert logic_manager.EDUCATION_LEVELS == ai_manager.EDUCATION_LEVELS
    assert logic_manager.SENIORITY_LEVELS == ai_manager.SENIORITY_LEVELS
    from data_layer import data_manager
    assert data_manager.STRONG_MATCH == logic_manager.OUTCOME_STRONG


# 21. A job card's skills apply only to the resume that found the job; the others get the card without them
def test_a_job_card_applies_only_to_the_resume_that_found_it():
    job = make_job(resume="cv_a.pdf", matched_skills=[{"job_skill": "SQL", "candidate_skill": "SQL"}],
                   missing_skills=["Python"], required_skills=["SQL", "Python", "C++"])
    same_job, same_resume = LOGIC.resume_view(job, "cv_a.pdf")
    other_job, other_resume = LOGIC.resume_view(job, "cv_b.pdf")
    assert same_resume is True and same_job is job
    assert other_resume is False and other_job["matched_skills"] == []
    assert job["matched_skills"] == [{"job_skill": "SQL", "candidate_skill": "SQL"}]  # the caller's job is unchanged
    nothing_removed = {"removed_skills": [], "skill_evidence": [], "parts": ["Wrote SQL queries"]}
    assert LOGIC.tailoring_skills(job, True, [], nothing_removed) == {
        "matched": [{"job_skill": "SQL", "candidate_skill": "SQL"}], "not_added": ["Python"],
        "shown": {"SQL": "SQL"}}
    # A removed skill the job never asked for ("Rust") is not listed as one the job wants
    assert LOGIC.tailoring_skills(job, False, ["Python", "C++"], {**nothing_removed, "removed_skills": ["Rust", "C++"]}) == {
        "matched": [], "not_added": ["Python", "C++"], "shown": {}}


# ---------- the ranking rules, one by one (each takes a job's fit and returns a sort value) ----------

RANKED_PROFILE = {"highest_qualification": "Diploma", "years_of_experience": 1, "seniority_level": "Fresh/entry level"}


@pytest.mark.parametrize("place, better, worse", [
    (0, make_job(min_years_experience=1), make_job(min_years_experience=3)),  # experience
    (1, make_job(min_education="Diploma"), make_job(min_education="Master's")),  # education
    (2, make_job(position_levels=["Executive"]), make_job(position_levels=["Manager"])),  # far above the level
    (3, make_job(required_skills=["Python", "SQL"], matched_skills=matched("Python", "SQL")),  # Strong before Fair
     make_job(required_skills=["Python", "SQL", "Excel", "Tableau"], matched_skills=matched("Python"))),
    (4, make_job(matched_skills=matched("Python", "SQL", "Excel")), make_job(matched_skills=matched("Python"))),
    (5, make_job(required_skills=["Python", "SQL"], matched_skills=matched("Python", "SQL")),
     make_job(required_skills=["Python", "SQL", "Excel", "Tableau"], matched_skills=matched("Python", "SQL"))),
    (6, make_job(position_levels=["Junior Executive"]), make_job(position_levels=["Executive"])),  # level gap
])
def test_each_standard_key_sorts_the_better_fit_first(place, better, worse):
    assert LOGIC.sort_key(RANKED_PROFILE, better)[place] < LOGIC.sort_key(RANKED_PROFILE, worse)[place]


# The case that used to rank wrongly: 4 of 10 key skills (Fair, more skills matched) above 3 of 4 (Strong)
def test_a_strong_match_always_ranks_above_a_fair_one():
    fair = make_job(title="fair", required_skills=["Python", "SQL", "Excel", "Tableau", "R", "Java", "Git", "AWS",
                                                   "Spark", "Airflow"],
                    matched_skills=matched("Python", "SQL", "Excel", "Tableau"))
    strong = make_job(title="strong", required_skills=["Python", "SQL", "Excel", "Power BI"],
                      matched_skills=matched("Python", "SQL", "Excel"))
    stretch = make_job(title="stretch", min_years_experience=5, required_skills=["Python"], matched_skills=matched("Python"))
    ranked = LOGIC.filter_and_rank(RANKED_PROFILE, [fair, stretch, strong], NO_FILTERS, top_n=3)
    assert [(job["title"], job["match_outcome"]) for job in ranked] == [
        ("strong", "Strong match"), ("fair", "Fair match"), ("stretch", "Stretch")]


def test_a_job_with_no_key_skills_has_a_share_of_zero():
    job = make_job(required_skills=[], matched_skills=[])
    assert LogicManager.job_fit(PROFILE, job)["share"] == 0


def test_the_default_order_is_the_phase_1_order():
    # seven keys, and an earlier one beats every later one: a job short on experience ranks below a job with fewer skills
    assert len(LOGIC.sort_key(RANKED_PROFILE, make_job())) == 7
    short = make_job(title="short", min_years_experience=5, matched_skills=matched("Python", "SQL", "Excel"))
    fits = make_job(title="fits", matched_skills=matched("Python"))
    assert LOGIC.sort_key(RANKED_PROFILE, fits) < LOGIC.sort_key(RANKED_PROFILE, short)


# ---------- the combined multi-condition rule: experience, education, level and skills together ----------

def test_multi_condition_order_uses_experience_then_education_then_level_then_skills():
    profile = {"highest_qualification": "Diploma", "years_of_experience": 1, "seniority_level": "Fresh/entry level"}
    jobs = [
        make_job(title="many skills but needs 3 more years", min_years_experience=4,
                 matched_skills=matched("Python", "SQL", "Excel", "Tableau")),
        make_job(title="degree needed", min_education="Bachelor's", matched_skills=matched("Python", "SQL", "Excel")),
        make_job(title="meets all, 1 skill", matched_skills=matched("Python")),
        make_job(title="meets all, 2 skills", matched_skills=matched("Python", "SQL")),
    ]
    ranked = LOGIC.filter_and_rank(profile, jobs, NO_FILTERS, top_n=4)
    assert [job["title"] for job in ranked] == ["meets all, 2 skills", "meets all, 1 skill", "degree needed",
                                                "many skills but needs 3 more years"]
    outcomes = {job["title"]: job["match_outcome"] for job in ranked}
    assert outcomes["meets all, 2 skills"] == logic_manager.OUTCOME_FAIR
    assert outcomes["many skills but needs 3 more years"] == logic_manager.OUTCOME_STRETCH
    assert outcomes["degree needed"] == logic_manager.OUTCOME_STRETCH


# ---------- open/closed: a new rule changes the ranking without changing LogicManager ----------

def fewest_skills_first(fit: dict) -> float:
    """A made-up key for the test: the opposite of the standard "most matched skills first"."""
    return fit["matched"]


def most_skills_first(fit: dict) -> float:
    return -fit["matched"]


def test_a_new_rule_can_be_plugged_in_without_touching_the_manager():
    jobs = [make_job(title="two", matched_skills=matched("Python", "SQL")), make_job(title="one", matched_skills=matched("Python"))]
    custom = LogicManager(ranking_keys=[fewest_skills_first])
    assert [job["title"] for job in custom.filter_and_rank(PROFILE, jobs, NO_FILTERS)] == ["one", "two"]
    assert [job["title"] for job in LOGIC.filter_and_rank(PROFILE, jobs, NO_FILTERS)] == ["two", "one"]


def test_rule_order_is_the_priority_order():
    jobs = [make_job(title="more skills, needs experience", min_years_experience=3, matched_skills=matched("Python", "SQL")),
            make_job(title="fewer skills, fits", matched_skills=matched("Python"))]
    skills_first = LogicManager(ranking_keys=[most_skills_first])
    assert skills_first.filter_and_rank(PROFILE, jobs, NO_FILTERS)[0]["title"] == "more skills, needs experience"
    assert LOGIC.filter_and_rank(PROFILE, jobs, NO_FILTERS)[0]["title"] == "fewer skills, fits"


# ---------- edge cases ----------

def test_no_jobs_and_ties_are_handled():
    assert LOGIC.filter_and_rank(PROFILE, [], NO_FILTERS) == []
    twins = [make_job(title="first", matched_skills=matched("Python")), make_job(title="second", matched_skills=matched("Python"))]
    assert [job["title"] for job in LOGIC.filter_and_rank(PROFILE, twins, NO_FILTERS)] == ["first", "second"]  # stable


def test_add_requirements_keeps_only_matches_the_candidate_really_has():
    profile = {"core_skills": ["Python"]}
    listing = {"title": "Dev", "description": "long text", "job_url": GOOD_URL}
    answer = {"required_skills": ["Python", "C++"], "skill_matches": [
        {"job_skill": "Python", "candidate_skill": "Python"}, {"job_skill": "C++", "candidate_skill": "Rust"}]}
    record = LOGIC.add_requirements([listing, listing], [answer, None], profile)
    assert len(record) == 1 and "description" not in record[0] and "skill_matches" not in record[0]
    assert record[0]["matched_skills"] == matched("Python")
    assert record[0]["missing_skills"] == ["C++"]


# A posting asking for SQL and Python once showed SQL alone. When the AI names fewer than MIN_KEY_SKILLS, the
# employer's own skill tags fill the list (behaviours and praise-dressed behaviours left out), and a tag the
# candidate lists is a match
def test_too_few_key_skills_are_topped_up_from_the_postings_own_tags():
    profile = {"core_skills": ["SQL", "Python"]}
    listing = {"title": "Analyst", "description": "SQL and Python", "job_url": GOOD_URL,
               "listed_skills": ["SQL", "Team Player", "Strong Attention To Detail", "Internships", "python",
                                 "Excel", "Tableau"]}
    answer = {"required_skills": ["SQL"], "skill_matches": [{"job_skill": "SQL", "candidate_skill": "SQL"}]}
    record = LOGIC.add_requirements([listing], [answer], profile)[0]
    assert record["required_skills"] == ["SQL", "python", "Excel", "Tableau"]  # up to MIN_KEY_SKILLS (4)
    assert record["matched_skills"] == [{"job_skill": "SQL", "candidate_skill": "SQL"},
                                        {"job_skill": "python", "candidate_skill": "Python"}]
    assert record["missing_skills"] == ["Excel", "Tableau"]


# One skill under two names: a job listing "UAT" and "User Acceptance Testing" keeps it once, and a resume's
# "UAT" covers the job's full name even when the AI didn't pair them
def test_an_abbreviation_and_its_full_name_are_one_skill_when_matching():
    profile = {"core_skills": ["UAT", "SQL"]}
    listing = {"title": "Analyst", "description": "x", "job_url": GOOD_URL}
    answer = {"required_skills": ["User Acceptance Testing", "SQL", "UAT Testing", "Jira", "Python"],
              "skill_matches": [{"job_skill": "SQL", "candidate_skill": "SQL"}]}
    record = LOGIC.add_requirements([listing], [answer], profile)[0]
    assert record["required_skills"] == ["User Acceptance Testing", "SQL", "Jira", "Python"]
    assert record["matched_skills"] == [{"job_skill": "User Acceptance Testing", "candidate_skill": "UAT"},
                                        {"job_skill": "SQL", "candidate_skill": "SQL"}]
    assert record["missing_skills"] == ["Jira", "Python"]


def test_skill_gaps_count_an_abbreviation_and_its_full_name_as_one():
    gaps = LOGIC.find_skill_gaps([{"missing_skills": ["UAT"]}, {"missing_skills": ["User Acceptance Testing"]}])
    assert gaps == [("UAT", 2)]


def test_enough_key_skills_from_the_ai_are_not_topped_up():
    listing = {"title": "Analyst", "description": "x", "job_url": GOOD_URL, "listed_skills": ["Jira", "Git"]}
    answer = {"required_skills": ["SQL", "Python", "Excel", "KYC"], "skill_matches": []}
    record = LOGIC.add_requirements([listing], [answer], {"core_skills": []})[0]
    assert record["required_skills"] == ["SQL", "Python", "Excel", "KYC"]


# Each job shows at most MAX_KEY_SKILLS (5) key skills: the AI lists the most important first, so the rest are cut
def test_a_job_keeps_its_five_most_important_key_skills():
    listing = {"title": "Analyst", "description": "x", "job_url": GOOD_URL}
    answer = {"required_skills": ["SQL", "Python", "Team Player", "Excel", "KYC", "Power BI", "Tableau", "Git"],
              "skill_matches": [{"job_skill": "Tableau", "candidate_skill": "Tableau"}]}
    record = LOGIC.add_requirements([listing], [answer], {"core_skills": ["Tableau"]})[0]
    assert record["required_skills"] == ["SQL", "Python", "Excel", "KYC", "Power BI"]  # Team Player is not a skill
    assert record["matched_skills"] == []  # Tableau was cut, so its match goes with it


def is_learnable(name: str) -> bool:
    """Whether a tag survives add_requirements as one of a job's required skills."""
    listing = {"title": "Analyst", "description": "long text", "job_url": GOOD_URL, "min_years_experience": 0}
    answer = {"required_skills": [name], "min_education": "Not specified", "skill_matches": []}
    return LOGIC.add_requirements([listing], [answer], {"core_skills": ["Python"]})[0]["required_skills"] == [name]


@pytest.mark.parametrize("name", [
    "SQL", "Power BI", "ETL", "Financial Modelling", "Node.js", "KYC",
    "Data Visualisation", "Agile", "Machine Learning",
    # No field is privileged here: a lab or engineering resume's tools pass the same check as a data one's,
    # because the rule names no tools and no industry
    "HPLC", "PCR", "Mass Spectrometry", "Cell Culture", "GMP", "ISO 13485", "AutoCAD", "Solidworks",
])
def test_a_named_tool_or_technique_is_a_learnable_skill(name):
    assert is_learnable(name)


@pytest.mark.parametrize("name", [
    "Reporting", "Presentation", "Presentation Skills", "Key Performance Indicators", "KPIs",
    "Ad-Hoc Analysis", "Ad Hoc Analysis", "Requirements Gathering", "Project Timeline",
    "Stakeholder Management", "Written Communication", "Process Improvement",
])
def test_an_activity_with_nothing_to_learn_is_not_a_learnable_skill(name):
    """These read like skills, unlike "Team Player", but name no tool, standard or technique, so there is
    no course to take and nothing to put in a roadmap."""
    assert not is_learnable(name)


@pytest.mark.parametrize("name", [
    "Work in A Fast Paced Environment", "Able to Work Independently", "Willing to Learn",
    "Team Player", "Communication", "Communication Skills", "Attention to Detail",
    "Time Management", "Problem Solving", "Detail-Oriented", "Positive Attitude",
    "A proven track record of delivering results under pressure every single day",  # too long to be a skill
    "", "   ",
])
def test_a_way_of_behaving_at_work_is_not_a_learnable_skill(name):
    assert not is_learnable(name)


def test_tags_that_are_not_skills_are_dropped_before_the_skill_share_is_counted():
    """MyCareersFuture tags behaviours as skills. Counting them used to dilute share = matched / required,
    pushing a job down for asking the candidate to be a team player."""
    profile = {"core_skills": ["Python", "SQL"]}
    listing = {"title": "Analyst", "description": "long text", "job_url": GOOD_URL,
               "min_years_experience": 0}
    answer = {"required_skills": ["Python", "SQL", "Work in A Fast Paced Environment", "Team Player"],
              "min_education": "Not specified",
              "skill_matches": [{"job_skill": "Python", "candidate_skill": "Python"},
                                {"job_skill": "SQL", "candidate_skill": "SQL"}]}
    record = LOGIC.add_requirements([listing], [answer], profile)[0]

    assert record["required_skills"] == ["Python", "SQL"]  # the behaviours never reach the domain rules
    assert record["missing_skills"] == []  # and are not offered as something to go and learn
    fit = LogicManager.job_fit(PROFILE, record)
    assert (fit["matched"], fit["required"], fit["share"]) == (2, 2, 1.0)


def test_a_lab_candidates_own_tools_survive_the_same_filter_a_data_candidates_do():
    """The filter names no tools and no industry, so a science resume keeps its field's skills and loses only
    the generic activity, exactly as a data resume does."""
    profile = {"core_skills": ["PCR"]}
    listing = {"title": "Research Associate", "description": "long text", "job_url": GOOD_URL,
               "min_years_experience": 0}
    answer = {"required_skills": ["PCR", "HPLC", "Cell Culture", "GMP", "Reporting", "Team Player"],
              "min_education": "Not specified",
              "skill_matches": [{"job_skill": "PCR", "candidate_skill": "PCR"}]}
    record = LOGIC.add_requirements([listing], [answer], profile)[0]

    assert record["required_skills"] == ["PCR", "HPLC", "Cell Culture", "GMP"]
    assert record["missing_skills"] == ["HPLC", "Cell Culture", "GMP"]  # what the what-if list would offer


# The tailoring guard finds a skill however the AI spells it, and blocks common tools the job doesn't list
@pytest.mark.parametrize("skill, rewrite", [
    ("APIs", "Built a REST API for the shop"), ("Power BI", "Made PowerBI dashboards"),
    ("Machine Learning", "Trained machine-learning models"), ("Node.js", "Wrote a Node JS service"),
    ("Tableau", "Shared Tableaus each week"), ("Tableau", "Shared TABLEAU reports"),
])
def test_the_guard_blocks_spelling_variants_of_a_missing_skill(skill, rewrite):
    paragraphs = [{"id": 0, "kind": "bullet", "text": "Served customers at the counter", "editable": True}]
    edits = [{"paragraph": 0, "text": rewrite, "reason": "aims at the job"}]
    kept, _, removed, _ = LogicManager().ground_docx_edits(edits, paragraphs, "Served customers at the counter", [skill])
    assert kept == {} and skill in removed


def test_the_guard_blocks_common_tools_the_job_does_not_list():
    paragraphs = [{"id": 0, "kind": "bullet", "text": "Built reports in Excel", "editable": True}]
    edits = [{"paragraph": 0, "text": "Built reports in Excel using Docker, AWS and Kubernetes", "reason": "tools"}]
    kept, _, removed, _ = LogicManager().ground_docx_edits(edits, paragraphs, "Built reports in Excel", ["Tableau"])
    assert kept == {} and set(removed) == {"Docker", "AWS", "Kubernetes"}


def test_the_guard_keeps_skills_and_everyday_words_the_resume_has():
    paragraphs = [{"id": 0, "kind": "bullet", "text": "Built Power BI reports", "editable": True, "chars_per_line": 100}]
    edits = [{"paragraph": 0, "text": "Built PowerBI reports to go to market faster", "reason": "clearer"}]
    kept, _, removed, _ = LogicManager().ground_docx_edits(edits, paragraphs, "Built Power BI reports", ["Tableau"])
    assert kept == {0: "Built PowerBI reports to go to market faster"} and removed == []
    assert not LogicManager().job_keywords({"required_skills": ["Java"]}, "JavaScript developer")[0]


# A rewrite can't upgrade a qualification: aimed at a job asking for a degree, the AI turned a diploma
# graduate's profile into "Degree holder", which no number or skill check catches
DIPLOMA_RESUME = "PROFILE\nDiploma in Financial Technology graduate. Comfortable with SQL and Excel.\nGCE O-Level"


@pytest.mark.parametrize("rewrite", [
    "Degree holder in Financial Technology. Proficient in SQL and Excel.",
    "Bachelor's graduate in Financial Technology with SQL and Excel.",
    "Diploma graduate working towards an MBA, using SQL and Excel.",
])
def test_the_guard_keeps_a_profile_that_claims_a_qualification_the_resume_lacks(rewrite):
    paragraphs = [{"id": 1, "kind": "text", "text": DIPLOMA_RESUME.splitlines()[1], "editable": True, "chars_per_line": 100}]
    kept, _, removed, notes = LogicManager().ground_docx_edits(
        [{"paragraph": 1, "text": rewrite, "reason": "aims at the job"}], paragraphs, DIPLOMA_RESUME, [])
    assert kept == {} and removed == [] and notes[0]["kind"] == "edit_adds_qualification"


def test_the_guard_keeps_a_rewrite_that_names_only_the_resumes_own_qualifications():
    paragraphs = [{"id": 1, "kind": "text", "text": DIPLOMA_RESUME.splitlines()[1], "editable": True, "chars_per_line": 100}]
    rewrite = "Financial Technology diploma graduate using SQL and Excel."
    kept, _, _, notes = LogicManager().ground_docx_edits(
        [{"paragraph": 1, "text": rewrite, "reason": "leads with SQL"}], paragraphs, DIPLOMA_RESUME, [])
    assert kept == {1: rewrite} and notes == []


def test_a_tailored_pdf_resume_drops_only_the_sentence_that_claims_a_qualification():
    tailored = {"name": "", "contact": "", "sections": [
        {"heading": "Profile", "kind": "text", "text": "Degree holder skilled in SQL. Comfortable with SQL and Excel.",
         "skill_groups": [], "entries": []},
        {"heading": "Education", "kind": "entries", "text": "", "skill_groups": [], "entries": [
            {"title": "Diploma in Financial Technology", "organisation": "", "subtitle": "", "dates": "",
             "bullets": ["Studied SQL", "Took Master-level modules"]}]}]}
    resume, _, notes = LogicManager().ground_tailored_resume(tailored, DIPLOMA_RESUME, [])
    profile, education = resume["sections"]
    assert "Degree" not in profile["text"] and "Comfortable with SQL and Excel." in profile["text"]
    assert education["entries"][0]["bullets"] == ["Studied SQL"]  # nothing close in the resume, so the point goes
    assert all(note["kind"] == "sentences_checked" and note["qualifications"] for note in notes)


# Found in a run with a sample teacher's PDF resume: a summary that added "Tuition" lost the whole section, a point
# lost its original wording, and the resume's own languages ("English: Native") were dropped as if made up
TEACHER_RESUME = """Terrance Ryan
PROFESSIONAL SUMMARY
Skilled high school math teacher with innovative and engaging teaching style
demonstrated over seven years of academic experience. Caring and hardworking
professional with exceptional work ethic.
WORK HISTORY
September 2011 - Current | High School Math Teacher
• Provide meaningful math instruction to improve the math skills of children.
• Assess submitted class assignments, determining grades and review work with
struggling learners.
LANGUAGES
English: Native | Spanish: Upper Intermediate (B2) | Italian: Elementary (A2)"""


def teacher_draft() -> dict:
    return {"name": "Terrance Ryan", "contact": "", "sections": [
        {"heading": "PROFESSIONAL SUMMARY", "kind": "text", "skill_groups": [], "entries": [],
         "text": "Skilled high school math teacher with an engaging teaching style and seven years of academic "
                 "experience. Experienced in tuition and student engagement for exam preparation."},
        {"heading": "WORK HISTORY", "kind": "entries", "text": "", "skill_groups": [], "entries": [
            {"title": "High School Math Teacher", "organisation": "", "subtitle": "", "dates": "September 2011 - Current",
             "bullets": ["Provided meaningful math tuition to improve the math skills of children.",
                         "Assess submitted class assignments, determining grades and review work with struggling learners."]}]},
        {"heading": "LANGUAGES", "kind": "skills", "text": "", "entries": [], "skill_groups": [
            {"label": "", "skills": ["English: Native", "Spanish: Upper Intermediate (B2)", "Italian: Elementary (A2)"]}]}]}


def test_a_pdf_summary_keeps_its_good_sentences_and_a_point_goes_back_to_its_own_wording():
    resume, removed, notes = LogicManager().ground_tailored_resume(teacher_draft(), TEACHER_RESUME, ["Tuition"])
    summary, work, languages = resume["sections"]
    assert summary["text"].startswith("Skilled high school math teacher") and "tuition" not in summary["text"].lower()
    assert work["entries"][0]["bullets"] == [
        "Provide meaningful math instruction to improve the math skills of children.",  # the resume's own point
        "Assess submitted class assignments, determining grades and review work with struggling learners."]
    assert removed == ["Tuition"] and {note["where"] for note in notes} == {"PROFESSIONAL SUMMARY", "High School Math Teacher"}


# Rewordings seen in run-throughs that use only facts from the resume but recombine them: a tool the resume names
# for other work moved into a point, or the work and skill made to sound stronger. Each keeps its original
FINTECH_RESUME = ("Diploma in Financial Technology graduate with a 6-month data analytics internship. Comfortable "
                  "with Python, SQL and Excel for financial data analysis and reporting.\n"
                  "Wrote SQL queries to extract and reconcile daily payment transactions across 3 settlement systems\n"
                  "Automated a weekly merchant performance report in Python (pandas) and Excel\n"
                  "Built Power BI dashboards tracking transaction volume, failure rates and chargebacks for the "
                  "operations team\nTechnical: Python, pandas, SQL, Microsoft Excel, Power BI, Tableau, Git")
FINTECH_LINES = FINTECH_RESUME.splitlines()


def docx_check(paragraph: int, kind: str, rewrite: str) -> tuple[dict, list[dict]]:
    paragraphs = [{"id": index, "kind": kind, "text": text, "editable": True, "chars_per_line": 200}
                  for index, text in enumerate(FINTECH_LINES)]
    kept, _, _, notes = LogicManager().ground_docx_edits(
        [{"paragraph": paragraph, "text": rewrite, "reason": "aims at the job"}], paragraphs, FINTECH_RESUME, [],
        ["SQL", "Tableau", "Power BI", "Microsoft Excel", "Python"])
    return kept, notes


@pytest.mark.parametrize("paragraph, kind, rewrite, tools, words", [
    (3, "bullet", "Built Tableau and Power BI dashboards tracking transaction volume, failure rates and chargebacks "
                  "for internal stakeholders", ["Tableau"], []),
    (1, "bullet", "Performed complex SQL scripting to reconcile daily payment transactions across 3 settlement systems",
     [], ["complex"]),
    (0, "text", "Diploma in Financial Technology graduate with a 6-month data analytics internship. Proficient in "
                "Python, SQL and Excel, with strong reporting skills.", [], ["proficient", "strong"]),
])
def test_a_rewording_that_recombines_real_facts_keeps_its_original(paragraph, kind, rewrite, tools, words):
    kept, notes = docx_check(paragraph, kind, rewrite)
    assert kept == {} and notes == [{"kind": "edit_recombines", "start": FINTECH_LINES[paragraph][:50],
                                     "tools": tools, "words": words}]


@pytest.mark.parametrize("paragraph, kind, rewrite", [
    (2, "bullet", "Automated a weekly merchant performance report in Python (pandas) and Microsoft Excel"),  # same tool
    (3, "bullet", "Built Power BI dashboards tracking transaction volume, failure rates and chargebacks for "
                  "internal stakeholders"),
    (1, "bullet", "Reconciled daily payment transactions across 3 settlement systems by writing SQL queries"),
    (0, "text", "Diploma in Financial Technology graduate comfortable with Python, SQL, Tableau and Excel for "
                "financial data analysis."),  # a summary may gather the resume's skills
])
def test_a_plain_rewording_of_the_same_facts_is_kept(paragraph, kind, rewrite):
    kept, notes = docx_check(paragraph, kind, rewrite)
    assert kept == {paragraph: rewrite} and notes == []


# Seen in a real run: the qualification word was right ("Diploma"), but its field was renamed
def test_a_renamed_qualification_keeps_its_original():
    kept, notes = docx_check(0, "text", "Diploma in Data Analytics and Financial Technology graduate with a 6-month "
                                        "data analytics internship. Comfortable with Python, SQL and Excel.")
    assert kept == {} and notes == [{"kind": "edit_adds_qualification", "start": FINTECH_LINES[0][:50],
                                     "qualifications": ["Diploma in Data Analytics and Financial Technology"]}]
    same = "Diploma in Financial Technology graduate with a 6-month data analytics internship, comfortable with SQL."
    assert docx_check(0, "text", same) == ({0: same}, [])
    joined = "Diploma in Financial Technology and a 6-month data analytics internship, comfortable with SQL."
    assert docx_check(0, "text", joined) == ({0: joined}, [])  # "and" after the name is not part of it


def test_a_pdf_sentence_that_recombines_real_facts_goes_back_to_the_resumes_own():
    draft = teacher_draft()
    draft["sections"][0]["text"] = ("Skilled high school math teacher with innovative and engaging teaching style "
                                    "demonstrated over seven years of academic experience and mastery of mathematics.")
    draft["sections"][1]["entries"][0]["bullets"][0] = ("Provide meaningful math instruction to improve the math "
                                                        "skills of children using effective classroom management.")
    resume, _, notes = LogicManager().ground_tailored_resume(draft, TEACHER_RESUME, [], ["Classroom management"])
    summary, work, _ = resume["sections"]
    assert "mastery" not in summary["text"] and summary["text"].startswith("Skilled high school math teacher")
    assert work["entries"][0]["bullets"][0] == "Provide meaningful math instruction to improve the math skills of children."
    assert [(note["where"], note["tools"], note["words"]) for note in notes] == [
        ("PROFESSIONAL SUMMARY", [], ["mastery"]), ("High School Math Teacher", ["Classroom management"], [])]


def test_the_resumes_own_items_with_colons_and_brackets_are_kept():
    resume, removed, _ = LogicManager().ground_tailored_resume(teacher_draft(), TEACHER_RESUME, [])
    assert resume["sections"][2]["skill_groups"][0]["skills"] == [
        "English: Native", "Spanish: Upper Intermediate (B2)", "Italian: Elementary (A2)"]
    assert removed == []
