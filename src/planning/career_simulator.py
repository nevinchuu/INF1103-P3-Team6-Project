"""CareerSimulator: the Career What-If Simulator and Roadmap (the Phase 2 new feature, pure logic, no AI).

what_if answers "if I learned these skills, how would my job matches change?"; roadmap answers "which skills
should I learn, and in what order?". Both re-rank the jobs from the last search with the LogicManager they are
given, so a what-if is judged by exactly the rules the search used. Results are plain dicts the apps display.
"""

import copy
from collections.abc import Sequence

from planning.greedy_planner import GreedyPlanner
from logic.logic_manager import LogicManager, OUTCOME_FAIR, OUTCOME_STRETCH, OUTCOME_STRONG
from planning.roadmap_planner import RoadmapPlanner
from planning.salary_weighted_planner import SalaryWeightedPlanner

OUTCOME_ORDER = [OUTCOME_STRETCH, OUTCOME_FAIR, OUTCOME_STRONG]  # worst to best
MAX_SKILLS = 15  # most skills one what-if can test
MAX_STEPS = 10   # longest roadmap
MAX_SKILL_LENGTH = 60  # longest skill name accepted (the jobs' own skills are far shorter)
# Most skills offered as "skills you might learn". A broad search can leave a candidate short of 100+ skills,
# which is a wall of checkboxes to read and buries the controls under it. They are counted most-wanted first,
# so the ones cut are the ones fewest jobs asked for. This caps what a roadmap chooses from too, so "tick
# none to let it choose from all of them" means the same list the page is showing.
MAX_LEARNABLE_SKILLS = 20


