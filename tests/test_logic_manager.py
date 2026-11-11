"""Tests for the logic layer (src/logic/logic_manager.py, tailoring_guard.py): filtering, ranking rules, outcomes and
tailoring checks.

Run with python -m pytest. No AI, network or files are involved: jobs are hard-coded sample AI records.
"""

import pytest

from io_layer import io_manager
from logic import logic_manager
from logic.logic_manager import LogicManager

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


# 1. Links: only https links on MyCareersFuture are accepted, checked without going online
def test_job_links_must_be_https_on_mycareersfuture():
    assert LOGIC.is_valid_job_url(GOOD_URL)
    assert not LOGIC.is_valid_job_url(GOOD_URL.replace("https", "http"))
    assert not LOGIC.is_valid_job_url("https://mycareersfuture.gov.sg.evil.com/job/1")
    assert not LOGIC.is_valid_job_url("N/A")
    assert not LOGIC.is_valid_job_url(None)
    assert LOGIC.filter_reason(make_job(job_url="N/A"), NO_FILTERS) == "bad link"


# 2. Salary: a job is removed only if its whole pay range is outside the user's range
def test_salary_filter_keeps_overlapping_and_unstated_pay():
    filters = {**NO_FILTERS, "min_salary": 5000, "max_salary": 8000}
    assert LOGIC.filter_reason(make_job(min_salary=4000, max_salary=6000), filters) == ""
    assert LOGIC.filter_reason(make_job(min_salary=3000, max_salary=4500), filters) == "salary"
    assert LOGIC.filter_reason(make_job(min_salary=9000, max_salary=12000), filters) == "salary"
    # 0 means the job doesn't state a salary, so it is kept
    assert LOGIC.filter_reason(make_job(min_salary=0, max_salary=0), filters) == ""


# 3. Job type and work arrangement, including the special cases
def test_job_type_and_work_arrangement_filters():
    full_time = {**NO_FILTERS, "job_type": "Full Time"}
    # "Permanent" jobs on MyCareersFuture are full-time
    assert LOGIC.filter_reason(make_job(employment_types=["Permanent"]), full_time) == ""
    assert LOGIC.filter_reason(make_job(employment_types=["Contract"]), full_time) == "job type"

    remote = {**NO_FILTERS, "work_arrangement": "Remote"}
    assert LOGIC.filter_reason(make_job(work_arrangement="Remote"), remote) == ""
    assert LOGIC.filter_reason(make_job(work_arrangement="Onsite"), remote) == "work arrangement"
    # A job that doesn't say is kept rather than wrongly removed
    assert LOGIC.filter_reason(make_job(work_arrangement="Not specified"), remote) == ""


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
    assert LOGIC.requirement_gaps(executive, make_job(position_levels=["Unknown"]))[2] == 0
    assert LOGIC.requirement_gaps(executive, make_job())[2] == 0


# 7. More matched skills ranks first, even when a job with a short skill list has a higher share
def test_more_matched_skills_beats_a_higher_share():
    jobs = [
        make_job(title="2 of 3", required_skills=["Python", "SQL", "Excel"], matched_skills=matched("Python", "SQL")),
        make_job(title="3 of 6", required_skills=["Python", "SQL", "Excel", "Tableau", "R", "Git"],
                 matched_skills=matched("Python", "SQL", "Excel")),
    ]
    assert [job["title"] for job in LOGIC.filter_and_rank(PROFILE, jobs, NO_FILTERS, top_n=2)] == ["3 of 6", "2 of 3"]


# 8. Jobs that match none of the candidate's skills are not shown
def test_jobs_with_no_matched_skills_are_hidden():
    jobs = [make_job(title="Match", matched_skills=matched("Python")), make_job(title="No match")]
    assert [job["title"] for job in LOGIC.filter_and_rank(PROFILE, jobs, NO_FILTERS, top_n=5)] == ["Match"]


