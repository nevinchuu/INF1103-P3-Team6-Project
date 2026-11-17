"""ExperienceRule: a job that needs more years of experience than the candidate has."""

from logic_layer.match_rule import MatchRule


class ExperienceRule(MatchRule):
    """Falls short when the job's minimum years of experience is more than the candidate's."""

    def shortfall(self, fit: dict, job: dict) -> str:
        years = fit["experience_gap"]
        # :g writes a whole number without ".0" (1.0 -> "1") and keeps a real fraction (0.5 -> "0.5")
        return f"needs {years:g} more year{'s' if years != 1 else ''} of experience" if years else ""
