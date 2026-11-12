"""Tests for the Career What-If Simulator and Roadmap: CareerSimulator and the RoadmapPlanner strategies.

Pure logic, so nothing here needs an AI, a network or a file: jobs are hard-coded records in the shape the AI step
produces (required, matched and missing skills).
"""

import copy
import random

import pytest

from planning import career_simulator
from planning.career_simulator import CareerSimulator, MAX_SKILLS, MAX_STEPS
from planning.greedy_planner import GreedyPlanner
from logic.logic_manager import LogicManager, OUTCOME_FAIR, OUTCOME_STRETCH, OUTCOME_STRONG
from planning.roadmap_planner import RoadmapPlanner
from planning.salary_weighted_planner import SalaryWeightedPlanner

PROFILE = {"years_of_experience": 1, "highest_qualification": "Diploma", "seniority_level": "Fresh/entry level",
           "core_skills": ["SQL"]}
LOGIC = LogicManager()


def make_job(title: str, required: list[str], matched: list[str], salary: int = 0, **changes) -> dict:
    return {"title": title, "company": f"{title} Ltd", "min_years_experience": 0, "min_education": "Not specified",
            "position_levels": ["Executive"], "required_skills": list(required),
            "matched_skills": [{"job_skill": skill, "candidate_skill": skill} for skill in matched],
            "missing_skills": [skill for skill in required if skill not in matched],
            "min_salary": salary, "max_salary": salary, **changes}


def jobs() -> list[dict]:
    return [make_job("A", ["SQL", "Python", "Tableau"], ["SQL"], 3000),
            make_job("B", ["SQL", "Python"], ["SQL"], 6000),
            make_job("C", ["Tableau", "Excel"], [], 4000)]


@pytest.fixture
def simulator() -> CareerSimulator:
    return CareerSimulator()


# ---------- what_if: the calculation ----------

def test_the_ranking_is_the_logic_managers_on_random_jobs(simulator):
    # The simulator must rank exactly as the search did, whatever the jobs are
    rng = random.Random(7)
    skills = ["SQL", "Python", "Tableau", "Excel", "Java", "Go"]
    pool = []
    for number in range(25):
        required = rng.sample(skills, rng.randint(1, 5))
        matched = [skill for skill in required if rng.random() < 0.4]
        pool.append(make_job(f"J{number}", required, matched, min_years_experience=rng.randint(0, 3)))
    report = simulator.what_if(LOGIC, PROFILE, pool, [simulator.learnable_skills(pool)[0]], 5)
    by_rank = sorted(report["movements"], key=lambda m: m["rank_before"])
    listed = [m["title"] for m in by_rank if m["listed_before"]]
    # The listed jobs are in the results page's order; jobs matching no skills come after them
    assert listed == [job["title"] for job in LOGIC.rank_jobs(PROFILE, [job for job in pool if job["matched_skills"]])]
    assert all(not m["listed_before"] for m in by_rank[len(listed):])


def test_learning_a_skill_lifts_the_jobs_that_ask_for_it(simulator):
    report = simulator.what_if(LOGIC, PROFILE, jobs(), ["Tableau"], top_n=2)
    moves = {m["title"]: m for m in report["movements"]}
    assert (moves["A"]["rank_before"], moves["A"]["rank_after"]) == (2, 1) and moves["A"]["moved_up"]
    assert (moves["B"]["rank_before"], moves["B"]["rank_after"]) == (1, 2) and moves["B"]["places_gained"] == -1
    assert moves["A"]["skills_gained"] == ["Tableau"] and moves["B"]["skills_gained"] == []
    assert report["jobs_moved_up"] == 1 and report["places_gained"] == 1
    assert [m["title"] for m in report["movements"]] == ["A", "B", "C"]  # best match after the scenario first


def test_jobs_entering_and_leaving_the_top_n_are_reported(simulator):
    pool = [make_job("X", ["SQL", "Go"], ["SQL"]), make_job("Y", ["SQL", "Rust"], ["SQL"]),
            make_job("Z", ["Kotlin", "Swift", "Dart"], [])]
    report = simulator.what_if(LOGIC, PROFILE, pool, ["Kotlin", "Swift", "Dart"], top_n=2)
    assert [m["title"] for m in report["entered_top"]] == ["Z"]
    assert [m["title"] for m in report["left_top"]] == ["Y"]


