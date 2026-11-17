# Design and evidence

How Resume Job Matcher is built, and where each requirement of the INF1103 Part 2 brief is in the code. For using the
app, see `README.md` and `GETTING_STARTED.md`.

## Design

The design follows the team's D1 diagram (`docs/diagrams/UML Diagram.png`). There are four managers, and the IO manager
coordinates the other three:

```
web pages (WebApp) -> IOManager -> AIManager      (the AI)
                               -> LogicManager   (the business rules)
                               -> DataManager    (saved jobs and job listings)
```

| Manager | Its job | Its parts |
|---|---|---|
| `IOManager` | the only class the web pages call (a facade). It reads and checks the input, starts the work, and writes every message the pages show. It is the only file that prints | `InputValidator`, `SearchService`, `TailoringService`, `ResumeFiles`, `DocxEditor`, `TaskRegistry` (composition) |
| `AIManager` | builds each prompt, calls the provider, parses the reply and checks it against a JSON schema. Every AI call goes through `run_task` | an `AIProvider` (aggregation) |
| `LogicManager` | the business rules on the AI-enriched jobs: filters, ranking, match outcome, skill gaps, tailoring checks and the What-If simulator | `TailoringGuard`, `CareerSimulator` (composition); `MatchRule`s (aggregation) |
| `DataManager` | saves, loads, searches, updates and removes saved jobs; fetches job listings from the portal | a `Storage` and a `JobPortal` (aggregation) |

Rules the code keeps (`tests/test_architecture.py` checks the first three):
- `AIManager`, `LogicManager` and `DataManager` never import each other. Only IO-side classes use all three.
- `WebApp` (`src/web_app.py`) is a thin route layer. It holds only an `IOManager` and names the other managers only in
  `from_environment`, where the app is wired together. It is not drawn in the UML.
- Each layer has its own folder in `src/` (`ai_layer/`, `logic_layer/`, `data_layer/`, `io_layer/`), holding its manager
  and every class below it. `web_app.py` stays in `src/` itself, next to `templates/` and `static/`.
- One class per file, and the file is named after the class (`PostgresStorage` is in `src/data_layer/postgres_storage.py`).
  There are 32 classes in `src/`.
- Names show visibility, as in the lectures: `public`, `_protected` (a subclass uses it), `__private`.

All the classes:

| Class | Why it is a class |
|---|---|
| `IOManager`, `AIManager`, `LogicManager`, `DataManager` | the four managers the brief asks for |
| `SearchService` | the steps of one search: profile (AI), listings (data), requirements (AI), rank (logic), save (data) |
| `TailoringService` | the steps of tailoring one resume for one job |
| `ResumeTailor` (abstract) with `WordResumeTailor`, `PdfResumeTailor` | the same steps for every resume (a template method); each subclass handles one kind of file |
| `ResumeFiles` | lists, reads, saves and removes resume files |
| `DocxEditor` | reads and writes Word files |
| `TaskRegistry` | progress of the background searches and tailoring runs, safe across threads |
| `AIProvider` (interface) | what any AI service must offer: `provider_name`, `complete`, `discard_reply` |
| `GeminiProvider` | Google Gemini through its OpenAI-compatible endpoint: the request formats, the retries and a missing or rejected key |
| `CachingAIProvider` | optional (`AI_CACHE=on`): wraps any provider and replays saved replies |
| `InputValidator` | every rule for what the user may type or upload |
| `TailoringGuard` | checks that a tailored resume adds nothing the real resume does not show |
| `MatchRule` (interface) with `ExperienceRule`, `EducationRule`, `SeniorityRule` | one requirement each of the Phase 1 multi-condition rule |
| `CareerSimulator` | the new feature: What-If and roadmap |
| `RankingRules` (interface) | the two questions the simulator asks about a job (`match_outcome`, `sort_key`); `LogicManager` answers them, so the simulator never names the manager that owns it |
| `RoadmapPlanner` (interface) with `GreedyPlanner`, `SalaryWeightedPlanner` | ways to choose the next skill to learn |
| `SkillName` | when two skill names are one skill: "UAT", "UAT Testing" and "User Acceptance Testing", for matching and the What-If |
| `Storage` (abstract) with `PostgresStorage` | where saved jobs live; PostgreSQL is the advanced feature |
| `JobPortal` | talks to MyCareersFuture and keeps the last listings for when it is down |
| `DataStoreError` | the one error the data layer raises when the saved jobs cannot be reached |
| `WebApp` | the Flask routes (not part of the D1 design) |

**Relationships** (as in the Week 9 and 10 lectures). All three arrive in different ways, so they can be told apart in
the code:
- *Association*: two objects work together, and neither is part of the other. They are handed in and kept.
  `IOManager` gets the three managers; a `ResumeTailor` gets the Word editor.
