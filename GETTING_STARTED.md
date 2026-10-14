# Getting started

This guide takes you from a fresh download to a working Resume Job Matcher. Pick **one** way to run it:
with Python on your computer, or in Docker. The README explains how the project is built; this file
only covers using it.

## What you need

- The code (step 1 below)
- An API key for one AI provider (step 2). The default is Google Gemini.
- A resume as a PDF or Word (.docx) file. A sample is included: `resumes/sample_resume_fintech.docx`.
- **Either** Python 3.11 or newer, **or** Docker Desktop.

The text of your resume is sent to the AI provider to be read. Use the sample resume to try the app,
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
| `GEMINI_API_KEY=...` | Key for Google Gemini, the default provider |
| `AI_PROVIDER=openai` | Optional. Use `gemini` (default), `qwen`, `openai` or `anthropic` |
| `OPENAI_API_KEY=...`, `QWEN_API_KEY=...`, `ANTHROPIC_API_KEY=...` | Only needed for the provider you choose |
| `AI_MODEL=...` | Optional. Replaces the provider's default model |

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
   once in that window and try again.
3. Install the packages:

   ```
   python -m pip install -r requirements.txt
   ```

   Use `python -m pip`, not plain `pip`, so the packages go to the same Python that runs the app.
4. Start the app you want:

   ```
   python src/main.py        # console app: a numbered menu in the terminal
   python src/web_app.py     # web app: then open http://127.0.0.1:5000 in your browser
   ```
5. Stop it with `Ctrl+C`.

## Way 2. Run with Docker

1. Start Docker Desktop and wait until it says it is running. You do not need Python for this way.
2. From the project folder, start the web app:

   ```
   docker compose up --build web
   ```

   The first run builds the image, which takes a few minutes. Open http://127.0.0.1:5000. Stop it with
   `Ctrl+C`, or run `docker compose down` from another window.
3. Or start the console app:

   ```
   docker compose run --rm console
   ```
4. Your `.env` file is read when the container starts and is never copied into the image. The
   `data/`, `logs/` and `resumes/` folders on your computer are shared with the container, so saved jobs
   and any resumes you add are still there after it stops.

Plain Docker, without Compose (your saved jobs are not kept this way):

```
docker build -t jobmatcher .
docker run -it --rm --env-file .env jobmatcher                                           # console
docker run --rm --env-file .env -p 127.0.0.1:5000:5000 jobmatcher python src/web_app.py  # web
```

## Using the app

### Web app

1. **Search** page: choose a saved resume or upload a new one, set any preferences (salary range, job type,
   work arrangement, most years of experience asked for), and click **Find matching jobs**. A progress bar
   shows the steps.
2. **Your matches** page: your profile as the AI read it, then the best jobs. Click a job to open it. It
   shows why it fits, the skills you have, the skills to build, and two buttons: **View on
   MyCareersFuture** and **Tailor my resume**.
3. **Tailor my resume**: pick a resume and click **Create tailored resume**. The AI rewords it for that job
   using only what is already in your resume. Read the result, then click **Download .docx**.
4. **Saved jobs** page: every search adds its best jobs here. You can filter by text, sort (newest, oldest,
   best skill match, highest salary), remove one job, or clear them all.

### Console app

The main menu has four options:

```
1. Find jobs for my resume
2. Show my saved jobs
3. View one job in detail
4. Exit
```

For option 1: pick your resume from the list, press Enter to leave the salary and experience questions
blank, and choose `1` (Any) for job type and work arrangement.

### What the match labels mean

| Label | Meaning |
|---|---|
| **Strong match** | You meet the job's requirements and have at least 60% of its key skills. |
| **Fair match** | You meet the requirements, but have fewer than 60% of the key skills. |
| **Stretch** | The job asks for more years of experience or a higher qualification than you have, or is four or more levels above yours. |

Stretch jobs are still shown, ranked below the others. Each card gives the reason in a sentence.

## Run the tests

The tests need no internet and no API key, and never touch your saved jobs.

```
python tests/test_logic_manager.py
python tests/test_data_manager.py
python tests/test_io_manager.py
python tests/test_main.py
python tests/test_web_app.py
```

Each prints how many tests passed. Expected: 21, 15, 11, 7 and 10.

## If something goes wrong

| What you see | What to do |
|---|---|
| `ModuleNotFoundError: No module named 'docx'` (or `flask`, `dotenv`) | The packages are not installed for the Python you ran. Run `python -m pip install -r requirements.txt` again, in the same window. |
| pip stops with a "Long Path" message | The folder path is too long for Windows. Move the project to a short folder such as `C:\Projects\`, or use Docker. |
| "Check your internet connection and API key" | This message covers any AI failure. Check that `.env` is in the project folder with the right key, and that no system variable overrides it. The terminal shows the real error. |
| `failed to connect to the docker API` | Docker Desktop is not running yet. Start it and wait until it is ready. |
| Port 5000 is already in use | Another copy of the app is running. Stop it, or in `docker-compose.yml` change `"127.0.0.1:5000:5000"` to `"127.0.0.1:5001:5000"` and open port 5001. |
| The search finds no jobs | The portal may be unreachable, or your preferences are too narrow. Clear the salary and experience fields and try again. |

For how the project is organised (the four layers, the rules, and the tests), read `README.md`.
