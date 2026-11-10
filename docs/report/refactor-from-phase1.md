# Refactor from Phase 1: the logic layer

> Outline only. Each bullet becomes a paragraph or a code sample in the full draft.

## 1. Where Phase 1 left the logic layer

- `src/logic_manager.py`: one module of about 30 free functions (537 lines), no classes
- Three jobs mixed in one file:
  - filtering jobs against the user's preferences (`filter_reason`, `count_removed`, `search_steps`)
  - ranking jobs and giving each an outcome (`requirement_gaps`, `match_outcome`, `select_top_jobs`)
  - checking the AI's tailored resumes against the original (`ground_tailored_resume`, `ground_docx_edits`, ...)
- TODO: one or two sentences on why this was fine for Phase 1 and what made it hard to change

## 2. Problems with the Phase 1 design

- **Hard-coded requirements.** `match_outcome` checks experience, education and level with three `if` blocks.
  A new requirement means editing the function (breaks Open/Closed)
- **Hard-coded ranking order.** `select_top_jobs` builds one fixed six-part tuple inside a nested `sort_key`;
  the order can't be changed or tested rule by rule
- **Duplicated measuring.** `requirement_gaps` and the skill share are worked out separately in `match_outcome`
  and in `sort_key`
- **One file, two responsibilities.** Ranking rules and the tailoring checks sit side by side
  (breaks Single Responsibility)
- TODO: an example of a change that was awkward in Phase 1

## 3. What we changed

### 3.1 `LogicManager` class (`src/logic/logic_manager.py`)
- One entry point, `filter_and_rank`, for both the console and web app
- `job_fit` measures a job once; every rule reads from that one dict
- Ranking rules are separate small methods (`rank_by_experience_gap`, `rank_by_education`, ...)
  passed in as a list; `default_rules()` keeps the Phase 1 order

### 3.2 `MatchRule` interface and three rules
- `MatchRule` (abstract base class) with one method, `shortfall(fit, job)`
- `ExperienceRule`, `EducationRule`, `SeniorityRule`: one class per Phase 1 `if` block
- `match_outcome` asks every rule the same question (polymorphism, no branching on rule type)

### 3.3 `TailoringGuard` (`src/logic/tailoring_guard.py`)
- The resume fact checks moved out into their own class
- Composition: `LogicManager` creates the guard and is the only thing that uses it;
  callers still call `LogicManager`, so the web app didn't change

## 4. OOP and SOLID principles shown

| Principle | Where |
|---|---|
| Abstraction / interface | `MatchRule` |
| Inheritance and polymorphism | the three rules; `match_outcome` loop |
| Encapsulation | private `__rules`, `__match_rules`, `__guard`; read-only properties |
| Composition | `LogicManager` owns `TailoringGuard` |
| Aggregation | match rules handed in through the constructor |
| Open/Closed | new rule = new class, `LogicManager` unchanged |
| Liskov Substitution | every rule returns `""` when nothing is short |
| Single Responsibility | ranking vs tailoring checks split |

- TODO: pick the two or three strongest to explain in detail

## 5. Before and after code samples

- Phase 1 `match_outcome` with its three `if` blocks → Part 2 loop over `self.match_rules`
- Phase 1 nested `sort_key` tuple → Part 2 `rules` list and `sort_key`
- (Samples are added in a later commit, once the code is merged)

## 6. How we checked nothing broke

- `tests/test_logic_manager.py`: the Phase 1 behaviour, now through the class
- `tests/test_match_rules.py`: each rule alone, plus a `RemoteOnlyRule` added from outside to prove Open/Closed
- `test_the_default_order_is_the_phase_1_order`: ranking still matches Phase 1
- TODO: number of tests passing and the CI run

## 7. Trade-offs and what we'd do next

- TODO: e.g. constants copied between layers (checked by a test) instead of shared
- TODO: what you would change with more time
