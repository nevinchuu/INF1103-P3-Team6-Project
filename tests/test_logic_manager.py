"""Tests for the logic layer (src/logic_manager.py): filtering and ranking job records.

Plain functions only (the project is fully procedural). Run from the project root:

    python tests/test_logic_manager.py

Each test_* function checks one rule with assert; the runner at the bottom calls them all.
They also run under pytest, if it is installed (python -m pytest tests).
"""

import os
import sys

# The project's modules live in src/, so add it to the import path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import io_manager  # noqa: E402  (all output goes through the input/output layer)
import logic_manager  # noqa: E402

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
    assert logic_manager.is_valid_job_url(GOOD_URL)
    assert not logic_manager.is_valid_job_url(GOOD_URL.replace("https", "http"))
    assert not logic_manager.is_valid_job_url("https://mycareersfuture.gov.sg.evil.com/job/1")
    assert not logic_manager.is_valid_job_url("N/A")
    assert not logic_manager.is_valid_job_url(None)
    assert logic_manager.filter_reason(make_job(job_url="N/A"), NO_FILTERS) == "bad link"


# 2. Salary: a job is removed only if its whole pay range is outside the user's range
def test_salary_filter_keeps_overlapping_and_unstated_pay():
    filters = {**NO_FILTERS, "min_salary": 5000, "max_salary": 8000}
    assert logic_manager.filter_reason(make_job(min_salary=4000, max_salary=6000), filters) == ""
    assert logic_manager.filter_reason(make_job(min_salary=3000, max_salary=4500), filters) == "salary"
    assert logic_manager.filter_reason(make_job(min_salary=9000, max_salary=12000), filters) == "salary"
    # 0 means the job doesn't state a salary, so it is kept
    assert logic_manager.filter_reason(make_job(min_salary=0, max_salary=0), filters) == ""


# 3. Job type and work arrangement, including the special cases
def test_job_type_and_work_arrangement_filters():
    full_time = {**NO_FILTERS, "job_type": "Full Time"}
    # "Permanent" jobs on MyCareersFuture are full-time
    assert logic_manager.filter_reason(make_job(employment_types=["Permanent"]), full_time) == ""
    assert logic_manager.filter_reason(make_job(employment_types=["Contract"]), full_time) == "job type"

    remote = {**NO_FILTERS, "work_arrangement": "Remote"}
    assert logic_manager.filter_reason(make_job(work_arrangement="Remote"), remote) == ""
    assert logic_manager.filter_reason(make_job(work_arrangement="Onsite"), remote) == "work arrangement"
    # A job that doesn't say is kept rather than wrongly removed
    assert logic_manager.filter_reason(make_job(work_arrangement="Not specified"), remote) == ""


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
    assert logic_manager.count_removed(jobs, filters) == {"bad link": 1, "salary": 2, "experience": 1}
    assert logic_manager.count_removed([make_job()], filters) == {}


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

    ranked = logic_manager.filter_and_rank(PROFILE, jobs, filters, top_n=4)
    assert [job["title"] for job in ranked] == ["Best match", "Half match", "Needs a degree", "Too senior"]

    # top_n limits how many are returned
    top_two = logic_manager.filter_and_rank(PROFILE, jobs, filters, top_n=2)
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
    ranked = logic_manager.filter_and_rank(profile, jobs, NO_FILTERS, top_n=4)
    # Junior Executive is 2 levels up: near enough, so its extra skill puts it first.
    # Senior Executive and Manager are 4+ levels up, so they come after both nearer jobs
    assert [job["title"] for job in ranked] == ["Better match, junior", "Entry role", "Better match, senior", "Senior role"]
    # Unknown or missing levels count as no gap
    assert logic_manager.seniority_gap(make_job(position_levels=["Unknown"]), "Executive") == 0
    assert logic_manager.seniority_gap(make_job(), "Executive") == 0


# 7. More matched skills ranks first, even when a job with a short skill list has a higher share
def test_more_matched_skills_beats_a_higher_share():
    jobs = [
        make_job(title="2 of 3", required_skills=["Python", "SQL", "Excel"], matched_skills=matched("Python", "SQL")),
        make_job(title="3 of 6", required_skills=["Python", "SQL", "Excel", "Tableau", "R", "Git"],
                 matched_skills=matched("Python", "SQL", "Excel")),
    ]
    assert [job["title"] for job in logic_manager.filter_and_rank(PROFILE, jobs, NO_FILTERS, top_n=2)] == ["3 of 6", "2 of 3"]


