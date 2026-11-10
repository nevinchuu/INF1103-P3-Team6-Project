"""Tests for IOManager (src/io_layer/io_manager.py): what the user types is checked and asked again, the web form's values
get the same checks, and messages go where the manager is told to write them.

No keyboard is needed: IOManager takes its input and output as arguments, so a script answers and a list collects.
"""

import os

import pytest

from io_layer import io_manager as constants
from io_layer.io_manager import IOManager

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = "sample_resume_fintech.docx"


def make_io(answers: list[str] | None = None, resume_folder: str | None = None) -> tuple[IOManager, list[str]]:
    """An IOManager that reads typed answers from the list (EOFError when they run out) and collects its output."""
    remaining = iter(answers or [])
    printed: list[str] = []

    def read(prompt: str = "") -> str:
        answer = next(remaining, None)
        if answer is None:
            raise EOFError
        return answer

    return IOManager(resume_folder or os.path.join(PROJECT_ROOT, "resumes"), read=read, write=printed.append), printed


def test_numbers_that_are_bad_are_rejected_and_asked_again():
    io, printed = make_io(["abc", "-3", "5.5", "70", "7"])
    assert io.prompt_number("Pick", 1, 10) == 7
    assert sum("[!]" in line for line in printed) == 4


def test_a_number_takes_the_default_on_enter_and_reads_money_formats():
    assert make_io([""])[0].prompt_number("Salary", 0, 9000, default=0) == 0
    assert make_io(["$5,000"])[0].prompt_number("Salary", 0, 9000) == 5000


def test_yes_no_asks_again_until_it_is_understood():
    assert make_io(["maybe", "", "Y"])[0].prompt_yes_no("Sure?") is True
    assert make_io(["no"])[0].prompt_yes_no("Sure?") is False


def test_text_cannot_be_blank_unless_allowed():
    io, printed = make_io(["", "   ", "Ann"])
    assert io.prompt_text("Name") == "Ann"
    assert sum("This cannot be left blank." in line for line in printed) == 2
    assert make_io([""])[0].prompt_text("Filter", allow_blank=True) == ""


def test_a_choice_can_be_picked_by_number_only_from_the_list():
    io, printed = make_io(["9", "0", "x", "2"])
    assert io.prompt_choice("Menu", ["a", "b", "c"]) == "b"
    assert sum("[!]" in line for line in printed) == 3


def test_closed_keyboard_raises_eof_for_the_app_to_handle():
    with pytest.raises(EOFError):
        make_io([])[0].prompt_text("Name")


def test_filters_recheck_the_salary_range():
    io, printed = make_io(["9000", "100", "3000", "", "", "2", "4"])  # min, max (too low), min, max, years, Full Time, Remote
    filters = io.prompt_filters()
    assert any("Maximum salary cannot be lower than minimum salary" in line for line in printed)
    assert filters == {"min_salary": 3000, "max_salary": constants.MAX_SALARY,
                       "max_years_experience": constants.MAX_EXPERIENCE_YEARS,
                       "job_type": "Full Time", "work_arrangement": "Remote"}


def test_form_filters_get_a_message_instead_of_a_new_question():
    io, _ = make_io()
    filters, problem = io.check_filters({"min_salary": "$3,000", "job_type": "Contract", "work_arrangement": "Any"})
    assert problem == "" and filters["min_salary"] == 3000 and filters["max_salary"] == constants.MAX_SALARY
    assert io.check_filters({"min_salary": "lots"})[1].startswith("Minimum salary must be a whole number")
    assert "of 0 or more" in io.check_filters({"min_salary": "-5"})[1]
    assert io.check_filters({"max_years_experience": "99"})[1] == "Maximum years of experience must be between 0 and 60 years."
    assert "whole number" in io.check_filters({"max_salary": "+3_000"})[1]
    assert "cannot be lower" in io.check_filters({"min_salary": "5000", "max_salary": "100"})[1]
    assert "from the lists" in io.check_filters({"job_type": "Astronaut"})[1]
    assert "from the lists" in io.check_filters({"work_arrangement": "Underwater"})[1]


def missing_profile() -> dict:
    return {"highest_qualification": "None", "qualification_detail": "", "core_skills": [],
            "search_keywords": ["Data Analyst"]}


def test_missing_details_come_from_the_form_fields():
    io, _ = make_io()
    filled = io.fill_missing_data(missing_profile(), "Diploma", "SQL, Python, ,")
    assert filled["highest_qualification"] == "Diploma" and filled["qualification_detail"] == "Diploma"
    assert filled["core_skills"] == ["SQL", "Python"]


def test_form_fields_never_overwrite_what_the_ai_found_or_accept_unknown_levels():
    io, _ = make_io()
    found = {"highest_qualification": "Master's", "qualification_detail": "MSc", "core_skills": ["Go"]}
    assert io.fill_missing_data(dict(found), "Diploma", "SQL") == found
    assert io.fill_missing_data(missing_profile(), "Wizard", "")["highest_qualification"] == "None"


def test_missing_details_are_asked_at_the_console():
    io, _ = make_io(["5", "", "SQL, Python"])  # level 5 = Diploma; a blank skills answer is asked again
    filled = io.prompt_missing_data(missing_profile())
    assert filled["highest_qualification"] == "Diploma" and filled["core_skills"] == ["SQL", "Python"]


def test_nothing_is_asked_when_the_profile_is_complete():
    io, printed = make_io()
    complete = {"highest_qualification": "Diploma", "core_skills": ["SQL"]}
    assert io.prompt_missing_data(dict(complete)) == complete and printed == []


def test_collect_input_reads_a_resume_and_the_filters():
    folder = os.path.join(PROJECT_ROOT, "resumes")
    number = str(IOManager(folder).list_resumes().index(SAMPLE) + 1)
    io, _ = make_io([number, "", "", "", "1", "1"], folder)
    record = io.collect_input("Fake AI")
    assert record["resume_path"].endswith(SAMPLE) and len(record["resume_text"]) >= 100
    assert record["filters"]["job_type"] == constants.ANY


def test_display_helpers_write_to_the_given_output():
    io, printed = make_io()
    io.display_message("hello")
    io.display_error("oops")
    io.display_header("TITLE")
    io.display_list([])
    assert "hello" in printed and any("oops" in line and "[!]" in line for line in printed)
    assert any("TITLE" in line for line in printed)


def test_display_record_shows_a_job_even_with_missing_fields():
    io, printed = make_io()
    io.display_record({"title": "Data Analyst", "company": "Acme"})
    assert any("Data Analyst" in line for line in printed)


# A search that partly failed says what it left out; an empty portal reply isn't called a connection problem
def test_partial_and_empty_search_messages():
    io = IOManager("resumes")
    assert io.partial_results_message({"listings_found": 45, "searches_errored": 0, "searches": 3}, 45) == ""
    text = io.partial_results_message({"listings_found": 45, "searches_errored": 1, "searches": 3}, 20)
    assert "25 of 45 listings could not be analysed" in text and "1 of 3 job title searches" in text
    assert "internet" in io.search_problem_message(0, 0, portal_failed=True)
    empty = io.search_problem_message(0, 0, portal_failed=False)
    assert "internet" not in empty and "loosening your filters" in empty
