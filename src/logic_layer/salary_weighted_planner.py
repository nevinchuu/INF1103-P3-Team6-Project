"""SalaryWeightedPlanner: each roadmap step takes the skill that raises the pay of your top jobs the most."""

from logic_layer.roadmap_planner import RoadmapPlanner


class SalaryWeightedPlanner(RoadmapPlanner):
    """Scores a result by the total monthly pay of the jobs in the top list (the jobs the results page lists,
    counting only those that state their pay). A total, not an average: qualifying for one more paid job raises
    it, where it would pull an average down, which stopped plans after one skill. When the list is full the two
    agree: swapping a job for a better-paid one raises both. When no skill raises it, the step still goes to the
    best-paid progress: the pay of the jobs whose match gets better, then places gained weighted by each job's pay,
    then skills matched. So a plan has as many skills as asked for whenever the jobs ask for them (CareerSimulator
    tries pairs first when no single skill raises the pay)."""

    @property
    def key(self) -> str:
        return "salary"

    @property
    def label(self) -> str:
        return "Earn more"

    def value(self, report: dict) -> tuple:
        movements = report["movements"]
        paid = [movement["salary"] for movement in movements
                if movement["listed_after"] and movement["rank_after"] <= report["top_n"] and movement["salary"] > 0]
        top_pay = round(sum(paid))
        upgraded_pay = sum(movement["salary"] for movement in movements if movement["outcome_upgraded"])
        paid_places = sum(movement["salary"] * movement["places_gained"] for movement in movements
                          if movement["moved_up"])
        return top_pay, upgraded_pay, paid_places, report["skill_matches_gained"]
