"""Tests for InputValidator (src/io_layer/input_validator.py): every check says what is wrong in words, or "" if the input is fine.

No AI, network or files are involved.
"""

import pytest

from data_layer import data_manager
from io_layer import input_validator
from io_layer.input_validator import InputValidator

VALIDATOR = InputValidator()
GOOD_URL = "https://www.mycareersfuture.gov.sg/job/it/data-analyst-acme-5365191b6ddd018b70f7b8149c0a20ca"
LONG_TEXT = "Experienced analyst. " * 20


# ---------- the search form ----------

def test_form_filters_get_a_message_instead_of_a_new_question():
    filters, problem = VALIDATOR.check_filters({"min_salary": "$3,000", "job_type": "Contract",
                                                "work_arrangement": "Any"})
    assert problem == "" and filters["min_salary"] == 3000 and filters["max_salary"] == input_validator.MAX_SALARY
    assert VALIDATOR.check_filters({"min_salary": "lots"})[1].startswith("Minimum salary must be a whole number")
    assert "of 0 or more" in VALIDATOR.check_filters({"min_salary": "-5"})[1]
    assert (VALIDATOR.check_filters({"max_years_experience": "99"})[1]
            == "Experience asked must be between 0 and 60 years.")
    assert "whole number" in VALIDATOR.check_filters({"max_salary": "+3_000"})[1]
    assert "cannot be lower" in VALIDATOR.check_filters({"min_salary": "5000", "max_salary": "100"})[1]
    assert "from the lists" in VALIDATOR.check_filters({"job_type": "Astronaut"})[1]
    assert "from the lists" in VALIDATOR.check_filters({"work_arrangement": "Underwater"})[1]


def test_a_problem_returns_no_filters():
    assert VALIDATOR.check_filters({"min_salary": "lots"})[0] == {}


def test_an_empty_form_means_no_limits():
    values, problem = VALIDATOR.check_filters({})
    assert problem == ""
    assert values == {"min_salary": 0, "max_salary": 50000, "max_years_experience": 60, "job_type": "Any",
                      "work_arrangement": "Any", "show": 5, "fallback_qualification": "", "fallback_skills": []}


@pytest.mark.parametrize("typed, expected", [("10", 10), ("20", 20), ("5", 5), ("7", 5), ("lots", 5), ("", 5)])
def test_the_show_choice_must_be_one_that_is_offered(typed, expected):
    assert VALIDATOR.check_filters({"show": typed})[0]["show"] == expected


def test_the_details_the_resume_lacked_come_from_the_form_fields():
    values, _ = VALIDATOR.check_filters({"fallback_qualification": "Diploma", "fallback_skills": "SQL, Python, ,"})
    assert values["fallback_qualification"] == "Diploma" and values["fallback_skills"] == ["SQL", "Python"]
    values, _ = VALIDATOR.check_filters({"fallback_qualification": "Wizard"})
    assert values["fallback_qualification"] == ""
    values, _ = VALIDATOR.check_filters({"fallback_qualification": "None"})  # "None" is not an answer to give
    assert values["fallback_qualification"] == ""


# ---------- resumes and uploads ----------

def test_a_resume_needs_a_known_type_and_enough_text():
    assert VALIDATOR.check_resume("cv.pdf", LONG_TEXT) == ""
    assert VALIDATOR.check_resume("CV.DOCX", LONG_TEXT) == ""
    assert VALIDATOR.check_resume("cv.txt", LONG_TEXT) == "'.txt' files are not supported. Please use a PDF or Word (.docx) file."
    assert VALIDATOR.check_resume("cv.pdf", "too short").startswith("Could not read enough text from that file.")


def test_an_upload_must_be_a_known_type_not_empty_and_not_too_large():
    assert VALIDATOR.check_upload("cv.pdf", 1000) == ""
    assert VALIDATOR.check_upload("cv.exe", 1000) == "Please upload a PDF or Word (.docx) file."
    assert VALIDATOR.check_upload("cv.pdf", 0) == "That file is empty."
    assert VALIDATOR.check_upload("cv.pdf", input_validator.MAX_UPLOAD_BYTES) == ""
    assert (VALIDATOR.check_upload("cv.pdf", input_validator.MAX_UPLOAD_BYTES + 1)
            == "That file is too large. Please upload a resume under 5 MB.")


# ---------- saved jobs ----------

