"""Tests for IOManager's wording methods: how jobs, notes and changes are worded for the console and pages."""

import pytest

from io_layer.io_manager import IOManager

text = IOManager()


def test_salary_is_shown_as_a_range_or_not_stated():
    assert text.format_salary({"min_salary": 4000, "max_salary": 6000}) == "$4,000 - $6,000 monthly"
    assert text.format_salary({"min_salary": 0, "max_salary": 0}) == "Not stated"
    assert text.format_salary({}) == "Not stated"


@pytest.mark.parametrize("raw, expected", [
    ("IT SUPPORT ENGINEER", "IT Support Engineer"),
    ("Data Analyst (SQL)", "Data Analyst (SQL)"),
    (None, "N/A"),
])
def test_titles_are_tidied(raw, expected):
    assert text.tidy_title(raw) == expected


def test_locations_experience_and_lists():
    assert text.short_location("D19 Hougang, Sengkang, Punggol") == "Hougang"
    assert text.experience_text(0) == "No experience needed" and text.experience_text(2) == "2+ years"
    assert text.join_list([{"job_skill": "SQL"}, "Python"]) == "SQL, Python"
    assert text.join_list(None) == "None"
    assert text.shorten("A very long company name", 10) == "A very ..."
    assert text.shorten("short", 10) == "short"


def test_skills_match_ignoring_case_and_spacing():
    assert text.same_skill("Python", " python ")
    assert not text.same_skill("Python", "Java")


def test_tailoring_notes_and_changes_wording():
    notes = text.describe_tailoring_notes([
        {"kind": "entry_not_in_resume", "title": "Invented Corp"},
        {"kind": "edit_adds_missing_skills", "start": "Built dashboards", "skills": ["Scala", "Java"]},
        {"kind": "bullet_group_kept", "heading": "Cashier"}])
    assert notes == ['Left out "Invented Corp": it isn\'t in your resume.',
                     'Kept "Built dashboards..." as it was: the new wording added Scala, Java, which isn\'t in your resume.',
                     'Kept every point under "Cashier": removing them all would leave it empty.']
    paragraphs = [{"id": 3, "text": "Built dashboards"}, {"id": 4, "text": "x" * 60}]
    changes = text.describe_docx_changes(paragraphs, {3: "Built SQL dashboards", 4: ""}, {3: "leads with SQL"})
    assert changes == ['Reworded "Built dashboards": leads with SQL', f'Removed "{"x" * 50}..."']


def test_search_problem_messages_cover_each_failure():
    assert text.search_problem_message(0, 0)  # the portal gave nothing
    assert text.search_problem_message(5, 0)  # listings found, none could be analysed by the AI
    assert text.search_problem_message(5, 5) == ""
    assert text.ai_unavailable_message()


def test_what_if_and_roadmap_wording():
    from planning.career_simulator import CareerSimulator
    from logic.logic_manager import LogicManager
    profile = {"years_of_experience": 1, "highest_qualification": "Diploma", "seniority_level": "Fresh/entry level",
               "core_skills": ["SQL"]}
    jobs = [{"title": "DATA ANALYST", "company": "A", "min_years_experience": 0, "min_education": "Not specified",
             "position_levels": [], "required_skills": ["SQL", "Python"],
             "matched_skills": [{"job_skill": "SQL", "candidate_skill": "SQL"}], "missing_skills": ["Python"],
             "min_salary": 0, "max_salary": 0}]
    simulator, logic = CareerSimulator(), LogicManager()
    lines = text.describe_what_if(simulator.what_if(logic, profile, jobs, ["Python"]))
    assert lines[0] == "If you learned Python:" and "Fair match -> Strong match" in lines[-1]
    assert "(covers Python)" in lines[-1] and "Data Analyst" in lines[-1]
    roadmap_lines = text.describe_roadmap(simulator.roadmap(logic, profile, jobs, "greedy", steps=2))
    assert roadmap_lines[0] == "Roadmap (Biggest rank gain first):" and "1. Python - improves your skill match" in roadmap_lines[1]
    done = [{**jobs[0], "missing_skills": [], "matched_skills": jobs[0]["matched_skills"]}]
    assert "none of these skills would improve" in text.describe_roadmap(simulator.roadmap(logic, profile, done, "greedy"))[0]