- *Aggregation*: a whole and a part that lives on its own. The part is handed in, and tests swap it.
  `AIManager(provider)`, `DataManager(storage, portal)`, `LogicManager(match_rules=...)`, `CareerSimulator(planners)`.
- *Composition*: the owner builds the part in its own `__init__` and nobody else holds it.
  `IOManager` builds its services, `LogicManager` its validator, guard and simulator, `TailoringService` its tailors.
- *Dependency*: used for one call and never kept. `CareerSimulator` is given the `RankingRules` (the `LogicManager`)
  for each What-If.

### Refactor from Phase 1

Phase 1 (commit `488d0a6`, 14 Oct) was procedural, as that brief asked: 6 modules, 163 functions, no classes. It had
five problems that the refactor fixes:
- **Module-level state.** `ai_manager.py` kept the client and provider in globals, and `io_manager.py` kept the web
  app's tasks in a module dict with a module lock.
- **Branching on type.** `call_api` chose its request with `if sdk == "anthropic"`, so a new provider meant editing it.
- **Mixed jobs in one file.** `io_manager.py` (1,014 lines) did console input and output, MyCareersFuture HTTP calls,
  Word editing and background-task tracking.
- **Fixed choices.** The ranking order was one hard-coded `sort_key`, and storage was one JSON file.
- **The same pipeline twice.** `main.py` and `web_app.py` each wrote out search, analyse, rank and save.

| Phase 1 (module: functions) | Part 2 | What changed and why |
|---|---|---|
| `ai_manager.py`: globals `_client`, `PROVIDER`, `MODEL`; `set_provider`, `call_api` (if/else on the SDK), `_with_retries` | the interface `AIProvider` (`src/ai_layer/ai_provider.py:6`); `GeminiProvider` (`src/ai_layer/gemini_provider.py:46`) makes Gemini's requests and holds the retries | No globals and no if/else on the SDK. A provider is an object, a new one is a new class, and tests hand in a fake |
| `ai_manager.py`: `build_prompt`, `parse_response`, `validate_response`, `run_task` | `AIManager` (`src/ai_layer/ai_manager.py:267`) with `run_task` (`src/ai_layer/ai_manager.py:317`); parsing is the private `__parse` (`src/ai_layer/ai_manager.py:343`) | The same prompt, parse and schema contract, on an object that is given its provider |
| `io_manager.py`: `fetch_jobs`, `fetch_job_details`, `fetch_listings` | `JobPortal` (`src/data_layer/job_portal.py:45`), reached only through `fetch_listings` (`src/data_layer/data_manager.py:58`) | Job records come from the data layer, not from the user-facing layer |
| `io_manager.py`: `read_docx_paragraphs`, `write_edited_docx`, `word_preview` | `DocxEditor` (`src/io_layer/docx_editor.py:21`) | Word editing is its own job |
| `io_manager.py`: module dict `tasks` with `tasks_lock`; `start_task`, `update_task`, `cancel_task` | `TaskRegistry` (`src/io_layer/task_registry.py:9`) | Shared state behind a private dict and lock; callers get copies |
| `io_manager.py`: resume listing and reading | `ResumeFiles` (`src/io_layer/resume_files.py:18`) | File work is its own job |
| `io_manager.py`: `check_filters` and the other form checks | `InputValidator` (`src/io_layer/input_validator.py:32`), built and used by `IOManager` as `__validator` (`src/io_layer/io_manager.py:89`) | The input rules are their own class. The brief gives "collect and validate user input" to the IO manager, so the IO manager owns it; the choices it checks against (job types, education levels) are the logic layer's |
| `io_manager.py`: `display_*`, `prompt_*` (console) | the console app is gone; `IOManager` (`src/io_layer/io_manager.py:45`) is now the facade the pages call | The team chose one front end, the web app. IO still owns every message |
| `logic_manager.py`: filters, `match_outcome` with three `if` checks, `select_top_jobs` with one fixed `sort_key` | `LogicManager` (`src/logic_layer/logic_manager.py:87`); the checks became `ExperienceRule` (`src/logic_layer/experience_rule.py:6`), `EducationRule` (`src/logic_layer/education_rule.py:6`) and `SeniorityRule` (`src/logic_layer/seniority_rule.py:6`); the ranking order is a tuple of keys, `__ranking_keys` (`src/logic_layer/logic_manager.py:100`) | Same outcomes and order, but a new rule or key plugs in without editing the class |
| `logic_manager.py`: `ground_tailored_resume`, `ground_docx_edits`, `job_keywords` | `TailoringGuard` (`src/logic_layer/tailoring_guard.py:51`), a part of `LogicManager` | The tailoring checks are their own class |
| `data_manager.py`: `read_database`, `write_database` on `data/job_listings.json` | `Storage` (`src/data_layer/storage.py:7`) with `PostgresStorage` (`src/data_layer/postgres_storage.py:47`); `DataManager` (`src/data_layer/data_manager.py:33`) is handed a `Storage` | The database replaced the file without changing anything above `DataManager` |
| `main.py`: `find_jobs`, `save_top_jobs`; `web_app.py`: `find_jobs_for_search`, `rank_and_save` | `SearchService` (`src/io_layer/search_service.py:25`) | One copy of search, analyse, rank and save |
| `web_app.py`: `run_tailor`, `tailor_word_resume`, `tailor_pdf_resume` | `TailoringService` (`src/io_layer/tailoring_service.py:15`) with `WordResumeTailor` (`src/io_layer/word_resume_tailor.py:6`) and `PdfResumeTailor` (`src/io_layer/pdf_resume_tailor.py:6`) | No Word/PDF `if`; a new kind of resume is a new class |
| `web_app.py`: module functions and a module-level Flask `app` | `WebApp` (`src/web_app.py:39`), built in `from_environment` (`src/web_app.py:64`) | The app is an object built from its parts, so tests build one with fakes |

