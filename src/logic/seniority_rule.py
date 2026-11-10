"""SeniorityRule: a job pitched well above the candidate's level."""

from logic.match_rule import MatchRule


class SeniorityRule(MatchRule):
    """Falls short when the job's lowest listed level is far_above_levels or more above the candidate's."""

    def __init__(self, far_above_levels: int) -> None:
        self.__far_above_levels = far_above_levels

    @property
    def far_above_levels(self) -> int:
        """How many levels above the candidate's counts as too far."""
        return self.__far_above_levels

    def shortfall(self, fit: dict, job: dict) -> str:
        return "is well above your current level" if fit["level_gap"] >= self.__far_above_levels else ""
