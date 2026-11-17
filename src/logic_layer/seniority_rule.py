"""SeniorityRule: a job pitched well above the candidate's level."""

from logic_layer.match_rule import MatchRule


class SeniorityRule(MatchRule):
    """Falls short when the job's lowest listed level is far_above or more levels above the candidate's."""

    def __init__(self, far_above: int) -> None:
        self.__far_above = far_above

    def shortfall(self, fit: dict, job: dict) -> str:
        return "is well above your current level" if fit["level_gap"] >= self.__far_above else ""