New in Part 2 rather than moved: `CareerSimulator` with `RoadmapPlanner` and two planners (the new feature),
`PostgresStorage` (the advanced feature), `CachingAIProvider`, the application tracker and `DataStoreError`. The
Phase 1 rule and ranking order are kept and tested
(`tests/test_logic_manager.py::test_the_default_order_is_the_phase_1_order`).

### Requirement evidence: OOP and SOLID

Where each concept is in the code, and the test that proves it. `tests/test_design_evidence.py` checks that every
`src/...py:line` below still points at the named code, and that every test named here exists. So these tables cannot
go out of date without a test failing.

**OOP concepts**

| Concept | Where | Proved by |
|---|---|---|
| Classes and objects | `from_environment` (`src/web_app.py:64`) builds one `AIManager`, one `LogicManager` and one `DataManager`, then one `IOManager` (`src/web_app.py:67`) from them | `tests/test_web_app.py::test_the_app_is_built_from_the_environment_and_starts_with_the_database_down`, `tests/test_io_manager.py::test_a_search_goes_through_all_four_managers_to_the_results` |
| Encapsulation | Private: `__tasks` (`src/io_layer/io_manager.py:92`), `__provider` (`src/ai_layer/ai_manager.py:276`), `__pool` (`src/data_layer/postgres_storage.py:56`), `__key_rejected` (`src/ai_layer/gemini_provider.py:54`). Protected, for subclasses: `_logic` (`src/io_layer/resume_tailor.py:21`). Read-only `@property`: `provider_name` (`src/ai_layer/ai_manager.py:306`), `size` (`src/ai_layer/caching_ai_provider.py:41`), `planners` (`src/logic_layer/logic_manager.py:190`). `get` (`src/io_layer/task_registry.py:60`) hands out a copy | `tests/test_task_registry.py::test_get_gives_a_copy_so_callers_cannot_change_the_task` |
| Abstract class | `ResumeTailor` (`src/io_layer/resume_tailor.py:12`) shares `tailor` (`src/io_layer/resume_tailor.py:24`). `Storage` (`src/data_layer/storage.py:7`) shares `insert_many` (`src/data_layer/storage.py:45`) and `search` (`src/data_layer/storage.py:50`) | `tests/test_resume_tailor.py::test_the_abstract_tailor_cannot_be_used_on_its_own` |
| Inheritance | `GeminiProvider` (`src/ai_layer/gemini_provider.py:46`) and `CachingAIProvider` (`src/ai_layer/caching_ai_provider.py:12`) implement `AIProvider`; `PostgresStorage` (`src/data_layer/postgres_storage.py:47`) extends `Storage`; `WordResumeTailor` (`src/io_layer/word_resume_tailor.py:6`) and `PdfResumeTailor` (`src/io_layer/pdf_resume_tailor.py:6`) extend `ResumeTailor` | `tests/test_caching_ai_provider.py::test_it_stands_in_for_any_provider`, `tests/test_resume_tailor.py::test_every_tailor_works_where_a_resume_tailor_is_expected` |
| Interface | ABCs with only abstract members and no shared code: `AIProvider` (`src/ai_layer/ai_provider.py:6`), `MatchRule` (`src/logic_layer/match_rule.py:11`) with `shortfall` (`src/logic_layer/match_rule.py:17`), `RoadmapPlanner` (`src/logic_layer/roadmap_planner.py:6`) with `value` (`src/logic_layer/roadmap_planner.py:22`), `RankingRules` (`src/logic_layer/ranking_rules.py:6`), which `LogicManager` (`src/logic_layer/logic_manager.py:87`) implements | `tests/test_ai_manager.py::test_the_interface_cannot_be_used_on_its_own_or_half_implemented`, `tests/test_roadmap.py::test_the_interface_cannot_be_used_on_its_own_or_half_implemented`, `tests/test_match_rules.py::test_the_interface_cannot_be_used_on_its_own`, `tests/test_roadmap.py::test_the_ranking_rules_interface_cannot_be_used_on_its_own_or_half_implemented` |
| Association | `IOManager` is handed the three managers in `__init__` (`src/io_layer/io_manager.py:76`) and keeps them, e.g. `__ai` (`src/io_layer/io_manager.py:80`); `ResumeTailor` is handed the Word editor in `__init__` (`src/io_layer/resume_tailor.py:17`) and keeps it as `_docx` (`src/io_layer/resume_tailor.py:22`) | `tests/test_io_manager.py::test_a_search_goes_through_all_four_managers_to_the_results` |
| Aggregation | The part is handed in and lives on its own: `AIManager` gets its provider in `__init__` (`src/ai_layer/ai_manager.py:273`); `CachingAIProvider` keeps the provider it wraps as `__inner` (`src/ai_layer/caching_ai_provider.py:25`); `DataManager` gets its storage and portal in `__init__` (`src/data_layer/data_manager.py:36`); `LogicManager` can be handed its rules in `__init__` (`src/logic_layer/logic_manager.py:91`); `CareerSimulator` gets its planners in `__init__` (`src/logic_layer/career_simulator.py:33`) | `tests/test_ai_manager.py::test_the_manager_gives_the_same_answer_with_a_fake_and_a_real_provider`, `tests/test_caching_ai_provider.py::test_a_cache_can_wrap_another_cache`, `tests/test_roadmap.py::test_a_new_planner_plugs_in_without_changing_the_simulator` |
| Composition | The owner builds the part: `IOManager` builds `InputValidator` (`src/io_layer/io_manager.py:89`), `ResumeFiles` (`src/io_layer/io_manager.py:90`), `DocxEditor` (`src/io_layer/io_manager.py:91`), `TaskRegistry` (`src/io_layer/io_manager.py:92`), `SearchService` (`src/io_layer/io_manager.py:93`) and `TailoringService` (`src/io_layer/io_manager.py:94`); `LogicManager` builds `TailoringGuard` (`src/logic_layer/logic_manager.py:104`) and `CareerSimulator` (`src/logic_layer/logic_manager.py:105`); `TailoringService` builds its tailors, `WordResumeTailor` (`src/io_layer/tailoring_service.py:28`) first | `tests/test_roadmap.py::test_logic_manager_runs_what_if_through_its_simulator`, `tests/test_io_manager.py::test_a_search_goes_through_all_four_managers_to_the_results` |
| Dependency | `what_if` (`src/logic_layer/career_simulator.py:46`) takes a `RankingRules` for one call and never keeps it; `LogicManager` passes itself, as the `RankingRules`, in `what_if` (`src/logic_layer/logic_manager.py:202`). `__stored` (`src/data_layer/data_manager.py:49`) raises `DataStoreError` (`src/data_layer/data_manager.py:54`) | `tests/test_roadmap.py::test_logic_manager_runs_what_if_through_its_simulator`, `tests/test_roadmap.py::test_the_simulator_never_names_the_logic_manager` |
| Polymorphism | One call, different behaviour, no type checks: `shortfall` (`src/logic_layer/logic_manager.py:306`) on every match rule; `value` (`src/logic_layer/career_simulator.py:109`) on the chosen planner; `handles` (`src/io_layer/tailoring_service.py:55`) and `tailor` (`src/io_layer/tailoring_service.py:48`) on the Word or PDF tailor; `complete` (`src/ai_layer/ai_manager.py:326`) on whichever provider was handed in | `tests/test_resume_tailor.py::test_the_service_picks_a_tailor_without_type_checks`, `tests/test_roadmap.py::test_planners_can_be_swapped_with_the_same_calls`, `tests/test_match_rules.py::test_the_outcome_lists_every_shortfall_in_rule_order` |

