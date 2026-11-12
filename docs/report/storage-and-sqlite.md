# Storage and SQLite: the data layer

## 1. Where Phase 1 left the data layer

In Phase 1 the data layer was one module, `src/data_manager.py`: 13 free functions in 171 lines, with no classes.
Saved jobs lived in a single JSON file whose path was fixed in the code (`DATABASE_PATH`, pointing at
`data/job_listings.json`). Every operation worked the same way: `read_database` loaded the whole file into a list,
the function changed the list in Python, and `write_database` wrote the whole list back.

Some of it was already careful. A missing file was created, a corrupt one was kept as a dated backup
(`_back_up_database`) instead of crashing the program, and every save went to a temporary file first and was then
swapped in, so a crash halfway through a save never left half a file behind.

For Phase 1 this was enough. There was one way to store jobs, a saved job only held what the search had found, and
the console and web app only needed to save, list, search and remove them. The cost showed up in Part 2, when we
wanted a second way to store jobs and more to store on each one.

## 2. Problems with the Phase 1 design

**One storage format, built in.** JSON was the only option, and every function read and wrote the file itself.
Adding a database would have meant rewriting every function rather than adding something new beside them
(Open/Closed Principle).

**Callers tied to the file.** `main.py` and `web_app.py` called the module's functions directly
(`data_manager.read_saved_jobs()`, `data_manager.save_new_jobs(...)`), and those functions always used the real
file. There was nothing to hand in instead, so the apps could not be tested without touching the real saved jobs
(Dependency Inversion Principle).

**Storage and job rules mixed.** How the file is kept safe (backups, temporary files, retries) sat in the same
functions as the rules about jobs (no duplicate links, a job needs a link, IDs are positions). The two change for
different reasons, but a change to either meant editing the same code (Single Responsibility Principle).

**Everything done in Python.** Searching and counting loaded every saved job into memory each time. That is fine
for a few jobs, but it is work a database does better.

An example of a change that was awkward: Part 2 lets the user set a status ("Applied", "Interviewing", ...) and a
note on each saved job. In Phase 1 there was no "update one job" operation at all, so this would have meant a new
read-change-write function, with its own checks, inside the same file-handling module.

## 3. What we changed

The data layer is now the `src/persistence/` package: one abstract class, two backends and one manager.

### 3.1 The `Storage` abstract class (`src/persistence/storage.py`)

`Storage` describes a store of saved jobs, each found by its unique `job_url`. It has six abstract methods that
every backend must write its own way: `load_all`, `get`, `insert`, `update`, `delete` and `clear`.

It also holds the behaviour every backend shares, so none of them repeats it: `insert_many`, `search`,
`count_by_status_and_outcome`, the link checks (`has_link`, `_check_new_job`, `_check_changes`) and the dated
backup of a broken file. A backend that can do one of these faster overrides it.

There is one rule for errors. A duplicate or missing job is a normal answer, so `insert`, `update` and `delete`
return `False`. Only a store that cannot be reached at all raises `OSError`, which the apps already handle.

### 3.2 `JsonStorage` (`src/persistence/json_storage.py`)

`JsonStorage` is the Phase 1 behaviour moved into a class. It keeps every Phase 1 safeguard: temporary-file saves,
the dated backup of a corrupt file or of valid JSON that isn't a list (for example, edited by hand), and up to five
retries when Windows reports the file as locked for a moment (an antivirus or OneDrive scan).

What is new is a lock (`threading.RLock`). The web app's background searches can save at the same time, and the
lock makes sure their read-change-write steps never interleave and lose a job.

### 3.3 `SqliteStorage` (`src/persistence/sqlite_storage.py`): the advanced feature

`SqliteStorage` keeps saved jobs in a SQLite database with one `jobs` table. `job_url` is a `UNIQUE` key, so the
database itself refuses a duplicate: `insert_many` uses `INSERT OR IGNORE` and counts how many rows were really
added. The columns people search and filter on (title, company, location, resume, note, status) have their own
columns, and the full job is kept as JSON in `data`, so nothing the search found is lost.

Every query is parameterised. Search words and links are passed as `?` values, never pasted into the SQL text, so a
keyword like `'; DROP TABLE jobs; --` is just text to look for.

It overrides two of the shared methods so the database does the work: `search` builds one `instr(...) > 0`
condition per word, and `count_by_status_and_outcome` counts with one `GROUP BY` instead of loading every job into
Python. Each operation opens its own connection behind a lock, commits on success and rolls back on an error. A
database locked by another program raises `OSError` and is left alone; one that is damaged is backed up and a new
one is started.

- TODO: the SQL queries themselves (added in a later commit)

### 3.4 The `DataManager` class (`src/persistence/data_manager.py`)

`DataManager` is the only class the rest of the program talks to. It saves new jobs (noting which resume found
them), reads them, searches them, finds one by its link, updates a status or note, removes them, and summarises
them for the application tracker.