def test_a_match_can_be_upgraded_by_a_skill(simulator):
    report = simulator.what_if(LOGIC, PROFILE, [make_job("B", ["SQL", "Python"], ["SQL"])], ["Python"])
    movement = report["movements"][0]
    assert movement["outcome_before"] == OUTCOME_FAIR and movement["outcome_after"] == OUTCOME_STRONG
    assert movement["outcome_upgraded"] and report["outcome_upgrades"] == 1 and report["skill_matches_gained"] == 1


def test_a_job_the_candidate_cannot_meet_stays_a_stretch(simulator):
    pool = [make_job("Senior", ["SQL", "Python"], ["SQL"], min_years_experience=9)]
    movement = simulator.what_if(LOGIC, PROFILE, pool, ["Python"])["movements"][0]
    assert movement["outcome_before"] == movement["outcome_after"] == OUTCOME_STRETCH


def test_a_jobs_salary_is_the_average_of_the_listed_range(simulator):
    pool = [make_job("Range", ["SQL", "Go"], ["SQL"], min_salary=4000, max_salary=6000),
            make_job("Top only", ["SQL"], ["SQL"], min_salary=0, max_salary=6000),
            make_job("Unstated", ["SQL"], ["SQL"], min_salary=0, max_salary=0)]
    report = simulator.what_if(LOGIC, PROFILE, pool, ["Go"], 3)
    assert {m["title"]: m["salary"] for m in report["movements"]} == {"Range": 5000, "Top only": 6000, "Unstated": 0}


def test_jobs_that_tie_keep_their_original_order(simulator):
    pool = [make_job("First", ["SQL", "Go"], ["SQL"]), make_job("Second", ["SQL", "Go"], ["SQL"]),
            make_job("Third", ["SQL", "Go"], ["SQL"])]
    report = simulator.what_if(LOGIC, PROFILE, pool, ["Go"], 2)
    assert [m["title"] for m in report["movements"]] == ["First", "Second", "Third"]
    assert all(m["rank_before"] == m["rank_after"] for m in report["movements"])


def test_learnable_skills_are_most_wanted_first_with_ties_alphabetical(simulator):
    pool = [make_job("A", ["Python", "Excel", "Tableau"], []), make_job("B", ["python", "Tableau"], []),
            make_job("C", ["Go"], [])]
    assert simulator.learnable_skills(pool) == ["Python", "Tableau", "Excel", "Go"]


def test_only_the_most_wanted_skills_are_offered(simulator):
    """A broad search can leave a candidate short of 100+ skills. The ones kept are the ones most jobs ask
    for, and the cap reaches the roadmap too, so "choose from all of them" means the list being shown."""
    wanted = [f"Skill {letter}" for letter in "ABC"]          # asked for by two jobs each
    rest = [f"Other {number:03}" for number in range(50)]     # asked for by one job each
    pool = [make_job("A", wanted + rest, []), make_job("B", wanted, [])]

    offered = simulator.learnable_skills(pool)
    assert len(offered) == career_simulator.MAX_LEARNABLE_SKILLS
    assert offered[:3] == wanted  # the most wanted survive the cut; the one-job tail is what gets trimmed

    planned = simulator.roadmap(LOGIC, PROFILE, pool, "greedy", skills=None)
    assert set(planned["skills"]) <= set(offered)


def test_normalise_ignores_case_and_extra_spaces():
    assert CareerSimulator.normalise("  Power   BI ") == CareerSimulator.normalise("power bi") == "power bi"


def test_the_real_profile_and_jobs_are_never_changed_by_a_simulation(simulator):
    profile, pool = copy.deepcopy(PROFILE), jobs()
    before = copy.deepcopy(pool)
    simulator.what_if(LOGIC, profile, pool, ["Python", "Tableau"])
    simulator.roadmap(LOGIC, profile, pool, "greedy", steps=3)
    assert profile == PROFILE and pool == before


# ---------- input validation ----------

@pytest.mark.parametrize("raw, message", [
    ([], "at least one skill"),
    (["", "   "], "at least one skill"),
    (["Python", "python"], "listed more than once"),
    (["sql"], 'You already have "sql"'),
    (["Cobol"], 'None of these jobs asks for "Cobol"'),
    (["Python"] + [f"S{n}" for n in range(MAX_SKILLS)], "or fewer"),
])
def test_bad_skill_lists_are_rejected_with_a_message(simulator, raw, message):
    with pytest.raises(ValueError, match=message):
        simulator.what_if(LOGIC, PROFILE, jobs(), raw)


