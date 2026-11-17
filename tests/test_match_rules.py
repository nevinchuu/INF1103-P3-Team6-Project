"""Tests for the match rules (src/logic_layer/match_rule.py and its three rules) and how LogicManager uses them.

No AI, network or files: the jobs are hard-coded sample AI records.
"""

import pytest

from logic_layer.education_rule import EducationRule
from logic_layer.experience_rule import ExperienceRule
from logic_layer.logic_manager import FAR_ABOVE_LEVELS, LogicManager
from logic_layer.match_rule import MatchRule, OUTCOME_FAIR, OUTCOME_STRETCH, OUTCOME_STRONG
from logic_layer.seniority_rule import SeniorityRule

GOOD_URL = "https://www.mycareersfuture.gov.sg/job/it/data-analyst-acme-5365191b6ddd018b70f7b8149c0a20ca"
PROFILE = {"highest_qualification": "Diploma", "years_of_experience": 1, "seniority_level": "Fresh/entry level"}


def make_job(**changes) -> dict:
    job = {"title": "Data Analyst", "job_url": GOOD_URL, "min_years_experience": 0, "min_education": "Not specified",
           "position_levels": ["Executive"], "required_skills": ["SQL", "Python"],
           "matched_skills": [{"job_skill": "SQL", "candidate_skill": "SQL"},
                              {"job_skill": "Python", "candidate_skill": "Python"}]}
    job.update(changes)
    return job


def fit(**changes) -> dict:
    return LogicManager.job_fit(PROFILE, make_job(**changes))


def test_the_interface_cannot_be_used_on_its_own():
    with pytest.raises(TypeError):
        MatchRule()


@pytest.mark.parametrize("years, wording", [(3, "needs 2 more years of experience"),
                                            (2, "needs 1 more year of experience"), (1, ""), (0, "")])
def test_the_experience_rule_counts_the_missing_years(years, wording):
    assert ExperienceRule().shortfall(fit(min_years_experience=years), make_job()) == wording


def test_the_education_rule_names_the_level_the_job_asks_for():
    job = make_job(min_education="Bachelor's")
    assert EducationRule().shortfall(LogicManager.job_fit(PROFILE, job), job) == "asks for Bachelor's or higher"
    assert EducationRule().shortfall(fit(min_education="Diploma"), make_job()) == ""
    assert EducationRule().shortfall(fit(min_education="Not specified"), make_job()) == ""


def test_the_seniority_rule_uses_the_distance_it_is_given():
    far_job = fit(position_levels=["Manager"])  # 6 levels above Fresh/entry level
    assert SeniorityRule(FAR_ABOVE_LEVELS).shortfall(far_job, make_job()) == "is well above your current level"
    assert SeniorityRule(FAR_ABOVE_LEVELS).shortfall(fit(position_levels=["Executive"]), make_job()) == ""
    assert SeniorityRule(2).shortfall(fit(position_levels=["Junior Executive"]), make_job()) == \
        "is well above your current level"


class RemoteOnlyRule(MatchRule):
    """A rule LogicManager has never seen: the candidate can't work onsite."""

    def shortfall(self, fit: dict, job: dict) -> str:
        return "is onsite only" if job.get("work_arrangement") == "Onsite" else ""


def test_the_standard_rules_are_the_three_from_phase_1_and_every_one_is_a_match_rule():
    # The standard rules are the ones that name their shortfall, in this order
    job = make_job(min_years_experience=3, min_education="Bachelor's", position_levels=["Manager"])
    reason = LogicManager().match_outcome(PROFILE, job)[1]
    assert reason.index("experience") < reason.index("Bachelor's") < reason.index("well above your current level")
    assert all(issubclass(rule, MatchRule) for rule in (ExperienceRule, EducationRule, SeniorityRule))


@pytest.mark.parametrize("rule", [ExperienceRule(), EducationRule(), SeniorityRule(FAR_ABOVE_LEVELS),
                                  RemoteOnlyRule()])
def test_every_match_rule_works_where_a_match_rule_is_expected(rule):
    """LSP: whatever rule it is, LogicManager asks it the same way and gets one of the three outcomes and a reason."""
    outcome, reason = LogicManager(match_rules=[rule]).match_outcome(PROFILE, make_job())
    assert isinstance(rule, MatchRule)
    assert outcome in (OUTCOME_STRONG, OUTCOME_FAIR, OUTCOME_STRETCH) and reason


def test_the_outcome_lists_every_shortfall_in_rule_order():
    job = make_job(min_years_experience=3, min_education="Bachelor's", position_levels=["Manager"])
    outcome, reason = LogicManager().match_outcome(PROFILE, job)
    assert outcome == "Stretch"
    assert reason == ("This job needs 2 more years of experience and asks for Bachelor's or higher and "
                      "is well above your current level.")


def test_a_new_rule_changes_the_outcome_without_changing_logic_manager():
    """OCP: a rule is added by handing it in, not by editing match_outcome."""
    job = make_job(work_arrangement="Onsite")
    assert LogicManager().match_outcome(PROFILE, job)[0] == "Strong match"
    logic = LogicManager(match_rules=[ExperienceRule(), EducationRule(), SeniorityRule(FAR_ABOVE_LEVELS), RemoteOnlyRule()])
    assert logic.match_outcome(PROFILE, job) == ("Stretch", "This job is onsite only.")


def test_with_no_match_rules_only_the_skill_share_decides():
    job = make_job(min_years_experience=10)
    assert LogicManager().match_outcome(PROFILE, job)[0] == "Stretch"
    assert LogicManager(match_rules=[]).match_outcome(PROFILE, job)[0] == "Strong match"
