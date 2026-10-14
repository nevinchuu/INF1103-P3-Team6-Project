# Resume Job Matcher

INF1103 team project (P3 Team 6). The app reads a job seeker's resume with AI, searches
[MyCareersFuture](https://www.mycareersfuture.gov.sg) for matching jobs, then filters, ranks and
saves the best ones. In the web app it can also tailor the resume to one job. It runs as a console
app and as a web app, locally or in Docker.

## The problem

Job seekers in Singapore, especially fresh graduates and career switchers, face thousands of
listings on MyCareersFuture. To find the ones that fit, they have to read each posting, compare its
requirements with their own resume, and send the same generic resume everywhere.

This app does the reading and comparing for them:

- **Target users:** fresh graduates, students looking for internships, and anyone changing jobs in Singapore.
- **Input:** their resume (PDF or Word) and their preferences: salary range, years of experience, job type and work arrangement.
- **Output:** the jobs that fit best, with:
  - which of their skills each job wants and which are missing
  - an outcome for each job: Strong match, Fair match or Stretch
  - the skills that are worth building
  - a tailored resume for any job they pick (web app)

**Why it needs AI:** a resume and a job posting are free text. Working out that "PostgreSQL" covers a
posting's "SQL", or that a diploma is finished rather than ongoing, needs language understanding.
Without the AI calls, the app has no profile and no job requirements, so nothing can be matched.

## How it works: the four layers

```
User / resume file → io_manager  →  ai_manager  →  logic_manager  →  data_manager
                     Input layer    AI layer       Logic layer       Data layer
```

`main.py` (console) and `web_app.py` (web) connect the layers. The layers never import each
other, so each one can be changed or tested on its own.

| Layer | File | What it does |
|---|---|---|
| Input / output | `src/io_manager.py` | Everything that comes into or goes out of the system, and the only file that prints. Prompts check what is typed and ask again on bad input. Reads PDF and Word resumes; fetches job listings from MyCareersFuture; writes tailored Word files; words everything shown in the console and on the web pages. |
| AI processing | `src/ai_manager.py` | Only talks to the AI: builds each prompt, calls the AI provider, parses the JSON reply and checks it against a JSON schema and against the candidate's own resume. A malformed reply is asked for again; a failed call is retried, then logged, and never crashes the app. |
| Logic | `src/logic_manager.py` | The business rules on the AI's records: which searches to try, filters, ranking, the match outcome for each job, skill gaps, and the rules a tailored resume must keep (only the job's words the resume shows, no heading left empty, no extra page). |
| Data | `src/data_manager.py` | Saves processed jobs to `data/job_listings.json`, loads them on startup, and answers queries (keyword search, find by link). A missing file is created; a corrupt one is backed up and replaced, never crashing the app. |

### One search, step by step

1. **Input** (`io_manager`): the user picks a resume and sets their preferences. Bad values, such as
   a maximum salary below the minimum, are rejected and asked for again.
2. **AI** (`ai_manager`), in three kinds of AI call:
   1. **Profile:** the resume becomes a profile with qualification, years of experience, level, skills and job titles to search for.
   2. **Job requirements:** `io_manager` searches MyCareersFuture for each job title, and the listings that fit the user's preferences go to the AI in batches of 10. If none fit, the search drops some preferences and says so (`logic_manager.search_steps`). The AI returns each job's required skills, minimum education and experience, and which of the candidate's skills cover which requirements.
   3. **Tailoring** (web app, only when the user asks): the resume is rewritten for one job. A Word
      resume is reworded in place, so it keeps its layout; a PDF becomes a new Word file. The job's
      matched skills are only used when tailoring the resume that found the job.
3. **Logic** (`logic_manager`): jobs are filtered, ranked and given an outcome (see the business rules below).
4. **Data** (`data_manager`): the top jobs are saved, skipping ones already saved. They appear under
   "Saved jobs" in the web app and "Show my saved jobs" in the console.

### How the AI's answers are checked

Every reply must be JSON that matches the task's schema: the right keys, types and allowed values.
Anything else is rejected and asked for again (`ai_manager.validate_response` and `run_task`).

On top of the schema check, `ai_manager` compares the AI's claims with the original text, so nothing
the candidate didn't write gets through:

- **Skill matches:** a match only counts if it names a real skill from the profile and a real requirement of the job.
- **Tailored resumes:** they may only contain employers, dates, numbers and skills that appear in the
  original resume. A rewritten line that adds one of the job's skills the resume doesn't show is left out.

These checks are part of validating the AI's response. Everything else stays out of `ai_manager`, as
the specification asks: listings come from `io_manager`, business rules are in `logic_manager`, and
the sentences the user reads are written by `io_manager`. Listings that don't meet the user's
preferences are dropped before the AI, because they are input the user ruled out, not records.

## Business rules (logic layer)

**Filters.** These remove jobs. For the preference filters, the console says how many each one
removed, and the web results page names the one that removed the most:

