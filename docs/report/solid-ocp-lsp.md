# SOLID: Open/Closed and Liskov Substitution

Five places in the design follow the same pattern: an abstract class or interface, two or more classes that
implement it, and a caller that only knows the abstraction. That pattern is what gives us both principles. A new
behaviour is a new class (Open/Closed), and any of those classes can stand in for another without the caller
checking which one it has (Liskov Substitution).

## 1. Open/Closed: add behaviour without editing the code that uses it

| To add | Write | Hand it to | Unchanged |
|---|---|---|---|
| another AI service | a subclass of `BaseAIProvider` (`src/ai/base_ai_provider.py`) | `AIManager(provider)` | `AIManager`, `run_task`, every caller |
| another storage backend | a subclass of `Storage` (`src/persistence/storage.py`) | `DataManager(storage)` | `DataManager` and everything above it |
| another roadmap strategy | a class implementing `RoadmapPlanner` | `CareerSimulator(planners=[...])` | `CareerSimulator` |
| another requirement in the match outcome | a class implementing `MatchRule` (`src/logic/match_rule.py`) | `LogicManager(match_rules=[...])` | `LogicManager.match_outcome` |
| another kind of resume file | a subclass of `ResumeTailor` (`src/tailoring/resume_tailor.py`) | the tailors in `TailoringService` | the tailoring steps in `ResumeTailor.tailor` |

**Phase 1 broke this.** `call_api` chose its request with `if sdk == "anthropic"`, storage was hard-wired to one
JSON file, `match_outcome` held three fixed `if` blocks, and the web app chose Word or PDF tailoring with an `if`
on the file type. Each new option meant editing a working function.

**The one place that still changes.** Something has to name the concrete class. We keep that to two small
factories: `AIManager._PROVIDER_CLASSES` maps `AI_PROVIDER` to a provider class, and
`DataManager.from_environment` picks `JsonStorage` or `SqliteStorage` from `STORAGE_BACKEND`. These are the only
lines that know the concrete classes, so the change stays in one place, as in the Week 11 Factory pattern.

**Evidence in the tests.**
- `tests/test_roadmap.py::test_a_new_planner_plugs_in_without_changing_the_simulator`: a third planner, written
  only in the test, works in the unchanged `CareerSimulator`.
- `tests/test_match_rules.py::test_a_new_rule_changes_the_outcome_without_changing_logic_manager`.
- `tests/test_data_manager.py::test_the_sqlite_backend_is_chosen_by_the_environment`.

## 2. Liskov Substitution: any subclass works where its parent is expected

Each caller uses only the parent's methods and never checks which subclass it was given:

- **Providers.** `AIManager` calls `complete` on whichever `BaseAIProvider` it holds. `OpenAICompatibleProvider`
  (Gemini, Qwen, OpenAI) and `AnthropicProvider` (Claude) take the same arguments and return the reply text or
  `None`, so `run_task`'s parse and schema check work the same for both.
- **Storage.** `JsonStorage` and `SqliteStorage` keep the same contract, including the error rule: a duplicate or
  missing job returns `False`, and only a store that can't be reached raises `OSError`. The same storage and
  data tests run against both backends (`tests/test_storage.py`, `tests/test_data_manager.py`).
- **Planners.** `GreedyPlanner` and `SalaryWeightedPlanner` both return a tuple from `value(report)`, and the
  roadmap keeps the next skill with the highest value, whichever planner scored it
  (`tests/test_roadmap.py::test_planners_can_be_swapped_with_the_same_calls`).
- **Match rules.** `ExperienceRule`, `EducationRule` and `SeniorityRule` each return a shortfall message or an
  empty string from `shortfall(fit, job)`, and `LogicManager` collects them the same way
  (`tests/test_match_rules.py::test_every_rule_can_stand_in_for_the_interface`).
- **Resume tailors.** `TailoringService` picks the first tailor whose `handles(path)` is true and calls the same
  `tailor` template method. `WordResumeTailor` and `PdfResumeTailor` fill in `_ask_ai` and `_check`, and both
  return the same shape of result (`tests/test_resume_tailor.py::test_both_tailors_give_the_service_the_same_shape_of_result`).

**What we avoided.** A subclass that raised `NotImplementedError` for a parent method, needed an extra argument,
or returned a different type would break LSP, because callers would have to check its type. The tests above run
the same calls on every subclass, so a subclass that changed the contract would fail them.

## 3. Provider swap check

TODO (Sat 14 Nov): run the Docker app with two AI providers by changing only `.env`, and record that no code
changed.
