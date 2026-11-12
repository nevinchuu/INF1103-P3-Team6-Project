# UML and OOP mapping

The class diagrams are in `docs/diagrams/`: `uml_0_full.mmd` shows every class, and five smaller ones each zoom in on one
part (`uml_1_overview`, `uml_2_ai_layer`, `uml_3_logic_and_whatif`, `uml_4_data_layer`, `uml_5_io_and_apps`). This section
explains how to read the arrows, then maps each OOP idea to the classes that use it.

## 1. Reading the relationships

| Arrow | Meaning | Example in our code |
|---|---|---|
| Inheritance `--|>` | A subclass extends a base class and reuses its code | `OpenAICompatibleProvider`, `AnthropicProvider` and `CachingAIProvider` extend `BaseAIProvider` |
| Realisation `..|>` | A class implements an interface that holds no code | `GreedyPlanner` and `SalaryWeightedPlanner` implement `RoadmapPlanner`; `ExperienceRule`, `EducationRule` and `SeniorityRule` implement `MatchRule` |
| Composition `*--` | The whole creates its part and owns it; the part does not outlive it | `WebApp` creates its `TaskRegistry`; `LogicManager` creates its `TailoringGuard`; `TailoringService` creates its `DocxEditor` |
| Aggregation `o--` | The part is handed in, exists on its own, and can be swapped or shared | `AIManager` is given a `BaseAIProvider`; `DataManager` a `Storage`; `CareerSimulator` its `RoadmapPlanner`s; `SearchService` its `JobPortal` |
| Association `-->` | One object uses another only during a call and does not keep it | `CareerSimulator` is given a `LogicManager` in each call to `what_if` and `roadmap` |
| Dependency `..>` | One class creates another when it needs it | `AIManager.create_provider` creates an `AnthropicProvider` or `OpenAICompatibleProvider` |

The rule we followed: if a class is something the caller might want to replace or share (an AI service, a storage
backend, a planner), it is **handed in** (aggregation). If it only makes sense inside its owner (the progress registry
of one web app), the owner **creates** it (composition).

## 2. OOP concepts and where they appear

| Concept | Where | How |
|---|---|---|
| **Encapsulation** | `CareerSimulator`, `LogicManager`, `CachingAIProvider`, `TaskRegistry` | State is private (`__planners`, `__rules`, `__replies`, `__lock`) and only reached through methods or read-only properties (`planners`, `rules`, `size`). `TaskRegistry` and `CachingAIProvider` keep a lock inside, so the threads that call them never handle it |
| **Abstraction** | `BaseAIProvider`, `Storage`, `ResumeTailor`, `MatchRule`, `RoadmapPlanner` | Each is an abstract class that says *what* a subclass must do and not *how*. `AIManager`, `DataManager` and the others are written against these, so they never name a concrete class |
| **Inheritance** | `BaseAIProvider`, `Storage`, `ResumeTailor` | A base class holds code that every subclass shares. For example `BaseAIProvider.complete` retries temporary failures (a busy server, a rate limit, a timeout), so every provider gets the same retry rules and only writes how it builds a request |
| **Polymorphism** | `AIManager`, `DataManager`, `LogicManager`, `CareerSimulator` | The same call runs different code depending on the object. `AIManager` calls `complete` on whichever provider it holds, and `CareerSimulator.roadmap` calls `planner.value(...)` on whichever planner was chosen, with no type check |

`MatchRule` and `RoadmapPlanner` are *interfaces*: they share no code, and each rule or planner decides for itself.
That is why the diagram uses `..|>` for them and `--|>` for the three base classes above.

## 3. Two design choices worth pointing out

**`CachingAIProvider` is both a provider and has one.** It extends `BaseAIProvider` and is also handed another provider to
wrap, so `AIManager` cannot tell it from a real provider. A cached reply is replayed and a new prompt goes to the wrapped
one. Adding the cache needed no change to `AIManager`.

**Both apps are handed their collaborators.** `ConsoleApp` and `WebApp` receive their `IOManager`, `AIManager`,
`LogicManager`, `DataManager` and `SearchService` through the constructor (aggregation). Only `from_environment` builds the
standard set. That is why the tests can hand the same classes a fake AI, fake storage and a fake portal
(`tests/fakes.py`). What each app owns alone, such as its `TailoringService`, it creates itself (composition).

How these classes keep the code open for extension and substitutable is covered in the SOLID section.