It is handed a `Storage` in its constructor and never creates one itself. `DataManager.from_environment()` picks
the backend: JSON by default, or SQLite when `STORAGE_BACKEND=sqlite`, with the file taken from `JOBS_DB_PATH` or
the default under `data/`. Tests hand in a temporary store instead.

Two features are new in Part 2. `update_saved_job` sets a status (one of `STATUSES`: Saved, Applied,
Interviewing, Offer, Rejected) and a note of up to 500 characters, and refuses anything else with `ValueError`.
`tracker_summary` counts how many saved jobs are at each stage, and how many strong matches have not been applied
to yet. On SQLite that count is done in SQL.

`tidy` also makes every job safe to show: a saved job from an older version or a hand-edited file may hold the
wrong type in a field, so list fields that aren't lists become empty and an unknown status counts as "Saved".

- TODO: how the console and web apps create one `DataManager` (once those PRs are merged)

## 4. OOP and SOLID principles shown

| Principle | Where |
|---|---|
| Abstraction | `Storage` and its six abstract methods |
| Inheritance and polymorphism | `JsonStorage` and `SqliteStorage`; `DataManager` calls the same methods on either |
| Encapsulation | private `__lock`, `__read`, `__write`, `__session`; read-only `path` and `storage` properties |
| Aggregation | `DataManager` is handed a storage it does not own |
| Open/Closed | a new backend is a new `Storage` subclass; `DataManager` does not change |
| Liskov Substitution | one test suite passes on both backends |
| Dependency Inversion | `DataManager` depends on `Storage`, not on a file or a database |
| Single Responsibility | storage details in the backends, job rules in `DataManager` |

The three that matter most here:

**Liskov Substitution.** `tests/test_storage.py` runs every test against both `JsonStorage` and `SqliteStorage`
with a pytest fixture, and `tests/test_data_manager.py` runs `DataManager` on both. The same calls give the same
results, so either backend can stand in for the other without `DataManager` noticing.

**Dependency Inversion.** `DataManager` only knows the `Storage` interface. Which backend it uses is decided once,
in `from_environment`, and a test can hand it a `FlakyStorage` (from `tests/fakes.py`) that fails on purpose to
check the error handling.

**Open/Closed.** SQLite was added as a new class beside JSON. Nothing in `DataManager` changed to support it, and
a third backend would be added the same way.

## 5. JSON or SQLite: when to use which

| | JSON (`JsonStorage`) | SQLite (`SqliteStorage`) |
|---|---|---|
| Set-up | none, the default | `STORAGE_BACKEND=sqlite` |
| Read it by hand | yes, any text editor | needs a SQLite tool |
| Duplicates refused by | our code | the database (`UNIQUE` key) |
| Search and counting | in Python, after loading every job | in SQL |
| Each save | rewrites the whole file | changes only the affected rows |
| Several threads saving | one lock around read-change-write | a lock, plus SQLite's own transactions |

JSON stays the default because it needs no set-up and is easy to check by hand. SQLite is the better choice when
there are many saved jobs or the web app's background searches save often.

## 6. How we checked it works

**`tests/test_storage.py`: 31 tests, each run on both backends.** They cover create, read, update and delete;
duplicate links; jobs without a usable link; IDs renumbered after a delete; saved jobs still there after a restart;
odd search words (SQL-like text, `%`, `_`, quotes, unicode); 20 threads saving at once without losing a job; a
corrupt file backed up, not crashed; a store that cannot be opened; and, for SQLite only, the `UNIQUE` key, a
database deleted while running, a locked database and 300 jobs saved and searched.

**`tests/test_data_manager.py`: 19 tests, run on both backends where it makes sense.** They cover saving only new
links and noting the resume, not changing the caller's records, finding and searching, removing and clearing,
setting a status and note (and refusing bad ones), an unreachable store raising `OSError`, choosing the backend
from `STORAGE_BACKEND`, tidying badly typed fields, and the tracker, including a check that the SQL count and the
shared Python count agree.

Every test uses a temporary folder, so the real `data/` folder is never touched.

- TODO: number of tests passing and the CI run

## 7. Screens

- TODO: the saved jobs page and the application tracker (added in a later commit)

## 8. Trade-offs and what we'd do next

**JSON rewrites the whole file on every save.** That keeps it simple and safe, but it gets slower as the list
grows. SQLite does not have this problem, which is one reason to offer it.

**Some SQLite reads still load every job.** `SqliteStorage.get` finds a job by looking through `load_all`, and
`search` runs a second query to work out each job's position. Both could be single queries, at the cost of
duplicating some of the ID logic.

**The full job is stored twice on SQLite.** The searchable columns are copied out of the JSON in `data`. That makes
searching simple, but each update has to write both.

- TODO: what you would change with more time