- a link that isn't an `https://` MyCareersFuture link
- pay entirely outside the user's salary range (jobs that don't state pay are kept)
- needing more years of experience than the user allows
- the wrong job type or work arrangement ("Permanent" counts as full time; "Not specified" is kept)
- matching none of the candidate's skills

**Ranking.** Each comparison only breaks ties left by the one before it:

1. fewer years of experience short
2. meets the education requirement
3. not 4 or more levels above the candidate's level
4. more key skills matched
5. a higher share of the key skills matched
6. closer to the candidate's level

**Match outcome** (`match_outcome`). A multi-condition rule on the AI's fields:

| Outcome | When |
|---|---|
| **Stretch** | short on experience, **or** below the education asked for, **or** 4+ levels above the candidate's level |
| **Strong match** | none of those, **and** at least 60% of the job's key skills matched |
| **Fair match** | none of those, but fewer than 60% of the key skills matched |

Each outcome comes with a reason, e.g. "This job needs 2 more years of experience." It's shown on
every job card and in the console list, and saved with the job.

## Running it

You need Python 3.11+ and an API key for one AI provider (see `.env` below).

### Locally

```
pip install -r requirements.txt
python src/main.py        # console app
python src/web_app.py     # web app: open http://127.0.0.1:5000
```

Put your resume (PDF or Word) in `resumes/`, or upload it in the web app.

**Where saved jobs go:** the first run creates `data/job_listings.json`, and every search adds its top
jobs to it. The `data/` folder isn't in git (see `.gitignore`) or the Docker image, because it holds
each person's own job search; that's why the repository on GitHub has no `data/` folder.

### In Docker

With Docker Desktop running, from the project root:

```
docker compose up --build web          # web app: open http://127.0.0.1:5000
docker compose run --rm console        # console app
docker compose down                    # stop
```

- **Saved jobs, logs and resumes** stay on your computer: `./data`, `./logs` and `./resumes` are mounted into the container.
- **The image** contains only the code and a sample resume (`resumes/sample_resume_fintech.docx`). It never contains `.env` or personal resumes.

### Settings: `.env`

Create a `.env` file in the project root. It is ignored by git and Docker, so keys are never shared.

| Setting | Meaning |
|---|---|
| `GEMINI_API_KEY` | Key for Google Gemini, the default provider |
| `QWEN_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | Keys for the other providers, needed only if you use them |
| `AI_PROVIDER` | `gemini` (default), `qwen`, `openai` or `anthropic` |
| `AI_MODEL` | Optional; replaces the provider's default model, e.g. `gemini-3.1-flash-lite` |

Write each line without spaces around `=`, for example `GEMINI_API_KEY=your-key`.

All providers run with temperature 0 where the model allows it, so the same resume and listings
give the same answers across runs.

## Tests and CI

```
python tests/test_logic_manager.py   # filters, search steps, ranking, match outcome, tailoring rules
python tests/test_data_manager.py    # missing/corrupt files, backups, saving, queries, removal
python tests/test_io_manager.py      # re-asking on bad input, form checks, resume files, portal replies
python tests/test_main.py            # the console from start to finish, through all four layers
python tests/test_web_app.py         # the web app's pages and actions, through all four layers
```

They use temporary files, typed answers and fake MyCareersFuture and AI replies, so they need no
keyboard, internet or API key, and never touch your `data/job_listings.json`.

GitHub Actions (`.github/workflows/main.yml`) runs on every push and pull request to `main`:

1. install the requirements
2. compile every file
3. run all five test scripts
4. import every layer and load the web pages
5. start and exit the console app

## Project structure

```
src/
  main.py               console app: start-up, menu, one function per part of a search
  web_app.py            web app: Flask routes and the background search and tailoring threads
  io_manager.py         input/output layer
  ai_manager.py         AI layer
  logic_manager.py      logic layer
  data_manager.py       data layer
  templates/, static/   web pages, styles and scripts
  ai_manager_test/      manual scripts for trying AI providers and models
tests/                  test scripts: one per layer, and the console and web app from start to finish
Dockerfile              the image; docker-compose.yml runs it
data/                   saved jobs (created on first run, not in git)
logs/                   the console app's log file (not in git)
resumes/                resumes to search with (only the sample is in git)
```

## Team and workflow

Main areas, from the git history (names as git records them):

| Area | Main contributors |
|---|---|
| Input layer (`io_manager`) | Daniel Lim, Koo Jia Juan |
| AI layer (`ai_manager`) | Koo Jia Juan, nevinchua |
| Logic layer (`logic_manager`) | Lolucaslee, Koo Jia Juan |
| Data layer (`data_manager`) and tests | Meng Hong |
| Console app (`main.py`) | ZhaoYan, nevinchua |
| Web app, CI and Docker | nevinchua |

**How we work:**

- Each change is made on its own branch from an up-to-date `main`, for example `refactor/main-functions`.
- It is merged through a pull request once CI passes.
- Commits are small, and each one describes one change.

## Known limitations

Search results are kept in memory (the web app forgets a finished search on restart; saved jobs are
kept), live listings change between runs, and the app is built for one user on their own computer.
