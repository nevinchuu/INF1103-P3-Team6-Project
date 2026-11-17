# Getting started

This guide takes you from a fresh download to a working Resume Job Matcher. It is a web app: you use it in your
browser. Pick **one** way to run it: with Python on your computer, or in Docker.

## What you need

- The code (step 1 below).
- An API key for one AI provider (step 2). The default is Google Gemini.
- A resume as a PDF or Word (.docx) file. A sample is included: `resumes/sample_resume_fintech.docx`.
- **Either** Docker Desktop (the easiest way: it also runs the database), **or** Python 3.11 or newer plus a
  PostgreSQL database. Saved jobs are kept in PostgreSQL. Way 1 shows how to get one.

The text of your resume is sent to Google Gemini to be read. Use the sample resume to try the app,
and remove private details (home address, NRIC) before using your own.

## Step 1. Get the code

```
git clone https://github.com/nevinchuu/INF1103-P3-Team6-Project.git
cd INF1103-P3-Team6-Project
```

## Step 2. Add your API key

Create a file named `.env` in the project folder (the one that contains `README.md`). In PowerShell:

```
"GEMINI_API_KEY=paste-your-key-here" | Out-File -Encoding ascii .env
```

| Line in `.env` | Meaning |
|---|---|
| `GEMINI_API_KEY=...` | Key for Google Gemini, the AI the app uses |
| `AI_MODEL=...` | Optional. Replaces the default Gemini model |
| `DATABASE_URL=...` | Optional, for Way 1 with your own PostgreSQL server only. Where that database is. Leave it out to use the Docker database |
| `POSTGRES_PASSWORD=...` | Optional. The password of the Docker `db` service (default `jobmatcher`). If you change it, change it in `DATABASE_URL` too |

`.env.example` lists every setting with a short note.

Rules for this file:

- Write each line as `NAME=value`, with no spaces around `=` and no quotes.
- The name must be exactly `.env`. Notepad often saves `.env.txt`, and Windows hides the extension.
- Never commit it or share it. Git and Docker already ignore it, so your key stays on your computer.
- If a system variable with the same name already exists (for example `GEMINI_API_KEY`), it is used
  instead of the file.

## Way 1. Run with Python

1. Check your Python version: `python --version` (it must say 3.11 or higher).
2. Create and activate a virtual environment, so the packages stay inside the project:

   ```
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

   If PowerShell refuses to run the script, run `Set-ExecutionPolicy -Scope Process RemoteSigned`
   once in that window and try again. On macOS or Linux, run `source .venv/bin/activate` instead.
3. Install the packages:

   ```
   python -m pip install -r requirements.txt
   ```

   Use `python -m pip`, not plain `pip`, so the packages go to the same Python that runs the app.
4. Start a PostgreSQL database. Choose one:
   - **With Docker Desktop** (easiest). Start Docker Desktop, then from the project folder:

     ```
     docker compose up -d db
     ```

     This starts only the database, in the background, on `127.0.0.1:5432`. You don't need to set
     `DATABASE_URL`: the default already points at it. Check it is ready with `docker compose ps`
     (the `db` line should say `healthy`).
   - **With your own PostgreSQL server.** Create a user and an empty database for the app, then put its address
     in `.env`, for example `DATABASE_URL=postgresql://myuser:mypassword@localhost:5432/jobmatcher`.
     The app creates its table itself the first time it connects.
5. Start the app:

   ```
   python src/web_app.py
   ```

   It prints how many saved jobs it loaded. Open http://127.0.0.1:5000 in your browser.
6. Stop the app with `Ctrl+C`. Stop the Docker database with `docker compose stop db`. Your saved jobs stay in it
   for next time.

If the database is not running, the app still starts and searches still work, but the Saved jobs page says it could
not open your saved jobs (see "If something goes wrong").

## Way 2. Run with Docker

1. Start Docker Desktop and wait until it says it is running. You do not need Python for this way.
2. From the project folder, start the web app and its database:

   ```
   docker compose up --build web
   ```

   The first run builds the image and downloads PostgreSQL, which takes a few minutes. The web app waits until the
   database is ready. Then open http://127.0.0.1:5000.
3. Stop it with `Ctrl+C`, or run `docker compose down` from another window.
4. Where your things are kept:
   - Your `.env` file is read when the container starts. It is never copied into the image.
   - Saved jobs are in the database, on the Docker volume `pg_data`. They are still there after the containers stop
     or are removed.
   - The `logs/` and `resumes/` folders on your computer are shared with the container, so any resume you add is
     still there after it stops.
   - `docker compose down -v` deletes the volumes too, which **deletes your saved jobs**. Use it only for a fresh start.