**SOLID**

| Principle | Where | Proved by |
|---|---|---|
| SRP: one reason to change per class | `InputValidator` (`src/io_layer/input_validator.py:32`) holds only the input rules; `ResumeFiles` (`src/io_layer/resume_files.py:18`) only file work; `TaskRegistry` (`src/io_layer/task_registry.py:9`) only progress; `DocxEditor` (`src/io_layer/docx_editor.py:21`) only Word files; `JobPortal` (`src/data_layer/job_portal.py:45`) only MyCareersFuture; `TailoringGuard` (`src/logic_layer/tailoring_guard.py:51`) only tailoring checks. Each manager keeps its own Phase 1 job: `run_task` (`src/ai_layer/ai_manager.py:317`) is prompt, provider, parse, schema; `DataManager` (`src/data_layer/data_manager.py:33`) is only records. Every `print()` is in `IOManager` | `tests/test_architecture.py::test_the_managers_never_import_each_other`, `tests/test_architecture.py::test_print_is_only_used_in_the_io_manager` |
| OCP: add behaviour without editing the class that uses it | A new requirement is a new `MatchRule`, and a new ranking key a new function, handed to `LogicManager`'s `__init__` (`src/logic_layer/logic_manager.py:91`). A new roadmap strategy is a new `RoadmapPlanner` handed to `CareerSimulator`'s `__init__` (`src/logic_layer/career_simulator.py:33`). Caching was added as `CachingAIProvider` (`src/ai_layer/caching_ai_provider.py:12`) with no change to any provider or to `run_task`. A new storage or AI service is a new subclass; only the factory lines change: `from_environment` (`src/data_layer/data_manager.py:43`) or `__PROVIDER_CLASSES` (`src/ai_layer/ai_manager.py:271`) | `tests/test_match_rules.py::test_a_new_rule_changes_the_outcome_without_changing_logic_manager` (a test-only `RemoteOnlyRule`), `tests/test_roadmap.py::test_a_new_planner_plugs_in_without_changing_the_simulator` (a test-only `FewestJobsFirstPlanner`), `tests/test_logic_manager.py::test_a_new_rule_can_be_plugged_in_without_touching_the_manager` (a new ranking key) |
| LSP: any subclass works where its parent is expected | Callers use only the parent's methods and never check which subclass they got: every `MatchRule`, `RoadmapPlanner`, `AIProvider`, `Storage` and `ResumeTailor` | `tests/test_match_rules.py::test_every_match_rule_works_where_a_match_rule_is_expected`, `tests/test_roadmap.py::test_every_planner_works_where_a_planner_is_expected`, `tests/test_ai_manager.py::test_every_provider_works_behind_the_ai_manager`, `tests/test_caching_ai_provider.py::test_it_stands_in_for_any_provider`, `tests/test_resume_tailor.py::test_every_tailor_works_where_a_resume_tailor_is_expected`, and one storage suite that runs on both an in-memory store and `PostgresStorage` (`tests/test_storage.py::kind`) |
| DIP (extra): depend on the abstraction | `AIManager` takes an `AIProvider` in `__init__` (`src/ai_layer/ai_manager.py:273`), not a concrete provider; `DataManager` takes a `Storage` in `__init__` (`src/data_layer/data_manager.py:36`), not `PostgresStorage`; `CareerSimulator` ranks through `RankingRules`, not `LogicManager`, so the manager owns the simulator and the simulator does not depend back on the manager | the data tests run on an in-memory store (`tests/test_data_manager.py::data`); the AI tests run on a fake provider (`tests/test_ai_manager.py::test_the_manager_gives_the_same_answer_with_a_fake_and_a_real_provider`); the simulator runs on test-only rules (`tests/test_roadmap.py::test_the_simulator_works_with_any_ranking_rules_not_only_the_logic_manager`) |