def test_ticking_nothing_is_an_error_to_test_but_means_every_skill_to_a_roadmap(simulator):
    """The two modes read an empty tick list oppositely, which the page has to say: testing nothing has
    nothing to measure, while a roadmap with nothing ticked may choose from everything."""
    with pytest.raises(ValueError, match="at least one skill") as rejected:
        simulator.what_if(LOGIC, PROFILE, jobs(), [])
    # the message must name the mode that does accept an empty list, or it reads as a dead end
    assert "roadmap" in str(rejected.value)

    planned = simulator.roadmap(LOGIC, PROFILE, jobs(), "greedy", skills=None)
    assert planned["steps"], "a roadmap with nothing ticked should still choose skills"
    assert set(planned["skills"]) <= set(simulator.learnable_skills(jobs()))


def test_skills_are_matched_ignoring_case_and_given_back_as_the_jobs_write_them(simulator):
    assert simulator.what_if(LOGIC, PROFILE, jobs(), ["  python ", "TABLEAU"])["skills"] == ["Python", "Tableau"]


def test_non_text_skills_are_ignored_not_crashed_on(simulator):
    with pytest.raises(ValueError, match="at least one skill"):
        simulator.what_if(LOGIC, PROFILE, jobs(), [None, 5])  # type: ignore[list-item]


def test_no_jobs_to_compare_is_an_error(simulator):
    with pytest.raises(ValueError, match="Run a search first"):
        simulator.what_if(LOGIC, PROFILE, [], ["Python"])
    with pytest.raises(ValueError, match="Run a search first"):
        simulator.roadmap(LOGIC, PROFILE, [], "greedy")


@pytest.mark.parametrize("top_n", [0, -1, "5", None, True])
def test_the_top_list_size_must_be_a_positive_whole_number(simulator, top_n):
    with pytest.raises(ValueError, match="at least 1 job"):
        simulator.what_if(LOGIC, PROFILE, jobs(), ["Python"], top_n=top_n)


@pytest.mark.parametrize("steps", [0, -2, MAX_STEPS + 1, 2.5, "3", True])
def test_roadmap_length_must_be_within_limits(simulator, steps):
    with pytest.raises(ValueError, match="between 1 and"):
        simulator.roadmap(LOGIC, PROFILE, jobs(), "greedy", steps=steps)


def test_an_unknown_planner_is_an_error_that_names_the_choices(simulator):
    with pytest.raises(ValueError, match="greedy, salary"):
        simulator.roadmap(LOGIC, PROFILE, jobs(), "magic")


def test_typed_skills_are_split_on_commas():
    assert CareerSimulator.split(" Python, ,SQL ,, Tableau") == ["Python", "SQL", "Tableau"]
    assert CareerSimulator.split("") == []


# ---------- roadmap and the planners ----------

def test_the_greedy_planner_takes_the_best_rank_gain_first(simulator):
    roadmap = simulator.roadmap(LOGIC, PROFILE, jobs(), "greedy", steps=3)
    assert roadmap["skills"][0] == "Tableau" and roadmap["planner_label"] == GreedyPlanner().label
    assert roadmap["steps"][0]["places_gained"] == 1
    assert roadmap["report"]["skills"] == roadmap["skills"]  # the final report covers every step


def test_the_salary_weighted_planner_prefers_the_better_paid_jobs(simulator):
    pool = [make_job("Low", ["SQL", "Excel"], ["SQL"], salary=2000),
            make_job("High", ["SQL", "Python"], ["SQL"], salary=9000),
            make_job("Mid", ["SQL", "Excel", "Go"], ["SQL"], salary=3000)]
    # Go lifts the mid-paid job two places, Python lifts the best-paid job one place: the greedy planner counts
    # places (Go), the salary-weighted one counts places times pay (Python)
    assert simulator.roadmap(LOGIC, PROFILE, pool, "greedy", steps=1)["skills"] == ["Go"]
    salary = simulator.roadmap(LOGIC, PROFILE, pool, "salary", steps=1)
    assert salary["skills"] == ["Python"] and salary["planner_label"] == SalaryWeightedPlanner().label