def test_a_saved_job_update_needs_a_known_status_and_a_short_note():
    assert VALIDATOR.check_saved_update("Applied", "sent my CV") == ""
    assert VALIDATOR.check_saved_update(None, None) == ""
    assert VALIDATOR.check_saved_update("", "") == ""
    assert VALIDATOR.check_saved_update("Hired", None) == "Status must be one of Saved, Applied, Interviewing, Offer, Rejected"
    assert VALIDATOR.check_saved_update("Saved", "x" * 501) == "A note can be at most 500 characters"
    assert VALIDATOR.check_saved_update("Saved", "x" * 500 + "   ") == ""  # spaces at the ends don't count


def test_the_status_list_and_note_limit_match_the_data_layer():
    assert input_validator.STATUSES == data_manager.STATUSES
    assert input_validator.MAX_NOTE_CHARS == data_manager.MAX_NOTE_CHARS


# ---------- the what-if form ----------

JOB_SKILLS = ["Python", "Tableau", "Power BI"]


def what_if(**form):
    return VALIDATOR.check_what_if({"mode": "test", "skills": [], "extra_skills": "", "steps": "3", "top_n": "5",
                                    **form}, JOB_SKILLS)


@pytest.mark.parametrize("form, message", [
    ({}, "Pick at least one skill"),
    ({"skills": ["", "   "]}, "Pick at least one skill"),
    ({"skills": ["Python", "python"]}, '"python" is listed more than once.'),
    ({"skills": ["Python"], "extra_skills": " python "}, '"python" is listed more than once.'),
    ({"skills": ["Python"] + [f"S{n}" for n in range(input_validator.MAX_SKILLS)]}, "or fewer"),
    ({"skills": ["x" * 10_000]}, "characters or fewer"),
])
def test_bad_skill_lists_are_rejected_with_a_message(form, message):
    values, problem = what_if(**form)
    assert values == {} and message in problem


def test_a_very_long_skill_name_is_rejected_briefly():
    assert len(what_if(skills=["x" * 10_000])[1]) < 100


def test_a_chosen_skill_is_given_back_the_way_the_jobs_write_it():
    values, problem = what_if(skills=["  python "], extra_skills="tableau, POWER   bi")
    assert problem == "" and values["skills"] == ["Python", "Tableau", "Power BI"]
    assert what_if(skills=["Cobol"])[0]["skills"] == ["Cobol"]  # the simulator says no job asks for it


def test_typed_skills_are_split_on_commas_and_non_text_entries_are_dropped():
    assert what_if(extra_skills=" Python, ,SQL ,, Tableau")[0]["skills"] == ["Python", "SQL", "Tableau"]
    assert what_if(skills=[None, 5, "Python"])[0]["skills"] == ["Python"]  # type: ignore[list-item]
    assert "Pick at least one skill" in what_if(skills=[None, 5])[1]  # type: ignore[list-item]


@pytest.mark.parametrize("top_n", ["0", "-1", 0, -3])
def test_the_top_list_size_must_be_a_positive_whole_number(top_n):
    assert what_if(skills=["Python"], top_n=top_n)[1] == "The list must show at least 1 job."


@pytest.mark.parametrize("top_n", ["lots", "", "2.5", True, None])
def test_a_top_list_size_that_is_not_a_whole_number_is_rejected(top_n):
    assert what_if(skills=["Python"], top_n=top_n)[1] == "Use a whole number for the list size."


def test_a_missing_top_list_size_or_roadmap_length_means_the_default():
    values, problem = VALIDATOR.check_what_if({"mode": "greedy"}, JOB_SKILLS)
    assert problem == "" and (values["steps"], values["top_n"]) == (3, 5)


@pytest.mark.parametrize("steps", ["0", "-2", "11", 0, 11])
def test_roadmap_length_must_be_within_limits(steps):
    assert what_if(mode="greedy", steps=steps)[1] == "A plan can have 1 to 10 skills."


@pytest.mark.parametrize("steps", ["many", "", "2.5", True])
def test_a_roadmap_length_that_is_not_a_whole_number_is_rejected(steps):
    assert what_if(mode="greedy", steps=steps)[1] == "Use whole numbers for the number of skills and the list size."


def test_testing_skills_ignores_the_roadmap_length():
    values, problem = what_if(skills=["Python"], steps="many")
    assert problem == "" and values == {"mode": "test", "skills": ["Python"], "steps": 3, "top_n": 5}


def test_a_roadmap_with_no_skills_lets_the_simulator_choose_and_may_take_many():
    assert what_if(mode="greedy")[0] == {"mode": "greedy", "skills": None, "steps": 3, "top_n": 5}
    many = [f"S{n}" for n in range(input_validator.MAX_SKILLS + 3)]
    assert what_if(mode="salary", skills=many)[0]["skills"] == many
    assert "listed more than once" in what_if(mode="greedy", skills=["Python", "python"])[1]
