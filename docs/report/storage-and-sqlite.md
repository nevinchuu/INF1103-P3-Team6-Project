# Storage and SQLite: the data layer

> Outline only. Each bullet becomes a paragraph, a table or a code sample in the full draft.

## 1. Where Phase 1 left the data layer

- `src/data_manager.py`: one module of 13 free functions (171 lines), no classes
- Saved jobs lived in one JSON file whose path was fixed in the code (`DATABASE_PATH` = `data/job_listings.json`)
- Every operation read the whole file, changed the list in Python and wrote the whole file back
  (`read_database` → change → `write_database`)
- Already handled well in Phase 1: a missing file is created, a corrupt one is backed up
  (`_back_up_database`), saves go through a temporary file so a crash never leaves half a file
- TODO: one or two sentences on why this was fine for Phase 1

## 2. Problems with the Phase 1 design

- **One storage format, built in.** JSON was the only option; adding a database meant rewriting every function
  (breaks Open/Closed)
- **Callers tied to the file.** `main.py` and `web_app.py` called module functions that read and wrote the file
  directly, so nothing could be swapped in for a test (breaks Dependency Inversion)
- **Storage and rules mixed.** File handling (backups, temp files, retries) sat in the same functions as the job
  rules (duplicates, links, IDs) (breaks Single Responsibility)
- **Slow for more jobs.** Searching and counting loaded every saved job into Python each time
- TODO: an example of a change that was awkward in Phase 1 (e.g. adding a status or a note to a saved job)

## 3. What we changed

### 3.1 `Storage` abstract class (`src/persistence/storage.py`)
- Six abstract methods every backend must provide: `load_all`, `get`, `insert`, `update`, `delete`, `clear`
- Shared behaviour every backend inherits: `insert_many`, `search`, `count_by_status_and_outcome`,
  the link checks and the corrupt-file backup
- One rule for errors: a duplicate or missing job returns `False`; only a store that can't be reached raises
  `OSError`

### 3.2 `JsonStorage` (`src/persistence/json_storage.py`)
- The Phase 1 behaviour, moved into a class: one JSON file, temporary-file writes, backup of a corrupt or
  non-list file, retry when Windows has the file locked
- A lock so the background search threads can save at the same time

### 3.3 `SqliteStorage` (`src/persistence/sqlite_storage.py`): the advanced feature
- A `jobs` table with `job_url` as a `UNIQUE` key, so the database itself refuses duplicates
- Searchable columns (title, company, location, status, ...) plus the full job kept as JSON in `data`
- Parameterised queries only, so a keyword or link can never change the SQL
- Overrides `search` and `count_by_status_and_outcome` to let SQL do the work instead of Python
- A locked database raises `OSError` and is left alone; a corrupt one is backed up and a new one started
- TODO: the SQL queries themselves (added in a later commit)

### 3.4 `DataManager` class (`src/persistence/data_manager.py`)
- The only class the rest of the program talks to: save, read, search, track, update, remove
- Takes any `Storage` in its constructor; `from_environment()` picks JSON (default) or SQLite from
  `STORAGE_BACKEND`, and the file from `JOBS_DB_PATH`
- New in Part 2: a status and a note on each saved job (`update_saved_job`), and the application tracker
  (`tracker_summary`)

## 4. OOP and SOLID principles shown

| Principle | Where |
|---|---|
| Abstraction | `Storage` and its six abstract methods |
| Inheritance and polymorphism | `JsonStorage` and `SqliteStorage`; `DataManager` calls the same methods on either |
| Encapsulation | private `__lock`, `__read`, `__write`, `__session`; read-only `path` and `storage` properties |
| Aggregation | `DataManager` is handed a storage it does not own |
| Open/Closed | a new backend = a new `Storage` subclass, `DataManager` unchanged |
| Liskov Substitution | one test suite passes on both backends |
| Dependency Inversion | `DataManager` depends on `Storage`, not on a file or a database |
| Single Responsibility | storage details in the backends, job rules in `DataManager` |

- TODO: pick the two or three strongest to explain in detail

## 5. JSON or SQLite: when to use which

- JSON: the default, no set-up, easy to open and read by hand
- SQLite: duplicates refused by the database, searching and counting done in SQL, safer with several threads
- TODO: a short comparison table (set-up, speed, safety, readability)

## 6. How we checked it works

- `tests/test_storage.py`: 31 tests, each run on both JSON and SQLite, covering create, read, update,
  delete, duplicates, odd search words (SQL-like text, `%`, unicode), 20 threads saving at once, corrupt files
  and a locked database
- TODO: `tests/test_data_manager.py` (added in a later commit)
- TODO: number of tests passing and the CI run

## 7. Screens

- TODO: the saved jobs page and the application tracker (added in a later commit)

## 8. Trade-offs and what we'd do next

- TODO: e.g. JSON still rewrites the whole file on every save
- TODO: what you would change with more time
