# Refactor from Phase 1: the logic layer

## 1. Where Phase 1 left the logic layer

In Phase 1 the logic layer was one module, `src/logic_manager.py`: about 30 free functions in 537 lines, with no
classes. It did three different jobs:

- **filtering** jobs against the user's preferences (`filter_reason`, `count_removed`, `search_steps`)
- **ranking** jobs and giving each one an outcome (`requirement_gaps`, `match_outcome`, `select_top_jobs`)
- **checking** the AI's tailored resumes against the original, so the AI could not add anything the candidate
  never wrote (`ground_tailored_resume`, `ground_docx_edits` and their helpers)

For Phase 1 this was enough. The rules were fixed, one person wrote them, and the console and web app only needed
to call a handful of functions. The cost showed up when we wanted to change a rule: every rule lived inside a
larger function, so changing one meant reading and editing code that also did other things, and there was no way
to test one rule on its own.

## 2. Problems with the Phase 1 design

**Hard-coded requirements.** `match_outcome` decided whether a job was a *Stretch* with three `if` blocks, one each
for experience, education and seniority. Adding a fourth requirement, such as "onsite jobs only", meant editing
`match_outcome` itself. The function was not closed for modification (Open/Closed Principle).

**Hard-coded ranking order.** `select_top_jobs` sorted jobs with a nested `sort_key` function that built one fixed
six-part tuple: experience gap, education, far above level, matched skills, skill share, level gap. The order
could not be changed without editing the function, and no single part of it could be tested separately.

**The same measurement made twice.** Both `match_outcome` and `sort_key` called `requirement_gaps` and worked out
the skill share themselves. If one was changed and the other was not, a job could be ranked by one rule and
labelled by another.

**One file, two responsibilities.** The ranking rules and the tailored-resume checks sat side by side in the same
module, although they change for different reasons: the first when the matching rules change, the second when
the AI's output format changes (Single Responsibility Principle).

## 3. What we changed

### 3.1 The `LogicManager` class (`src/logic/logic_manager.py`)

`LogicManager` is now the one entry point to the domain rules for both apps. `filter_and_rank` filters, ranks and
labels the jobs in a single call.

The measuring is done once. `job_fit(profile, job)` compares one job with the candidate and returns a dictionary
with the experience gap, whether the education is met, the level gap, and the matched and required skill counts.
Every ranking rule and every match rule reads from that one dictionary, so they can no longer disagree.

The ranking order is now a list of small rules, one method per part of the old tuple
(`rank_by_experience_gap`, `rank_by_education`, `rank_by_far_above_level`, `rank_by_skill_count`,
`rank_by_skill_share`, `rank_by_level_gap`). Each takes a job's fit and returns a sort value. `default_rules()`
returns them in the Phase 1 order, and a different list can be passed to the constructor to add or reorder rules.
`sort_key` simply asks each rule in turn.

### 3.2 The `MatchRule` interface and its three rules

`MatchRule` (`src/logic/match_rule.py`) is an abstract base class with one method, `shortfall(fit, job)`. It
returns how the candidate falls short of the job, such as "needs 2 more years of experience", or an empty string
if they don't. Each of the three Phase 1 `if` blocks became its own class: `ExperienceRule`, `EducationRule` and
`SeniorityRule`.

`match_outcome` now loops over its rules and asks each the same question. It never checks which rule it is
talking to. A new requirement is a new `MatchRule` subclass passed in to the constructor, with no change to
`LogicManager`.

### 3.3 `TailoringGuard` (`src/logic/tailoring_guard.py`)

The resume fact checks moved into their own class, `TailoringGuard`. `LogicManager` creates its own guard in its
constructor and keeps it private (`__guard`). Callers use `LogicManager`'s methods, such as `ground_docx_edits`,
which pass straight through to the guard. Nothing outside `LogicManager` reaches into the guard, so the guard can
change without touching its callers.

## 4. OOP and SOLID principles shown

| Principle | Where |
|---|---|
| Abstraction / interface | `MatchRule` and its abstract `shortfall` |
| Inheritance and polymorphism | the three rule classes; the loop in `match_outcome` |
| Encapsulation | private `__rules`, `__match_rules`, `__guard` and `SeniorityRule.__far_above_levels`, read through properties |
| Composition | `LogicManager` creates and owns its `TailoringGuard` |
| Aggregation | match rules are handed in through the constructor and can be shared |
| Open/Closed | a new rule is a new class or function; `LogicManager` stays unchanged |
| Liskov Substitution | any rule can stand in for `MatchRule`: it returns `""` when nothing is short |
| Single Responsibility | ranking and outcomes in `LogicManager`; resume checks in `TailoringGuard` |

The three that matter most here:

**Polymorphism with Open/Closed.** This is the main reason for the refactor. `match_outcome` and `sort_key` no
longer list their rules, they loop over them. Our test adds a `RemoteOnlyRule` from outside the logic package and
the outcome changes from *Strong match* to *Stretch* without a line of `LogicManager` being edited.

**Composition versus aggregation.** We used both on purpose. The guard is a part of `LogicManager`: it is created
with it, has no other owner, and stays private (composition). The match rules are handed in and hold no state of
the manager, so the same rule objects can be shared between managers, or swapped in a test (aggregation).

**Single Responsibility.** Splitting out `TailoringGuard` cut `LogicManager` to the rules about jobs. Each class
now changes for one reason only.

## 5. Before and after code samples

*Code samples are added in the next commit.*

## 6. How we checked nothing broke

The Phase 1 behaviour had to stay the same, so the tests were written against what Phase 1 did, now called through
the class. `tests/test_logic_manager.py` and `tests/test_match_rules.py` hold 99 tests between them, including:

- `test_the_default_order_is_the_phase_1_order`: the default ranking rules are still the Phase 1 tuple, in order
- `test_multi_condition_order_uses_experience_then_education_then_level_then_skills`: the combined rule ranks a set
  of jobs exactly as Phase 1 did
- `test_each_rule_sorts_the_better_fit_first`: each ranking rule on its own, which Phase 1 could not test
- `test_the_interface_cannot_be_used_on_its_own`: `MatchRule` is abstract
- `test_every_rule_can_stand_in_for_the_interface`: the Liskov check, every rule returns `""` for a job the
  candidate fully meets
- `test_a_new_rule_changes_the_outcome_without_changing_logic_manager`: the Open/Closed check with `RemoteOnlyRule`

The whole suite (322 tests) passes. CI (`.github/workflows/main.yml`) runs it on Python 3.11 for every pull
request into `part2`, after checking that every file compiles.

## 7. Trade-offs and what we'd do next

**Copied constants.** The logic layer keeps its own copies of a few values that other layers also define, such
as the education levels and the salary limit, so that it never imports another layer. The cost is that the
copies could drift apart. `test_copied_values_still_match_the_other_layers` catches that, but a shared constants
module would remove the copies altogether.

**Pass-through methods.** Keeping `TailoringGuard` private means `LogicManager` has ten methods that only pass
calls on to it. That is extra code, but it keeps one contract for both apps.

**The fit is a dictionary.** `job_fit` returns a plain `dict`, so a typo in a key fails only when a rule runs.
A small `JobFit` data class would let the editor catch it.

**Ranking rules are functions, match rules are classes.** A ranking rule needs nothing but the fit, so a function
was enough. A match rule can need settings (`SeniorityRule` takes its distance), so it is a class. With more
time we would make the two kinds of rule consistent.
