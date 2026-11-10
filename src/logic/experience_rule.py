"""ExperienceRule: a job that needs more years of experience than the candidate has."""

from logic.match_rule import MatchRule


class ExperienceRule(MatchRule):
    """Falls short when the job's minimum years of experience is more than the candidate's."""

    def shortfall(self, fit: dict, job: dict) -> str:
        years = fit["experience_gap"]
        return f"needs {years} more year{'s' if years != 1 else ''} of experience" if years else ""
