# New feature: the Career What-If Simulator and Roadmap

## 1. The problem

A search ranks the jobs against the skills on the resume and shows, for each job, the skills the candidate is missing.
It does not say which of those skills is worth learning first, or how much learning one would change. A candidate looking
at twenty missing skills can only guess. The simulator answers two questions about the jobs they have just seen:

- **What if I learned these skills?** It re-ranks the jobs as if the candidate already had them.
- **Which skills should I learn, and in what order?** It builds a roadmap of up to ten skills, each chosen as the one that
  helps most given the ones before it, and stops early when no remaining skill improves anything.

## 2. How a user reaches it

It is available after a search, in both apps, and uses the jobs from that search.

- **Console.** The main menu has *Plan skills to learn (what-if)*. Before any search it says *Run a search first: the what-if
  works on the jobs it finds.* After one, it asks whether to test skills or build a roadmap, which planner to use and how many
  skills.
- **Web.** The results page has a *What if I learned...?* button that opens `/whatif/<search_id>`. The form takes the mode
  (test skills, or a roadmap with one of the planners), the roadmap length, the size of the top list, skills ticked from the
  ones the jobs ask for, and any other skills typed in, separated by commas.

Both apps call the same `CareerSimulator`, so they give the same answer. `IOManager` words the console result and the
`whatif.html` template shows the web one.

## 3. It runs on the AI-enriched jobs

The simulator makes no AI call. It works on the jobs the search has already analysed. For each job, `AIManager` extracted the
skills it requires, and `LogicManager.add_requirements` compared them with the candidate's skills to give a list of
`matched_skills` and a list of `missing_skills`. A what-if moves the chosen skills from missing to matched on **a copy** of each
job, ranks the copies with the same `LogicManager` rules the search used, and compares the order before and after. Three things
follow:

- The AI cost is paid once, during the search. Every what-if after that is instant and gives the same answer each time.
- A what-if is judged by exactly the rules that ranked the search, so the two can never disagree.
- The candidate's real profile and jobs are never changed.

The report says how many jobs moved up and by how many places in total, how many matches were upgraded (for example Fair to
Strong), how many skill matches were gained, and which jobs entered or left the top list.

## 4. Classes

| Class | Role |
|---|---|
| `CareerSimulator` (`src/planning/`) | Runs `what_if` and `roadmap`. Holds its planners (aggregation) and is given a `LogicManager` in each call (association) |
| `RoadmapPlanner` | The interface: a `key`, a `label` and `value(report)`, which scores a result as a tuple that is compared left to right |
| `GreedyPlanner` | "Biggest rank gain first": most places gained, then most match upgrades, then most skills matched |
| `SalaryWeightedPlanner` | "Best-paid jobs first": a place gained on a better-paying job counts for more. Jobs with no listed pay count for nothing but still break ties |
| `LogicManager` | Ranks the jobs and gives each its match outcome, before and after |
| `IOManager`, `ConsoleApp`, `WebApp` | Ask for the input and show the result |

A roadmap tries each remaining skill, asks the chosen planner to score the result, and keeps the best. Because the simulator only
calls `value`, a new strategy is a new class handed to `CareerSimulator(planners=[...])` and the simulator is unchanged. Input it
cannot use (no jobs, an unknown planner, too many skills, a roadmap of the wrong length) raises a `ValueError` whose message is
written for the user.

## 5. Tests

`tests/test_roadmap.py` has 53 test cases, none of which needs an AI or the internet:

- **Ranking.** The simulator's ranking matches `LogicManager`'s on random jobs, and a skill lifts the jobs that ask for it.
- **Planners.** Each planner picks what it should, and a roadmap stops at the requested length or when nothing improves it.
- **Safety.** A simulation never changes the real profile or jobs, and bad input gives a message instead of a crash.
- **Extension.** A third planner written only in the test works in the unchanged simulator, and changing the ranking rules changes
  what the simulator reports.