**Design patterns** (Week 11)

| Pattern | Where | Proved by |
|---|---|---|
| Strategy | the match rules and the roadmap planners: `MatchRule` (`src/logic_layer/match_rule.py:11`), `RoadmapPlanner` (`src/logic_layer/roadmap_planner.py:6`) | `tests/test_roadmap.py::test_planners_can_be_swapped_with_the_same_calls` |
| Template Method | `tailor` (`src/io_layer/resume_tailor.py:24`) runs the same steps for every resume: `_ask_ai` (`src/io_layer/resume_tailor.py:34`), progress, then `_check` (`src/io_layer/resume_tailor.py:38`), filled in by each subclass | `tests/test_resume_tailor.py::test_the_template_method_asks_then_reports_then_checks` |
| Decorator | `CachingAIProvider` (`src/ai_layer/caching_ai_provider.py:12`) is an `AIProvider` that wraps another one; on a cache miss it calls `complete` (`src/ai_layer/caching_ai_provider.py:60`) on the wrapped provider | `tests/test_caching_ai_provider.py::test_a_cache_can_wrap_another_cache` |
| Factory | `create_provider` (`src/ai_layer/ai_manager.py:289`) picks the provider class by name from a table, with no if/else chain | `tests/test_ai_manager.py::test_the_factory_picks_the_provider_class_by_name` |
| Facade | the pages call only `IOManager` (`src/io_layer/io_manager.py:45`): `WebApp` keeps just `__io` (`src/web_app.py:45`). `LogicManager` is a facade for its parts too, e.g. `learnable_skills` (`src/logic_layer/logic_manager.py:194`) | `tests/test_architecture.py::test_the_web_app_names_the_managers_only_in_from_environment` |

**Other Part 2 requirements**