def test_a_roadmap_stops_at_the_requested_length(simulator):
    assert len(simulator.roadmap(LOGIC, PROFILE, jobs(), "greedy", steps=1)["steps"]) == 1
    assert len(simulator.roadmap(LOGIC, PROFILE, jobs(), "greedy", steps=MAX_STEPS)["steps"]) <= 4  # only 4 skills


def test_each_step_builds_on_the_ones_before(simulator):
    steps = simulator.roadmap(LOGIC, PROFILE, jobs(), "greedy", steps=3)["steps"]
    for earlier, later in zip(steps, steps[1:]):
        assert later["report"]["skills"][:len(earlier["report"]["skills"])] == earlier["report"]["skills"]
        assert len(later["report"]["skills"]) == len(earlier["report"]["skills"]) + 1


def test_the_roadmap_can_be_limited_to_skills_the_user_chose(simulator):
    roadmap = simulator.roadmap(LOGIC, PROFILE, jobs(), "greedy", steps=3, skills=["Excel", "python"])
    assert set(roadmap["skills"]) <= {"Excel", "Python"}
    with pytest.raises(ValueError, match="more than once"):
        simulator.roadmap(LOGIC, PROFILE, jobs(), "greedy", skills=["Excel", "excel"])


def test_no_missing_skills_gives_an_empty_roadmap_not_an_error(simulator):
    complete = [make_job("Done", ["SQL"], ["SQL"]), make_job("Done2", ["SQL"], ["SQL"])]
    roadmap = simulator.roadmap(LOGIC, PROFILE, complete, "greedy", steps=3)
    assert roadmap["is_empty"] and roadmap["skills"] == []
    assert roadmap["report"]["places_gained"] == 0 and simulator.learnable_skills(complete) == []


def test_testing_a_skill_when_nothing_is_missing_says_no_job_asks_for_it(simulator):
    with pytest.raises(ValueError, match="asks for"):
        simulator.what_if(LOGIC, PROFILE, [make_job("Done", ["SQL"], ["SQL"])], ["Python"])


def test_with_one_job_the_roadmap_still_works_through_matches_and_upgrades(simulator):
    # A single job can't move up, but its match can improve, so the skills still have value
    pool = [make_job("Only", ["SQL", "Python", "Tableau"], ["SQL"])]
    roadmap = simulator.roadmap(LOGIC, PROFILE, pool, "greedy", steps=3)
    assert roadmap["skills"] == ["Python", "Tableau"]
    assert all(step["places_gained"] == 0 for step in roadmap["steps"])
    assert roadmap["report"]["movements"][0]["outcome_after"] == OUTCOME_STRONG


def test_ties_keep_the_order_of_the_most_wanted_skills_and_are_repeatable(simulator):
    pool = [make_job("A", ["SQL", "Alpha", "Beta"], ["SQL"]), make_job("B", ["SQL", "Alpha", "Beta"], ["SQL"])]
    first = simulator.roadmap(LOGIC, PROFILE, pool, "greedy", steps=2)
    second = simulator.roadmap(LOGIC, PROFILE, pool, "greedy", steps=2)
    assert first["skills"] == second["skills"] == ["Alpha", "Beta"]  # equal value: as learnable_skills orders them


# ---------- the RoadmapPlanner interface: new strategies plug in (OCP) and any one can stand in (LSP) ----------

class IndifferentPlanner(RoadmapPlanner):
    """Values every result the same, so no skill is ever an improvement."""

    @property
    def key(self) -> str:
        return "indifferent"

    @property
    def label(self) -> str:
        return "Indifferent"

    def value(self, report: dict) -> tuple:
        return (0,)


class FewestJobsFirstPlanner(RoadmapPlanner):
    """A new strategy, written in a test: it prefers skills that move the *fewest* jobs. Adding it needs no change
    to RoadmapPlanner or CareerSimulator."""

    @property
    def key(self) -> str:
        return "narrow"

    @property
    def label(self) -> str:
        return "Narrowest skill first"

    def value(self, report: dict) -> tuple:
        return (-report["jobs_moved_up"], report["skill_matches_gained"])


