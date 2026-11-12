"""SalaryWeightedPlanner: each roadmap step favours the skill that lifts the best-paid jobs."""

from planning.roadmap_planner import RoadmapPlanner


class SalaryWeightedPlanner(RoadmapPlanner):
    """Like GreedyPlanner, but a place gained on a better-paying job counts for more (pay in $1,000s a month).
    Jobs that state no pay count for nothing here, but still break ties through the greedy measures."""

    @property
    def key(self) -> str:
        return "salary"

    @property
    def label(self) -> str:
        return "Best-paid jobs first"

    def value(self, report: dict) -> tuple:
        paid_places = sum(movement["places_gained"] * movement["salary"] / 1000
                          for movement in report["movements"] if movement["moved_up"])
        return paid_places, report["places_gained"], report["outcome_upgrades"], report["skill_matches_gained"]