| Requirement | Where | Proved by |
|---|---|---|
| Phase 1 multi-condition rule kept | `match_outcome` (`src/logic_layer/logic_manager.py:300`) uses years, education and level from the AI, then the share of key skills | `tests/test_logic_manager.py::test_match_outcome_combines_requirements_and_skills`, `tests/test_logic_manager.py::test_multi_condition_order_uses_experience_then_education_then_level_then_skills` |
| Every record through the AI, JSON checked | every AI call goes through `run_task` (`src/ai_layer/ai_manager.py:317`), which parses the reply and checks it with `__validate_schema` (`src/ai_layer/ai_manager.py:373`) | `tests/test_ai_manager.py::test_a_reply_with_the_wrong_schema_is_rejected_after_one_retry`, `tests/test_ai_manager.py::test_validator_rejects_every_missing_required_field` |
| AI failures do not crash the app | `__with_retries` (`src/ai_layer/gemini_provider.py:132`) retries busy servers, timeouts and connection errors, then gives `None` | `tests/test_ai_manager.py::test_connection_failure_is_retried_then_gives_none`, `tests/test_ai_manager.py::test_a_timeout_is_retried_then_gives_none` |
| Database problems do not crash the app | `PostgresStorage` turns any psycopg error into a `ConnectionError` (`src/data_layer/postgres_storage.py:73`); `DataManager` turns that into `DataStoreError`; `saved_jobs` (`src/io_layer/io_manager.py:130`) shows a message and `__save` (`src/io_layer/search_service.py:180`) still shows the results | `tests/test_data_manager.py::test_a_store_that_cannot_be_reached_is_reported_as_a_data_store_error`, `tests/test_io_manager.py::test_a_database_that_is_down_becomes_a_message_not_a_crash` |
| UML matches the code | `docs/diagrams/UML Diagram.png` and `docs/diagrams/Full App UML Diagram.png` | checked by hand against the code (no automated test) |

### Exception-handling matrix

How the app handles each failure the brief names, plus the job portal being down. No failure stops the app: each
becomes a message the page shows, and the work that can still be done is kept.

| Failure | Where it is caught | What the user sees | Proved by |
|---|---|---|---|
| API connection failure, timeout or busy server | `__with_retries` (`src/ai_layer/gemini_provider.py:132`) retries up to 3 times with a wait, then gives `None`; `run_task` (`src/ai_layer/ai_manager.py:317`) passes the `None` on. A missing or refused API key is not retried and is worded by `__problem_message` (`src/io_layer/io_manager.py:535`) | "The AI couldn't read your resume just now. Please try again in a minute." For a key: what to put in `.env` | `tests/test_ai_manager.py::test_connection_failure_is_retried_then_gives_none`, `tests/test_ai_manager.py::test_a_timeout_is_retried_then_gives_none`, `tests/test_io_manager.py::test_an_ai_that_cannot_read_the_resume_ends_the_search_with_a_message`, `tests/test_io_manager.py::test_a_missing_key_says_what_to_fix_instead_of_blaming_the_connection` |
| Malformed API response or invalid schema | `__parse` (`src/ai_layer/ai_manager.py:343`) rejects text that isn't a JSON object and `__validate_schema` (`src/ai_layer/ai_manager.py:373`) one that breaks the schema; `run_task` (`src/ai_layer/ai_manager.py:317`) asks once more, then gives `None`. A job the AI still can't read is left out and counted | "2 of 3 listings could not be analysed", or "We found 3 job listings but couldn't read them just now. Please wait a minute and try again." | `tests/test_ai_manager.py::test_a_malformed_reply_is_asked_for_again_once`, `tests/test_ai_manager.py::test_a_reply_with_the_wrong_schema_is_rejected_after_one_retry`, `tests/test_ai_manager.py::test_validator_rejects_every_missing_required_field`, `tests/test_io_manager.py::test_listings_the_ai_could_not_analyse_end_the_search_with_a_message`, `tests/test_web_app.py::test_a_partly_failed_search_says_what_it_left_out` |
| Corrupt or missing data file, or the database is down | Saved jobs: `__connect` (`src/data_layer/postgres_storage.py:65`) turns any database error into `ConnectionError`, which `__stored` (`src/data_layer/data_manager.py:49`) turns into `DataStoreError`; `saved_jobs` (`src/io_layer/io_manager.py:130`) shows a message, and `__save` (`src/io_layer/search_service.py:180`) still shows the results. Files: `__read_saved` (`src/data_layer/job_portal.py:102`) ignores a corrupt listings file, `__load` (`src/ai_layer/caching_ai_provider.py:72`) a corrupt AI cache, and `load` (`src/io_layer/resume_files.py:42`) gives empty text for a resume it can't read | "Could not open your saved jobs file. Check that the database is running, then reload this page. New searches still work." After a search: "Your results could not be saved, but they are shown below." | `tests/test_data_manager.py::test_a_store_that_cannot_be_reached_is_reported_as_a_data_store_error`, `tests/test_io_manager.py::test_a_database_that_is_down_becomes_a_message_not_a_crash`, `tests/test_io_manager.py::test_a_failed_save_still_gives_the_results_with_a_flag`, `tests/test_job_portal.py::test_a_corrupt_listings_file_is_ignored`, `tests/test_caching_ai_provider.py::test_a_missing_corrupt_or_unwritable_cache_never_stops_the_ai`, `tests/test_io_manager.py::test_a_saved_resume_that_cannot_be_read_is_a_message_not_a_crash` |
| Invalid user input | The IO manager's `InputValidator` checks every form before any work starts: `check_filters` (`src/io_layer/input_validator.py:35`), `check_upload` (`src/io_layer/input_validator.py:84`) and `check_resume` (`src/io_layer/input_validator.py:74`). `start_search` (`src/io_layer/io_manager.py:180`) raises `ValueError` with the problem, and the page's `start_search` (`src/web_app.py:258`) shows it | The problem in words beside the form, e.g. "Minimum salary must be a whole number of 0 or more, e.g. 3000." | `tests/test_input_validator.py::test_form_filters_get_a_message_instead_of_a_new_question`, `tests/test_input_validator.py::test_an_upload_must_be_a_known_type_not_empty_and_not_too_large`, `tests/test_io_manager.py::test_bad_input_is_a_value_error_with_the_message_and_no_task`, `tests/test_web_app.py::test_form_problems_are_messages` |
| Job portal down (MyCareersFuture) | `search` (`src/data_layer/job_portal.py:64`) tries 3 times, then uses the same search's last saved listings from `__last_listings` (`src/data_layer/job_portal.py:129`); with none, `__search_problem_message` (`src/io_layer/io_manager.py:624`) words it | "Could not get any job listings from MyCareersFuture. Check your internet connection, or try again in a few minutes." | `tests/test_job_portal.py::test_a_portal_that_keeps_failing_gives_none_after_three_tries`, `tests/test_job_portal.py::test_the_last_listings_of_a_search_are_used_when_the_portal_is_down`, `tests/test_io_manager.py::test_a_portal_that_is_down_and_one_that_lists_nothing_are_told_apart` |

