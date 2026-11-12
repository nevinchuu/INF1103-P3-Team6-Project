"""RoadmapPlanner: the interface every roadmap strategy implements (GreedyPlanner, SalaryWeightedPlanner)."""

from abc import ABC, abstractmethod


class RoadmapPlanner(ABC):
    """Interface: a roadmap strategy must be able to say how good a what-if result is. It shares no code: how
    a planner scores is entirely up to it, and CareerSimulator.roadmap tries each next skill and keeps the one
    the planner scores highest."""

    @property
    @abstractmethod
    def key(self) -> str:
        """Short name used in forms and menus, e.g. "greedy"."""

    @property
    @abstractmethod
    def label(self) -> str:
        """Name shown to the user, e.g. "Biggest rank gain first"."""

    @abstractmethod
    def value(self, report: dict) -> tuple:
        """How good a what-if report is, as a tuple compared left to right: a later number only breaks ties in
        the earlier ones. A bigger tuple is a better result."""