# 8. Jobs that match none of the candidate's skills are not shown
def test_jobs_with_no_matched_skills_are_hidden():
    jobs = [make_job(title="Match", matched_skills=matched("Python")), make_job(title="No match")]
    assert [job["title"] for job in logic_manager.filter_and_rank(PROFILE, jobs, NO_FILTERS, top_n=5)] == ["Match"]


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
    assert logic_manager.skills_shown(card, evidence, parts) == {"SQL": "Wrote SQL queries",
                                                                  "Spreadsheets": "Microsoft Excel"}


# 10. Match outcome: a job the candidate misses a requirement for is a stretch, whatever the skills;
#     otherwise STRONG_MATCH_SHARE of the key skills (3 of 4 here) makes a strong match
def test_match_outcome_combines_requirements_and_skills():
    profile = {**PROFILE, "seniority_level": "Fresh/entry level"}
    strong = make_job(matched_skills=matched("Python", "SQL", "Excel"))
    fair = make_job(matched_skills=matched("Python"))
    assert logic_manager.match_outcome(profile, strong)[0] == logic_manager.OUTCOME_STRONG
    assert logic_manager.match_outcome(profile, fair)[0] == logic_manager.OUTCOME_FAIR

    every_skill = matched("Python", "SQL", "Excel", "Tableau")
    short_on_experience = logic_manager.match_outcome(profile, make_job(min_years_experience=3, matched_skills=every_skill))
    assert short_on_experience == (logic_manager.OUTCOME_STRETCH, "This job needs 2 more years of experience.")
    needs_degree = make_job(min_education="Bachelor's", matched_skills=every_skill)
    assert logic_manager.match_outcome(profile, needs_degree)[0] == logic_manager.OUTCOME_STRETCH
    far_above = make_job(position_levels=["Manager"], matched_skills=every_skill)
    assert logic_manager.match_outcome(profile, far_above)[0] == logic_manager.OUTCOME_STRETCH
    # Several gaps are all named
    both = make_job(min_years_experience=2, min_education="Bachelor's", matched_skills=every_skill)
    assert logic_manager.match_outcome(profile, both)[1] == ("This job needs 1 more year of experience "
                                                             "and asks for Bachelor's or higher.")

    # filter_and_rank gives every job it returns an outcome and a reason
    ranked = logic_manager.filter_and_rank(profile, [strong, fair], NO_FILTERS, top_n=2)
    assert [job["match_outcome"] for job in ranked] == [logic_manager.OUTCOME_STRONG, logic_manager.OUTCOME_FAIR]
    assert all(job["outcome_reason"] for job in ranked)


# 11. Search steps: every preference first, then the job type only (unless it is Any), then none
def test_search_steps_loosen_preferences_in_order():
    assert logic_manager.search_steps({**NO_FILTERS, "job_type": "Full Time"}) == ["all", "job type", "general"]
    assert logic_manager.search_steps(NO_FILTERS) == ["all", "general"]
    assert logic_manager.search_steps(None) == ["all", "general"]


# 12. Job keywords for tailoring: a skill the resume shows (word for word, or matched under another
#     name) may be used; one it doesn't show may not. Each skill is listed once
def test_job_keywords_split_shown_and_missing_skills():
    job = make_job(required_skills=["SQL", "Business Data Analysis", "Scala", "Java"],
                   listed_skills=["SQL", "Tableau"],
                   matched_skills=[{"job_skill": "Business Data Analysis", "candidate_skill": "Data Analysis"}])
    use, avoid = logic_manager.job_keywords(job, "Wrote SQL queries in JavaScript and Data Analysis")
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
    kept, notes = logic_manager.keep_bullet_groups(edits, paragraphs)
    assert kept == {4: "", 5: "Took card and cash payments"}
    assert notes == [{"kind": "bullet_group_kept", "heading": "Data Analyst Intern"}]
    assert edits == {1: "", 2: "", 4: "", 5: "Took card and cash payments"}  # the caller's edits are not changed


# 14. Tailoring keeps the number of pages: a rewrite needing extra lines is kept only if shorter
#     rewrites free at least as many lines
def test_fit_edits_to_page_count_keeps_the_number_of_lines():
    paragraphs = [make_paragraph(1, "x" * 100), make_paragraph(2, "y" * 40)]
    longer = {2: "z" * 120}  # 1 line becomes 3, and nothing is freed
    kept, notes = logic_manager.fit_edits_to_page_count(longer, paragraphs)
    assert kept == {} and notes == [{"kind": "edit_too_long", "start": "y" * 40}]
    # Removing the 2-line paragraph frees enough lines for the longer one
    kept, notes = logic_manager.fit_edits_to_page_count({1: "", 2: "z" * 80}, paragraphs)
    assert kept == {1: "", 2: "z" * 80} and notes == []