## New feature: What-If simulator and roadmap

**The problem.** A search tells you which jobs fit now. It does not tell you which skill to learn to reach better jobs.

**How you reach it.** On the results page, click **What if I learned...?**. Tick skills the jobs ask for (or type
some), then either test them, or ask a planner for a roadmap: the best skills to learn, in order.

**How it works.** It runs on the AI-enriched jobs from the search. Their required and matched skills came from the AI,
so the AI's output drives every result, with no extra AI call. `CareerSimulator` adds the skills to the profile,
moves them from missing to matched on every job, and ranks again with the same `LogicManager` rules the search used.
It reports which jobs move up, by how many places, which change outcome (for example Fair to Strong), and which enter
or leave the top list. A roadmap picks one skill at a time: the one the chosen planner scores highest.
`GreedyPlanner` ("Rank higher") takes the biggest rank gain; `SalaryWeightedPlanner` ("Earn more") takes the skill
that raises the total pay of your top list the most (a total, so qualifying for one more paid job counts), then the
best-paid progress (better matches and places on the best-paid jobs), so a plan has as many skills as asked for.
When no single skill moves a planner's main goal, a step can be a pair of skills that only help together
(`CareerSimulator.__best_step`).

**Classes.** `IOManager.what_if` and `IOManager.roadmap` -> `LogicManager` (`learnable_skills`, `what_if`, `roadmap`)
-> `CareerSimulator` -> a `RoadmapPlanner`. The simulator ranks through `RankingRules`, which `LogicManager`
implements, so it uses the manager's rules without depending on the manager. The form is checked by the IO manager's
`InputValidator` (empty, too many, too long, bad numbers). `CareerSimulator` rejects a skill you already have or that
no job asks for. Each problem is a message, not an error page.

**Tests.** `tests/test_roadmap.py` (the simulator, both planners and the interface), the What-If tests in
`tests/test_input_validator.py`, `tests/test_io_manager.py` and `tests/test_web_app.py`.

## Advanced feature: Saved jobs in PostgreSQL

Every search saves its best jobs. They are kept in one PostgreSQL table, `jobs`, by `PostgresStorage`
(`src/data_layer/postgres_storage.py`):
- **CRUD**: `insert_many` adds jobs (a link already saved is skipped), `load_all` and `get` read them, `update` sets a
  status or note, `delete` and `clear` remove them.
- **Search in the database**: the keyword filter on the Saved page is one `SELECT` with `ILIKE`, one condition per word.
- **Counting in the database**: the application tracker (how many jobs are Saved, Applied, Interviewing, Offer,
  Rejected) is one `GROUP BY`; no job is loaded to count it.
