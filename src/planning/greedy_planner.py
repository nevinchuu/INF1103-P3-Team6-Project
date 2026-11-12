"""GreedyPlanner: each roadmap step takes the skill that lifts the jobs the most places."""

from planning.roadmap_planner import RoadmapPlanner


class GreedyPlanner(RoadmapPlanner):
    """Most places gained first; then more match upgrades (e.g. Fair -> Strong); then more skills matched."""

    @property
    def key(self) -> str:
        return "greedy"

    @property
    def label(self) -> str:
        return "Biggest rank gain first"

    def value(self, report: dict) -> tuple:
        return report["places_gained"], report["outcome_upgrades"], report["skill_matches_gained"]