# 15. After a search step drops preferences, only the ones it kept still apply
def test_filters_for_step_keeps_only_the_preferences_still_applied():
    preferences = {**NO_FILTERS, "min_salary": 4000, "max_salary": 6000, "job_type": "Full Time", "work_arrangement": "Remote"}
    assert logic_manager.filters_for_step(preferences, "all") == preferences
    assert logic_manager.filters_for_step(preferences, "job type") == {**NO_FILTERS, "job_type": "Full Time"}
    assert logic_manager.filters_for_step(preferences, "general") == NO_FILTERS


# 16. Skills worth building: missing from 2 or more jobs, counted once per job, "SQL" and "sql" as one
def test_find_skill_gaps_counts_skills_missing_from_several_jobs():
    jobs = [make_job(missing_skills=["SQL", "sql ", "Excel"]), make_job(missing_skills=["SQL", "Tableau"]),
            make_job(missing_skills=["Excel"]), make_job()]
    # Equal counts keep the order the skills first appear in, so every run gives the same list
    assert logic_manager.find_skill_gaps(jobs) == [("SQL", 2), ("Excel", 2)]
    assert logic_manager.find_skill_gaps([make_job(missing_skills=["R"])]) == []


# 17. Every piece of text in a tailored resume, for checking which skills it shows
def test_resume_parts_lists_every_piece_of_text():
    resume = {"sections": [
        {"text": "Analyst aiming at data roles", "skill_groups": [{"label": "Technical", "skills": ["SQL", "Python"]}],
         "entries": []},
        {"text": "", "skill_groups": [], "entries": [
            {"title": "Intern", "organisation": "Acme", "subtitle": "Singapore", "bullets": ["Wrote SQL", "Built reports"]}]}]}
    assert logic_manager.resume_parts(resume) == ["Analyst aiming at data roles", "SQL", "Python", "",
                                                  "Intern", "Acme", "Singapore", "Wrote SQL", "Built reports"]


# 18. Ranking gives back copies with the outcome added, and leaves the caller's job records unchanged
def test_filter_and_rank_leaves_the_callers_jobs_unchanged():
    jobs = [make_job(matched_skills=matched("Python", "SQL", "Excel"))]
    ranked = logic_manager.filter_and_rank(PROFILE, jobs, NO_FILTERS, top_n=1)
    assert ranked[0]["match_outcome"] == logic_manager.OUTCOME_STRONG
    assert "match_outcome" not in jobs[0] and "outcome_reason" not in jobs[0]


# 19. An education level the logic layer doesn't know asks for nothing (no crash), and a job with
#     no key skills gets a plain reason instead of "0 of 0"
def test_match_outcome_handles_unknown_education_and_no_key_skills():
    profile = {**PROFILE, "seniority_level": "Fresh/entry level"}
    outcome = logic_manager.match_outcome(profile, make_job(min_education="Degree", matched_skills=matched("Python", "SQL", "Excel")))
    assert outcome[0] == logic_manager.OUTCOME_STRONG
    assert logic_manager.match_outcome(profile, make_job(required_skills=[])) == (logic_manager.OUTCOME_FAIR,
                                                                                  "You meet its requirements.")


# 20. Values the logic layer copies (so it never imports another layer) still match the other layers
def test_copied_values_still_match_the_other_layers():
    import ai_manager  # imported here: only this test needs the AI layer's lists
    assert logic_manager.EDUCATION_LEVELS == ai_manager.EDUCATION_LEVELS == io_manager.EDUCATION_LEVELS
    assert logic_manager.SENIORITY_LEVELS == ai_manager.SENIORITY_LEVELS
    assert logic_manager.ANY == io_manager.ANY
    assert (logic_manager.MAX_SALARY, logic_manager.MAX_EXPERIENCE_YEARS) == (io_manager.MAX_SALARY,
                                                                              io_manager.MAX_EXPERIENCE_YEARS)


def run_all_tests():
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
    io_manager.display_message(f"\n{len(tests) - failed} of {len(tests)} tests passed.")
    return failed


if __name__ == "__main__":
    sys.exit(1 if run_all_tests() else 0)  # a non-zero exit code tells CI that a test failed
