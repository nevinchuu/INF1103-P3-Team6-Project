"""RankingRules: the interface for the two questions the What-If simulator asks about a job (LogicManager answers them)."""

from abc import ABC, abstractmethod


class RankingRules(ABC):
    """Interface: how good a match a job is for a candidate. CareerSimulator re-ranks the jobs through it, so it
    depends on these two questions only, not on LogicManager: the simulator never names the manager that owns it,
    and there is no loop between the two classes (Dependency Inversion). LogicManager implements it with the same
    rules its search uses, so a what-if is judged exactly as the search was."""

    @abstractmethod
    def match_outcome(self, profile: dict, job: dict) -> tuple[str, str]:
        """(outcome, reason): the outcome is "Strong match", "Fair match" or "Stretch" (match_rule.py)."""

    @abstractmethod
    def sort_key(self, profile: dict, job: dict) -> tuple:
        """The job's place in the ranking as a sort key: a smaller key is a better match."""
