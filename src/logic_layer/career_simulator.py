"""CareerSimulator: the Career What-If Simulator and Roadmap (the Phase 2 new feature, pure logic, no AI).

what_if answers "if I learned these skills, how would my job matches change?"; roadmap answers "which skills
should I learn, and in what order?". Both re-rank the jobs from the last search with the RankingRules they are
given (LogicManager, with the rules the search used), so a what-if is judged exactly as the search was. The
simulator knows only that interface, never the manager that owns it. Results are plain dicts the apps display.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Sequence

from logic_layer.greedy_planner import GreedyPlanner
from logic_layer.match_rule import OUTCOME_FAIR, OUTCOME_STRETCH, OUTCOME_STRONG
from logic_layer.ranking_rules import RankingRules
from logic_layer.roadmap_planner import RoadmapPlanner
from logic_layer.salary_weighted_planner import SalaryWeightedPlanner
from logic_layer.skill_name import SkillName

OUTCOME_ORDER = [OUTCOME_STRETCH, OUTCOME_FAIR, OUTCOME_STRONG]  # worst to best
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

    def what_if(self, rules: RankingRules, profile: dict, jobs: Sequence[dict], skills: Sequence[str],
                top_n: int = 5) -> dict:
        """How the jobs would rank if the candidate had these skills (see __evaluate for the report's keys).

        Dependency: rules is only used for this call (its ranking and outcome rules), never kept or owned."""
        self.__check_jobs(jobs)
        checked = self.__check_skills(skills, profile, jobs)
        return self.__evaluate(rules, profile, jobs, checked, top_n)

    def roadmap(self, rules: RankingRules, profile: dict, jobs: Sequence[dict], planner_key: str, steps: int = 3,
                top_n: int = 5, skills: Sequence[str] | None = None) -> dict:
        """Up to steps skills to learn in order, each step the one the chosen planner scores highest next. When no
        single skill improves the score, a step may be a pair that only helps together (it counts as two skills).
        skills limits which skills it may pick (default: every skill the jobs ask for that the candidate lacks).
        Stops early when nothing left improves anything.

        Returns {"planner_label", "steps": [{"skill", "skills", "places_gained", "report"}], "report", "skills",
        "is_empty"}: a step's "skill" names it ("Statistics + Tableau" for a pair), "skills" lists what it adds,
        and report is the whole roadmap so far measured against today."""
        self.__check_jobs(jobs)
        planner = self.__planners.get(planner_key)
        if planner is None:
            raise ValueError(f'Unknown planner "{planner_key}". Choose one of: {", ".join(self.__planners)}.')
        # A roadmap may choose from any number of ticked skills: it only tests them one at a time
        remaining = (self.__check_skills(skills, profile, jobs) if skills is not None
                     else self.learnable_skills(jobs))

        chosen: list[str] = []
        current = self.__evaluate(rules, profile, jobs, chosen, top_n)
        roadmap_steps = []

        def trial(extra: list[str]) -> dict:
            """The report if these skills were learned on top of the steps so far."""
            return self.__evaluate(rules, profile, jobs, chosen + extra, top_n)
        while remaining and len(chosen) < steps:
            found = self.__best_step(planner, trial, remaining, current, steps - len(chosen))
            if found is None:
                break  # nothing left improves anything
            skills, report = found
            roadmap_steps.append({"skill": " + ".join(skills), "skills": skills, "report": report,
                                  "places_gained": report["places_gained"] - current["places_gained"]})
            chosen.extend(skills)
            for skill in skills:
                remaining.remove(skill)
            current = report
        return {"planner_label": planner.label, "steps": roadmap_steps, "report": current,
                "skills": list(chosen), "is_empty": not roadmap_steps}

    @staticmethod
    def __best_step(planner: RoadmapPlanner, trial: Callable[[list[str]], dict], remaining: list[str],
                    current: dict, room: int) -> tuple[list[str], dict] | None:
        """The next step: the single skill the planner scores highest, if any beats the current report. When no
        single skill improves the planner's main goal (the first part of its value, e.g. the top list's pay) and
        room allows two more skills, a pair that does improve it wins instead (some skills only help together, e.g.
        the second skill a job asks for). A pair that doesn't move the main goal is never taken: two skills always
        match more than one, which is no reason to learn both. Strictly better only, so equal choices keep their
        order (most-wanted first). Returns (skills, report), or None if nothing improves the score."""
        best_skills, best_report = None, current
        # Polymorphism: whichever planner was chosen scores the result its own way, with no type checks
        for skill in remaining:
            report = trial([skill])
            if planner.value(report) > planner.value(best_report):
                best_skills, best_report = [skill], report
        main_goal_now = planner.value(current)[:1]
        if room >= 2 and planner.value(best_report)[:1] <= main_goal_now:
            best_pair, best_pair_report = None, None
            for index, first in enumerate(remaining):
                for second in remaining[index + 1:]:
                    report = trial([first, second])
                    if planner.value(report)[:1] > main_goal_now and (
                            best_pair_report is None or planner.value(report) > planner.value(best_pair_report)):
                        best_pair, best_pair_report = [first, second], report
            if best_pair:
                best_skills, best_report = best_pair, best_pair_report
        return (best_skills, best_report) if best_skills else None

    def learnable_skills(self, jobs: Sequence[dict]) -> list[str]:
        """The MAX_LEARNABLE_SKILLS skills most jobs ask for that the candidate lacks, most-wanted first (ties
        alphabetical). Names for one skill count as one ("UAT" and "User Acceptance Testing", see SkillName), written
        in full where any job wrote it in full, else as the first job wrote it."""
        names = self.__skill_names(jobs)
        counts: dict[str, int] = {}
        shown: dict[str, str] = {}
        for job in jobs:
            for skill in {names.key(name): name for name in job.get("missing_skills", [])}.values():
                key = names.key(skill)
                if key not in shown or (self.normalise(skill) == key != self.normalise(shown[key])):
                    shown[key] = skill.strip()  # the full name wins over an abbreviation
                counts[key] = counts.get(key, 0) + 1
        ordered = sorted(counts, key=lambda key: (-counts[key], key))
        return [shown[key] for key in ordered[:MAX_LEARNABLE_SKILLS]]

    @staticmethod
    def normalise(skill: str) -> str:
        """A skill's plain comparison form: "SQL", " sql " and "Sql" are one skill."""
        return " ".join(skill.lower().split())

    @staticmethod
    def __skill_names(jobs: Sequence[dict], *more: Sequence[str]) -> SkillName:
        """How skill names compare across these jobs (and any more names, e.g. the candidate's): every name they
        use is in the set, so an abbreviation finds the full name another job wrote."""
        pool = [skill for job in jobs for key in ("required_skills", "missing_skills") for skill in job.get(key, [])]
        pool += [match["job_skill"] for job in jobs for match in job.get("matched_skills", [])]
        return SkillName(pool + [name for names in more for name in names])

    # ---------- the calculation ----------

    def __evaluate(self, rules: RankingRules, profile: dict, jobs: Sequence[dict], skills: Sequence[str],
                   top_n: int) -> dict:
        """Ranks the jobs as they are and as they would be with the new skills, and compares.

        Returns {"skills", "top_n", "movements" (best match after first), "jobs_moved_up", "places_gained",
        "outcome_upgrades", "skill_matches_gained", "entered_top", "left_top"}. Each movement is
        {"index", "title", "company", "rank_before", "rank_after", "outcome_before", "outcome_after",
        "skills_gained", "salary", "places_gained", "moved_up", "outcome_upgraded"}. The profile and jobs are
        never changed: everything is worked out on copies."""
        after_profile = copy.deepcopy(profile)
        after_profile["core_skills"] = list(profile.get("core_skills", [])) + list(skills)
        names = self.__skill_names(jobs, skills)
        after_jobs = [self.__with_skills(job, skills, names) for job in jobs]

        rank_before = {index: place for place, index in enumerate(self.__rank_order(rules, profile, jobs), start=1)}
        listed_before = sum(bool(job.get("matched_skills")) for job in jobs)
        listed_after = sum(bool(job.get("matched_skills")) for job in after_jobs)
        movements = []
        for place, index in enumerate(self.__rank_order(rules, after_profile, after_jobs), start=1):
            job, after = jobs[index], after_jobs[index]
            outcome_before = rules.match_outcome(profile, job)[0]
            outcome_after = rules.match_outcome(after_profile, after)[0]
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

    @staticmethod
    def __with_skills(job: dict, skills: Sequence[str], names: SkillName) -> dict:
        """A copy of the job where each new skill it was missing is matched instead, whichever name the job used
        for it ("UAT" when "User Acceptance Testing" is learned). The job is unchanged."""
        gained = {names.key(skill): skill for skill in skills}
        missing, newly_matched = [], []
        for skill in job.get("missing_skills", []):
            if names.key(skill) in gained:
                newly_matched.append({"job_skill": skill, "candidate_skill": gained[names.key(skill)]})
            else:
                missing.append(skill)
        return {**job, "missing_skills": missing, "matched_skills": list(job.get("matched_skills", [])) + newly_matched}

    @staticmethod
    def __rank_order(rules: RankingRules, profile: dict, jobs: Sequence[dict]) -> list[int]:
        """The indexes of the jobs, best match first (ties keep their original order). Jobs matching none of the
        candidate's skills come last, as the results page leaves them out, so the listed jobs keep its order."""
        return sorted(range(len(jobs)), key=lambda index: (not jobs[index].get("matched_skills"),
                                                           rules.sort_key(profile, jobs[index])))

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

    def __check_skills(self, raw: Sequence[str], profile: dict, jobs: Sequence[dict]) -> list[str]:
        """The skills to test, each written as the jobs write it. Rejects one the candidate already has and one that
        no job asks for (nothing to gain). The skill list's own format (how many, how long, listed twice) is
        checked first, by InputValidator.check_what_if."""
        cleaned = [skill.strip() for skill in raw]
        names = self.__skill_names(jobs, profile.get("core_skills", []), cleaned)
        have = {names.key(skill) for skill in profile.get("core_skills", [])}
        asked_for: dict[str, str] = {}  # each skill the jobs ask for, as the page writes it
        for job in jobs:
            for skill in job.get("missing_skills", []):
                asked_for.setdefault(names.key(skill), skill.strip())
        for skill in self.learnable_skills(jobs):  # the name the page offers (in full where a job wrote it so)
            asked_for[names.key(skill)] = skill
        checked = []
        for skill in cleaned:
            key = names.key(skill)
            if key in asked_for:
                checked.append(asked_for[key])
            elif key in have:
                raise ValueError(f'You already have "{skill}".')
            else:
                raise ValueError(f'None of these jobs asks for "{skill}", so learning it would change nothing.')
        return checked