Plain `docker run` (without Compose) starts no database, so the Saved jobs page can't open. Use Compose.

## Using the app

1. **Search** page: choose a saved resume or upload a new one, set any preferences (salary range, job type,
   work arrangement, most years of experience asked for), and click **Find matching jobs**. A progress bar
   shows the steps.
2. **Your matches** page: your profile as the AI read it, then the best jobs. Click a job to open it. It
   shows why it fits, the skills you have, the skills to build, and two buttons: **View on
   MyCareersFuture** and **Tailor my resume**.
3. **Tailor my resume**: pick a resume and click **Create tailored resume**. The AI rewords it for that job
   using only what is already in your resume. Read the result, then click **Download .docx**. A copy is also
   kept in `resumes/tailored/`.
4. **What if I learned...?** (button on the matches page): tick a skill the jobs ask for and see how your top list
   would change, or get a learning plan that aims to rank you higher or to earn more. No AI is used.
5. **Saved jobs** page: every search adds its best jobs here. It shows how many jobs are at each stage (Saved,
   Applied, Interviewing, Offer, Rejected). You can filter by text, sort (newest, oldest, best skill match, highest
   salary), set a status and a note on a job, remove one job, or clear them all.

### What the match labels mean

| Label | Meaning |
|---|---|
| **Strong match** | You meet the job's requirements and have at least 60% of its key skills. |
| **Partial match** | You meet the requirements, but have fewer than 60% of the key skills. |
| **Missing a requirement** | The job asks for more years of experience or a higher qualification than you have, or is four or more levels above yours. |

Jobs missing a requirement are still shown, ranked below the others. Each card gives the reason in a sentence.

## If something goes wrong

| What you see | What to do |
|---|---|
| **Saved jobs could not be opened**: the Saved jobs page says "Could not open your saved jobs file", or the terminal says `[!] Could not open your saved jobs file. New searches still work.` | The app cannot reach PostgreSQL. Usually the database is not running: start it with `docker compose up -d db` (Way 1) and check `docker compose ps` says `healthy`, then reload the page. If you use your own server, check `DATABASE_URL` in `.env`. Searches and tailoring still work meanwhile. Each try waits about 5 seconds before it gives up, so the page is slow while the database is down. The real error is in `logs/job_matcher.log`. |
| `password authentication failed for user "jobmatcher"` in the log | The database password is set only when the `pg_data` volume is first made. If you changed `POSTGRES_PASSWORD` later, put the old one back, or run `docker compose down -v` to start again with an empty database (this deletes your saved jobs). |
| `docker compose up -d db` says port 5432 is already in use | Another PostgreSQL is running on your computer. Stop it, or use it as your own server (Way 1, step 4), or in `docker-compose.yml` change `"127.0.0.1:5432:5432"` to `"127.0.0.1:5433:5432"` and set `DATABASE_URL=postgresql://jobmatcher:jobmatcher@localhost:5433/jobmatcher` in `.env`. |
| `ModuleNotFoundError: No module named 'docx'` (or `flask`, `dotenv`, `psycopg`) | The packages are not installed for the Python you ran. Run `python -m pip install -r requirements.txt` again, in the same window. |
| pip stops with a "Long Path" message | The folder path is too long for Windows. Move the project to a short folder such as `C:\Projects\`, or use Docker. |
| "The AI isn't set up: GEMINI_API_KEY is not set" (or "was rejected") | The Gemini key is missing from `.env`, or Google refused it. Check that `.env` is in the project folder with the right key, and that no system variable overrides it, then restart the app (a key is read only at start-up). |
| "The AI couldn't read your resume just now" | The key is fine, but the AI could not be reached or was busy. Wait a minute and try again. The terminal and `logs/job_matcher.log` show the real error. |
| `failed to connect to the docker API` | Docker Desktop is not running yet. Start it and wait until it is ready. |
| Port 5000 is already in use | Another copy of the app is running. Stop it, or in `docker-compose.yml` change `"127.0.0.1:5000:5000"` to `"127.0.0.1:5001:5000"` and open port 5001. |
| The search finds no jobs | The portal may be unreachable, or your preferences are too narrow. Clear the salary and experience fields and try again. |

For how the project is built (the classes, the design and the tests), read `docs/DESIGN.md`.