# 9. A tailored resume shows a matched skill if the AI's quote is really in it, or else if the
#    candidate's own skill name is; a made-up quote alone doesn't count
def test_skills_shown_checks_quotes_and_falls_back_to_skill_names():
    card = [{"job_skill": "Spreadsheets", "candidate_skill": "Microsoft Excel"},
            {"job_skill": "SQL", "candidate_skill": "SQL"},
            {"job_skill": "Python", "candidate_skill": "Python"}]
    parts = ["Technical: Microsoft Excel, SQL", "Wrote SQL queries to reconcile payments"]
    evidence = [{"job_skill": "SQL", "evidence": "Wrote SQL queries"},
                {"job_skill": "Spreadsheets", "evidence": "a quote that is not in the resume"},
                {"job_skill": "Python", "evidence": "Built Python scripts"}]
    assert LOGIC.skills_shown(card, evidence, parts) == {"SQL": "Wrote SQL queries",
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
    """A paragraph like io_manager.read_docx_paragraphs returns."""
    return {"id": paragraph_id, "text": text, "kind": kind, "editable": True, "chars_per_line": chars_per_line}


# 13. Tailoring never empties a job's points: if every bullet under a heading would be removed,
#     none of them are, and a note says why; removing some of them is fine
def test_keep_bullet_groups_never_empties_a_heading():
    paragraphs = [make_paragraph(0, "Data Analyst Intern\t2024", kind="entry"), make_paragraph(1, "Built dashboards"),
                  make_paragraph(2, "Wrote SQL"), make_paragraph(3, "Cashier\t2023", kind="entry"),
                  make_paragraph(4, "Served customers"), make_paragraph(5, "Handled payments")]
    edits = {1: "", 2: "", 4: "", 5: "Took card and cash payments"}
    kept, notes = LOGIC.keep_bullet_groups(edits, paragraphs)
    assert kept == {4: "", 5: "Took card and cash payments"}
    assert notes == [{"kind": "bullet_group_kept", "heading": "Data Analyst Intern"}]
    assert edits == {1: "", 2: "", 4: "", 5: "Took card and cash payments"}  # the caller's edits are not changed


# 14. Tailoring keeps the number of pages: a rewrite needing extra lines is kept only if shorter
#     rewrites free at least as many lines
def test_fit_edits_to_page_count_keeps_the_number_of_lines():
    paragraphs = [make_paragraph(1, "x" * 100), make_paragraph(2, "y" * 40)]
    longer = {2: "z" * 120}  # 1 line becomes 3, and nothing is freed
    kept, notes = LOGIC.fit_edits_to_page_count(longer, paragraphs)
    assert kept == {} and notes == [{"kind": "edit_too_long", "start": "y" * 40}]
    # Removing the 2-line paragraph frees enough lines for the longer one
    kept, notes = LOGIC.fit_edits_to_page_count({1: "", 2: "z" * 80}, paragraphs)
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
    from ai import ai_manager  # imported here: only this test needs the AI layer's lists
    assert logic_manager.EDUCATION_LEVELS == ai_manager.EDUCATION_LEVELS == io_manager.EDUCATION_LEVELS
    assert logic_manager.SENIORITY_LEVELS == ai_manager.SENIORITY_LEVELS
    assert logic_manager.ANY == io_manager.ANY
    assert (logic_manager.MAX_SALARY, logic_manager.MAX_EXPERIENCE_YEARS) == (io_manager.MAX_SALARY,
                                                                              io_manager.MAX_EXPERIENCE_YEARS)
    from persistence import data_manager
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
    assert LOGIC.tailoring_skills(job, True, [], []) == {
        "matched": [{"job_skill": "SQL", "candidate_skill": "SQL"}], "not_added": ["Python"]}
    assert LOGIC.tailoring_skills(job, False, ["Python", "C++"], ["Rust"]) == {
        "matched": [], "not_added": ["Python", "C++", "Rust"]}


# ---------- the ranking rules, one by one (each takes a job's fit and returns a sort value) ----------

def fit(**changes) -> dict:
    values = {"experience_gap": 0, "education_ok": True, "level_gap": 0, "matched": 3, "required": 4}
    values.update(changes)
    values["share"] = values["matched"] / values["required"] if values["required"] else 0
    return values


@pytest.mark.parametrize("rule, better, worse", [
    (LogicManager.rank_by_experience_gap, fit(experience_gap=0), fit(experience_gap=2)),
    (LogicManager.rank_by_education, fit(education_ok=True), fit(education_ok=False)),
    (LogicManager.rank_by_far_above_level, fit(level_gap=3), fit(level_gap=logic_manager.FAR_ABOVE_LEVELS)),
    (LogicManager.rank_by_skill_count, fit(matched=4), fit(matched=2)),
    (LogicManager.rank_by_skill_share, fit(matched=2, required=2), fit(matched=2, required=4)),
    (LogicManager.rank_by_level_gap, fit(level_gap=1), fit(level_gap=2)),
])
def test_each_rule_sorts_the_better_fit_first(rule, better, worse):
    assert rule(better) < rule(worse)


def test_a_job_with_no_key_skills_has_a_share_of_zero():
    job = make_job(required_skills=[], matched_skills=[])
    assert LogicManager.job_fit(PROFILE, job)["share"] == 0


def test_the_default_order_is_the_phase_1_order():
    assert LOGIC.rules == (LogicManager.rank_by_experience_gap, LogicManager.rank_by_education,
                           LogicManager.rank_by_far_above_level, LogicManager.rank_by_skill_count,
                           LogicManager.rank_by_skill_share, LogicManager.rank_by_level_gap)


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
    """A made-up rule for the test: the opposite of rank_by_skill_count."""
    return fit["matched"]


def test_a_new_rule_can_be_plugged_in_without_touching_the_manager():
    jobs = [make_job(title="two", matched_skills=matched("Python", "SQL")), make_job(title="one", matched_skills=matched("Python"))]
    custom = LogicManager(rules=[fewest_skills_first])
    assert [job["title"] for job in custom.filter_and_rank(PROFILE, jobs, NO_FILTERS)] == ["one", "two"]
    assert [job["title"] for job in LOGIC.filter_and_rank(PROFILE, jobs, NO_FILTERS)] == ["two", "one"]


def test_rule_order_is_the_priority_order():
    jobs = [make_job(title="more skills, needs experience", min_years_experience=3, matched_skills=matched("Python", "SQL")),
            make_job(title="fewer skills, fits", matched_skills=matched("Python"))]
    skills_first = LogicManager(rules=[LogicManager.rank_by_skill_count])
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


@pytest.mark.parametrize("name", [
    "SQL", "Power BI", "ETL", "Financial Modelling", "Node.js", "KYC",
    "Data Visualisation", "Agile", "Machine Learning",
    # No field is privileged here: a lab or engineering resume's tools pass the same check as a data one's,
    # because the rule names no tools and no industry
    "HPLC", "PCR", "Mass Spectrometry", "Cell Culture", "GMP", "ISO 13485", "AutoCAD", "Solidworks",
])
def test_a_named_tool_or_technique_is_a_learnable_skill(name):
    assert LogicManager.is_learnable_skill(name)


@pytest.mark.parametrize("name", [
    "Reporting", "Presentation", "Presentation Skills", "Key Performance Indicators", "KPIs",
    "Ad-Hoc Analysis", "Ad Hoc Analysis", "Requirements Gathering", "Project Timeline",
    "Stakeholder Management", "Written Communication", "Process Improvement",
])
def test_an_activity_with_nothing_to_learn_is_not_a_learnable_skill(name):
    """These read like skills, unlike "Team Player", but name no tool, standard or technique, so there is
    no course to take and nothing to put in a roadmap."""
    assert not LogicManager.is_learnable_skill(name)


@pytest.mark.parametrize("name", [
    "Work in A Fast Paced Environment", "Able to Work Independently", "Willing to Learn",
    "Team Player", "Communication", "Communication Skills", "Attention to Detail",
    "Time Management", "Problem Solving", "Detail-Oriented", "Positive Attitude",
    "A proven track record of delivering results under pressure every single day",  # too long to be a skill
    "", "   ",
])
def test_a_way_of_behaving_at_work_is_not_a_learnable_skill(name):
    assert not LogicManager.is_learnable_skill(name)


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
    paragraphs = [{"id": 0, "kind": "bullet", "text": "Built Power BI reports", "editable": True}]
    edits = [{"paragraph": 0, "text": "Built PowerBI reports to go to market faster", "reason": "clearer"}]
    kept, _, removed, _ = LogicManager().ground_docx_edits(edits, paragraphs, "Built Power BI reports", ["Tableau"])
    assert kept == {0: "Built PowerBI reports to go to market faster"} and removed == []
    assert not LogicManager().job_keywords({"required_skills": ["Java"]}, "JavaScript developer")[0]
