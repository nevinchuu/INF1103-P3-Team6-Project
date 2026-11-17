"""Tests for SkillName (src/logic_layer/skill_name.py): when two skill names are the same skill.

Jobs are read one posting at a time, so one skill turns up as "UAT", "UAT Testing" and "User Acceptance Testing".
The What-If page offered them as three skills to learn, and a resume's "UAT" didn't cover a job's full name."""

import pytest

from logic_layer.skill_name import SkillName

NAMES = SkillName(["User Acceptance Testing", "UAT", "UAT Testing", "Know Your Customer", "Anti-Money Laundering",
                   "Microsoft Excel", "Go", "Google Operations", "Quality of Service"])


@pytest.mark.parametrize("first, second", [
    ("UAT", "User Acceptance Testing"),
    ("UAT Testing", "User Acceptance Testing"),
    (" uat testing ", "UAT"),  # case and spaces never matter, as before
    ("KYC", "Know Your Customer"),
    ("AML", "Anti-Money Laundering"),  # a hyphen splits words
    ("QS", "Quality of Service"),  # small linking words are skipped
    ("sql", "SQL"),
])
def test_an_abbreviation_is_the_same_skill_as_the_name_it_spells(first, second):
    assert NAMES.same(first, second)


@pytest.mark.parametrize("first, second", [
    ("Go", "Google Operations"),  # "Go" is a word, not an abbreviation written in capitals
    ("UAT", "Microsoft Excel"),
    ("UAT Excel", "User Acceptance Testing"),  # only the full name's own last word may follow
])
def test_different_skills_stay_different(first, second):
    assert not NAMES.same(first, second)


def test_initials_two_names_share_stand_for_neither():
    names = SkillName(["Continuous Integration", "Competitive Intelligence", "CI"])
    assert not names.same("CI", "Continuous Integration") and not names.same("CI", "Competitive Intelligence")


def test_an_abbreviation_with_no_full_name_in_the_set_is_just_itself():
    names = SkillName(["SQL", "Python"])
    assert names.key("SQL") == "sql" and not names.same("SQL", "Python")
