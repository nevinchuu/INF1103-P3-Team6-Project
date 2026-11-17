"""MatchRule: the interface every match rule implements (ExperienceRule, EducationRule, SeniorityRule)."""

from abc import ABC, abstractmethod

# The outcome a job gets from LogicManager.match_outcome (here so the simulator can use them without the manager)
OUTCOME_STRONG = "Strong match"
OUTCOME_FAIR = "Fair match"
OUTCOME_STRETCH = "Stretch"


class MatchRule(ABC):
    """Interface: one requirement a job has that the candidate can fall short of. LogicManager.match_outcome asks
    every rule the same question and makes a job a Stretch if any rule finds a shortfall, so a new requirement is a
    new rule class, with no change to LogicManager (OCP)."""

    @abstractmethod
    def shortfall(self, fit: dict, job: dict) -> str:
        """How the candidate falls short of the job, worded to follow "This job " (e.g. "asks for Diploma or
        higher"), or "" if they don't. fit is LogicManager.job_fit(profile, job)."""