def test_a_planner_stops_when_no_skill_improves_its_value():
    roadmap = CareerSimulator([IndifferentPlanner()]).roadmap(LOGIC, PROFILE, jobs(), "indifferent", steps=5)
    assert roadmap["is_empty"] and roadmap["report"]["skills"] == []


def test_a_new_planner_plugs_in_without_changing_the_simulator():
    simulator = CareerSimulator([GreedyPlanner(), FewestJobsFirstPlanner()])
    assert simulator.planners == [("greedy", "Biggest rank gain first"), ("narrow", "Narrowest skill first")]
    roadmap = simulator.roadmap(LOGIC, PROFILE, jobs(), "narrow", steps=2)
    assert roadmap["planner_label"] == "Narrowest skill first" and len(roadmap["steps"]) >= 1
    with pytest.raises(ValueError):
        simulator.roadmap(LOGIC, PROFILE, jobs(), "salary")  # not given to this simulator


def test_the_interface_cannot_be_used_on_its_own_or_half_implemented():
    with pytest.raises(TypeError):
        RoadmapPlanner()  # type: ignore[abstract]

    class NoValue(RoadmapPlanner):
        key = "x"  # type: ignore[assignment]
        label = "x"  # type: ignore[assignment]

    with pytest.raises(TypeError):
        NoValue()  # type: ignore[abstract]


@pytest.mark.parametrize("planner", [GreedyPlanner(), SalaryWeightedPlanner(), FewestJobsFirstPlanner()])
def test_planners_can_be_swapped_with_the_same_calls(planner):
    # Liskov: any planner answers the same call with the same kind of result
    roadmap = CareerSimulator([planner]).roadmap(LOGIC, PROFILE, jobs(), planner.key, steps=2,
                                                 skills=["Python", "Tableau"])
    assert isinstance(planner, RoadmapPlanner)
    assert len(roadmap["steps"]) <= 2 and set(roadmap["skills"]) <= {"Python", "Tableau"}
    assert isinstance(planner.value(roadmap["report"]), tuple)


def test_the_ranking_rules_can_be_changed_and_the_simulator_follows(simulator):
    # The simulator ranks through the LogicManager it is given, so different rules give a different what-if
    only_count = LogicManager(rules=[LogicManager.rank_by_skill_count])
    pool = [make_job("Few", ["SQL", "Go"], ["SQL"], min_years_experience=0),
            make_job("Many", ["SQL", "Python", "Go"], ["SQL", "Python"], min_years_experience=5)]
    default_top = simulator.what_if(LOGIC, PROFILE, pool, ["Go"])["movements"][0]["title"]
    count_top = simulator.what_if(only_count, PROFILE, pool, ["Go"])["movements"][0]["title"]
    assert default_top != count_top


# ---------- the what-if matches the results page ----------

def test_a_job_matching_no_skills_is_not_listed_and_can_enter_the_top_list(simulator):
    # C matches none of the candidate's skills, so the results page hides it: it isn't "rank 3" before
    report = simulator.what_if(LOGIC, PROFILE, jobs(), ["Tableau", "Excel"], top_n=5)
    moves = {m["title"]: m for m in report["movements"]}
    assert not moves["C"]["listed_before"] and moves["C"]["listed_after"]
    assert [m["title"] for m in report["entered_top"]] == ["C"]
    # A job that stays unlisted never counts as being in the top list
    still_hidden = simulator.what_if(LOGIC, PROFILE, jobs(), ["Python"], top_n=5)
    assert not {m["title"] for m in still_hidden["entered_top"]} and not still_hidden["left_top"]


def test_a_roadmap_may_choose_from_more_ticked_skills_than_a_test(simulator):
    many = [f"Skill{number}" for number in range(MAX_SKILLS + 3)]
    pool = [make_job("Big", ["SQL", *many], ["SQL"])]
    with pytest.raises(ValueError, match="or fewer"):
        simulator.what_if(LOGIC, PROFILE, pool, many)
    assert simulator.roadmap(LOGIC, PROFILE, pool, "greedy", 2, 5, many)["steps"]


def test_a_very_long_skill_name_is_rejected_briefly(simulator):
    with pytest.raises(ValueError) as error:
        simulator.what_if(LOGIC, PROFILE, jobs(), ["x" * 10_000])
    assert "characters or fewer" in str(error.value) and len(str(error.value)) < 100