- **Safe SQL**: every value is a `%s` parameter, never part of the SQL text. Each job is also kept whole as `JSONB`.
- **Connection pool**: `psycopg_pool` reuses connections between requests. Nothing connects at start-up, so the app
  starts even when the database is down.
- **Failures**: a database that is down or too slow (5 seconds) becomes `DataStoreError`. The Saved page then says it
  could not open the saved jobs, and searches still work.

In Docker the database is the `db` service (`postgres:17-alpine`). Its data is on the named volume `pg_data`, so it
survives removing the container.

Tests: `tests/test_postgres_storage.py` runs the real class and SQL on a fake database. `tests/test_storage.py` runs the
same storage tests on `PostgresStorage` and on an in-memory store.

## Diagrams

All in `docs/diagrams/`, as pictures (`.png`).

| File | Shows |
|---|---|
| `UML Diagram.png` | the team's D1 design, drawn from the finished code: constructors, every public method, private helpers and exact signatures |
| `Full App UML Diagram.png` | every class in `src/` except `WebApp`, with every member and relationship, grouped by layer |
| `Dataflow.png` | resume to results: web pages -> IOManager -> AI -> Logic -> Data -> PostgreSQL, plus What-If and tailoring |

How each failure is handled is in the exception-handling matrix above. No test checks the pictures, so update them
by hand when the classes change.

## Tests and CI

`python -m pytest` runs every test without a live AI, the internet, an API key or a database. Test doubles in
`tests/fakes.py` stand in: `FakeAIProvider`, `FakePortal` and `StubHttp`, `InMemoryStorage`, `FlakyStorage` (fails on
demand) and `FakePostgres` (a fake database that records each SQL statement). `tests/conftest.py` removes the API keys
and points `DATABASE_URL` at a port nothing listens on, so no test can reach a real service. Covered: the filters,
ranking keys and match outcome; AI parsing and schema checks (valid, missing fields, wrong types, malformed JSON,
empty); connection failures and timeouts; the database being down; bad input; the What-If simulator and planners;
tailoring; the web pages end to end; the layer rules; and the evidence tables above.

GitHub Actions (`.github/workflows/main.yml`) runs on every push and pull request to `main` and `part2`: install,
compile, run pytest with at least 90% coverage, import every manager and load `/` and `/saved` with no database. A
second job builds the Docker image and runs the tests inside it.

## Business rules

Kept from Phase 1, with one ranking key added (a Strong match before a Fair one).

**Filters** remove: a link that isn't an `https://` MyCareersFuture link; pay entirely outside the salary range (no pay
stated is kept); more years of experience than allowed; the wrong job type or work arrangement; no matching skills.

**Ranking** (a tuple of keys in `LogicManager`; a later key only breaks ties left by the ones before): fewer years
short, meets the education asked, not 4+ levels above the candidate, a Strong match before a Fair one, more matched
skills, higher share matched, closer level.

**Match outcome** (`LogicManager.match_outcome`, a multi-condition rule on AI fields, one `MatchRule` per
requirement): *Stretch* if short on experience, below the education asked, or 4+ levels above; otherwise *Strong
match* with at least 60% of the key skills, else *Fair match*. The pages show *Fair match* as "Partial match" and
*Stretch* as "Missing a requirement" (the stored names stay, so saved jobs are unchanged).

## Project structure

```
src/              one file per class; web_app.py starts the app; templates/ and static/ for the web pages
tests/            pytest tests; fakes.py holds the test doubles; conftest.py keeps tests away from real services
docs/diagrams/    UML and data-flow diagrams (PNG)
Dockerfile, docker-compose.yml (services web, db, tests), .github/workflows/main.yml, requirements.txt
```

## Running the tests

The tests need no internet, API key or database. With the virtual environment from `GETTING_STARTED.md` active, from
the project folder:

```
python -m pytest                                                          # every test
python -m pytest tests/test_roadmap.py                                    # one file
python -m pytest --cov=src --cov-report=term-missing --cov-fail-under=90  # with coverage (CI fails below 90%)
docker compose run --rm --build tests                                     # inside the Docker image instead
```

The run ends with a line such as `674 passed, 1 skipped`. The skipped test reads a sample PDF resume from `resumes/`,
which the repository does not include. Lines starting with `WARNING` or `ERROR` in the captured output are expected:
some tests fake problems on purpose (the AI failing, the portal failing, the database being down).

## Technical limits

- The automated tests never run the SQL on a real PostgreSQL server, only on a fake one.
- Saved jobs from Phase 1 (`data/job_listings.json`) are not copied into PostgreSQL; the table starts empty.
- The Saved page's keyword filter uses SQL `ILIKE`; letters outside plain English may match slightly differently.
- The UML pictures are checked against the code by hand; no test reads them.
