"""EducationRule: a job that asks for a higher qualification than the candidate's."""

from logic.match_rule import MatchRule


class EducationRule(MatchRule):
    """Falls short when the candidate's highest qualification is below the job's minimum education."""

    def shortfall(self, fit: dict, job: dict) -> str:
        return "" if fit["education_ok"] else f"asks for {job['min_education']} or higher"