class CareerSimulator:
    """Tests skills the candidate might learn against the jobs from a search, and plans which to learn first.
    Input that can't be simulated raises ValueError with a message written for the user."""

    def __init__(self, planners: Sequence[RoadmapPlanner] | None = None) -> None:
        # Aggregation: the planners are created outside and handed in, and exist on their own; a new strategy is
        # added by passing it in, without changing this class (OCP). None means the two standard planners.
        chosen = planners if planners is not None else (GreedyPlanner(), SalaryWeightedPlanner())
        self.__planners = {planner.key: planner for planner in chosen}

    @property
    def planners(self) -> list[tuple[str, str]]:
        """(key, label) of each planner, for a form or menu."""
        return [(planner.key, planner.label) for planner in self.__planners.values()]

    # ---------- what the apps call ----------

    def what_if(self, logic: LogicManager, profile: dict, jobs: Sequence[dict], skills: Sequence[str],
                top_n: int = 5) -> dict:
        """How the jobs would rank if the candidate had these skills (see __evaluate for the report's keys).

        Association: logic is only used for this call (its ranking and outcome rules), never kept or owned."""
        self.__check_jobs(jobs)
        top_n = self.__check_top_n(top_n)
        checked = self.__check_skills(skills, profile, jobs)
        return self.__evaluate(logic, profile, jobs, checked, top_n)

    def roadmap(self, logic: LogicManager, profile: dict, jobs: Sequence[dict], planner_key: str, steps: int = 3,
                top_n: int = 5, skills: Sequence[str] | None = None) -> dict:
        """Up to steps skills to learn in order, each the one the chosen planner scores highest next. skills limits
        which skills it may pick (default: every skill the jobs ask for that the candidate lacks). Stops early
        when no remaining skill improves anything.

        Returns {"planner_label", "steps": [{"skill", "places_gained", "report"}], "report", "skills",
        "is_empty"}, where report is the whole roadmap so far measured against today."""
        self.__check_jobs(jobs)
        top_n = self.__check_top_n(top_n)
        steps = self.__check_steps(steps)
        planner = self.__planners.get(planner_key)
        if planner is None:
            raise ValueError(f'Unknown planner "{planner_key}". Choose one of: {", ".join(self.__planners)}.')
        # A roadmap may choose from any number of ticked skills: it only tests them one at a time
        remaining = (self.__check_skills(skills, profile, jobs, limit=False) if skills is not None
                     else self.learnable_skills(jobs))

        chosen: list[str] = []
        current = self.__evaluate(logic, profile, jobs, chosen, top_n)
        roadmap_steps = []
        while remaining and len(roadmap_steps) < steps:
            best_skill, best_report = None, current
            for skill in remaining:
                trial = self.__evaluate(logic, profile, jobs, chosen + [skill], top_n)
                # Polymorphism: whichever planner was chosen scores the result its own way, with no type checks.
                # Strictly better only, so equal skills keep their order (most-wanted first)
                if planner.value(trial) > planner.value(best_report):
                    best_skill, best_report = skill, trial
            if best_skill is None:
                break  # nothing left improves anything
            roadmap_steps.append({"skill": best_skill, "report": best_report,
                                  "places_gained": best_report["places_gained"] - current["places_gained"]})
            chosen.append(best_skill)
            remaining.remove(best_skill)
            current = best_report
        return {"planner_label": planner.label, "steps": roadmap_steps, "report": current,
                "skills": [step["skill"] for step in roadmap_steps], "is_empty": not roadmap_steps}

    def learnable_skills(self, jobs: Sequence[dict]) -> list[str]:
        """The MAX_LEARNABLE_SKILLS skills most jobs ask for that the candidate lacks, most-wanted first (ties
        alphabetical), each written as the first job wrote it."""
        counts: dict[str, int] = {}
        names: dict[str, str] = {}
        for job in jobs:
            for skill in {self.normalise(name): name for name in job.get("missing_skills", [])}.values():
                key = self.normalise(skill)
                names.setdefault(key, skill.strip())
                counts[key] = counts.get(key, 0) + 1
        ordered = sorted(counts, key=lambda key: (-counts[key], key))
        return [names[key] for key in ordered[:MAX_LEARNABLE_SKILLS]]

    @staticmethod
    def normalise(skill: str) -> str:
        """A skill's comparison form: "SQL", " sql " and "Sql" are one skill."""
        return " ".join(skill.lower().split())

    @staticmethod
    def split(text: str) -> list[str]:
        """Skills typed as "SQL, Python, Tableau" -> ["SQL", "Python", "Tableau"] (blanks dropped)."""
        return [skill.strip() for skill in text.split(",") if skill.strip()]

    # ---------- the calculation ----------

    def __evaluate(self, logic: LogicManager, profile: dict, jobs: Sequence[dict], skills: Sequence[str],
                   top_n: int) -> dict:
        """Ranks the jobs as they are and as they would be with the new skills, and compares.

        Returns {"skills", "top_n", "movements" (best match after first), "jobs_moved_up", "places_gained",
        "outcome_upgrades", "skill_matches_gained", "entered_top", "left_top"}. Each movement is
        {"index", "title", "company", "rank_before", "rank_after", "outcome_before", "outcome_after",
        "skills_gained", "salary", "places_gained", "moved_up", "outcome_upgraded"}. The profile and jobs are
        never changed: everything is worked out on copies."""
        after_profile = copy.deepcopy(profile)
        after_profile["core_skills"] = list(profile.get("core_skills", [])) + list(skills)
        after_jobs = [self.__with_skills(job, skills) for job in jobs]

        rank_before = {index: place for place, index in enumerate(self.__rank_order(logic, profile, jobs), start=1)}
        listed_before = sum(bool(job.get("matched_skills")) for job in jobs)
        listed_after = sum(bool(job.get("matched_skills")) for job in after_jobs)
        movements = []
        for place, index in enumerate(self.__rank_order(logic, after_profile, after_jobs), start=1):
            job, after = jobs[index], after_jobs[index]
            outcome_before = logic.match_outcome(profile, job)[0]
            outcome_after = logic.match_outcome(after_profile, after)[0]
            gained = rank_before[index] - place
            movements.append({
                "index": index, "title": job.get("title", "N/A"), "company": job.get("company", "N/A"),
                "rank_before": rank_before[index], "rank_after": place,
                "outcome_before": outcome_before, "outcome_after": outcome_after,
                "skills_gained": [match["job_skill"]
                                  for match in after["matched_skills"][len(job.get("matched_skills", [])):]],
                # The results page hides a job that matches none of the candidate's skills: "not listed"
                "listed_before": rank_before[index] <= listed_before, "listed_after": place <= listed_after,
                "salary": self.__average_salary(job), "places_gained": gained, "moved_up": gained > 0,
                "outcome_upgraded": OUTCOME_ORDER.index(outcome_after) > OUTCOME_ORDER.index(outcome_before)})
        return {
            "skills": list(skills), "top_n": top_n, "movements": movements,
            "jobs_moved_up": sum(movement["moved_up"] for movement in movements),
            "places_gained": sum(movement["places_gained"] for movement in movements if movement["moved_up"]),
            "outcome_upgrades": sum(movement["outcome_upgraded"] for movement in movements),
            "skill_matches_gained": sum(len(movement["skills_gained"]) for movement in movements),
            # Only a listed job can be in the top list: one matching none of the skills isn't on the results page
            "entered_top": [movement for movement in movements
                            if movement["listed_after"] and movement["rank_after"] <= top_n
                            and not (movement["listed_before"] and movement["rank_before"] <= top_n)],
            "left_top": [movement for movement in movements
                         if movement["listed_before"] and movement["rank_before"] <= top_n
                         and not (movement["listed_after"] and movement["rank_after"] <= top_n)],
        }

    def __with_skills(self, job: dict, skills: Sequence[str]) -> dict:
        """A copy of the job where each new skill it was missing is matched instead. The job is unchanged."""
        gained = {self.normalise(skill): skill for skill in skills}
        missing, newly_matched = [], []
        for skill in job.get("missing_skills", []):
            if self.normalise(skill) in gained:
                newly_matched.append({"job_skill": skill, "candidate_skill": gained[self.normalise(skill)]})
            else:
                missing.append(skill)
        return {**job, "missing_skills": missing, "matched_skills": list(job.get("matched_skills", [])) + newly_matched}

    @staticmethod
    def __rank_order(logic: LogicManager, profile: dict, jobs: Sequence[dict]) -> list[int]:
        """The indexes of the jobs, best match first (ties keep their original order). Jobs matching none of the
        candidate's skills come last, as the results page leaves them out, so the listed jobs keep its order."""
        return sorted(range(len(jobs)), key=lambda index: (not jobs[index].get("matched_skills"),
                                                           logic.sort_key(profile, jobs[index])))

    @staticmethod
    def __average_salary(job: dict) -> float:
        """Average monthly pay the job lists, 0 if it lists none."""
        low, high = job.get("min_salary") or 0, job.get("max_salary") or 0
        if low and high:
            return (low + high) / 2
        return float(low or high)

    # ---------- checking what the user asked for ----------

    @staticmethod
    def __check_jobs(jobs: Sequence[dict]) -> None:
        if not jobs:
            raise ValueError("There are no jobs to compare yet. Run a search first.")

    @staticmethod
    def __check_top_n(top_n: int) -> int:
        if not isinstance(top_n, int) or isinstance(top_n, bool) or top_n < 1:
            raise ValueError("The top list must hold at least 1 job.")
        return top_n

    @staticmethod
    def __check_steps(steps: int) -> int:
        if not isinstance(steps, int) or isinstance(steps, bool) or not 1 <= steps <= MAX_STEPS:
            raise ValueError(f"A roadmap needs between 1 and {MAX_STEPS} steps.")
        return steps

    def __check_skills(self, raw: Sequence[str], profile: dict, jobs: Sequence[dict], limit: bool = True) -> list[str]:
        """The skills to test, each written as the jobs write it. Rejects an empty list, more than MAX_SKILLS (when
        limit), a very long name, a skill listed twice, one the candidate already has, and one that no job asks for
        (nothing to gain)."""
        cleaned = [skill.strip() for skill in raw if isinstance(skill, str) and skill.strip()]
        if not cleaned:
            # Says the way out, because a roadmap is the mode that does accept no skills at all
            raise ValueError("Choose at least one skill to test, or ask for a roadmap to have skills chosen for you.")
        if any(len(skill) > MAX_SKILL_LENGTH for skill in cleaned):
            raise ValueError(f"Each skill name must be {MAX_SKILL_LENGTH} characters or fewer.")
        if limit and len(cleaned) > MAX_SKILLS:
            raise ValueError(f"Please test {MAX_SKILLS} skills or fewer at a time.")

        seen: set[str] = set()
        for skill in cleaned:
            if self.normalise(skill) in seen:
                raise ValueError(f'"{skill}" is listed more than once.')
            seen.add(self.normalise(skill))

        have = {self.normalise(skill) for skill in profile.get("core_skills", [])}
        asked_for: dict[str, str] = {}
        for job in jobs:
            for skill in job.get("missing_skills", []):
                asked_for.setdefault(self.normalise(skill), skill.strip())
        checked = []
        for skill in cleaned:
            key = self.normalise(skill)
            if key in asked_for:
                checked.append(asked_for[key])
            elif key in have:
                raise ValueError(f'You already have "{skill}".')
            else:
                raise ValueError(f'None of these jobs asks for "{skill}", so learning it would change nothing.')
        return checked
