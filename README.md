# Resume Job Matcher

Resume Job Matcher reads your resume and finds the jobs on [MyCareersFuture](https://www.mycareersfuture.gov.sg) that
fit you best. It runs in your web browser, on your own computer.

It was built by INF1103 P3 Team 6 for Part 2 of the team project. How it is built, and where each requirement of the
brief is in the code, is in [docs/DESIGN.md](docs/DESIGN.md).

## What it does

- **Finds and ranks jobs.** Upload a PDF or Word resume. The AI (Google Gemini) reads it, the app searches
  MyCareersFuture, and you get your best matches, each with the skills you have and the ones to build.
- **Labels each match.** *Strong match*: you meet the requirements and have at least 60% of the key skills.
  *Partial match*: you meet the requirements but have fewer of the skills. *Missing a requirement*: the job asks for
  more experience or a higher qualification than you have, or is well above your level.
- **Tailors your resume to one job.** The AI rewords your resume for that job, using only what your resume already
  says, and gives you a Word file to download.
- **Shows what to learn next.** On the **What if I learned...?** page, tick a skill to see how your matches would
  change, or get a short learning plan that aims to rank you higher or to earn more.
- **Tracks your applications.** Every search saves its best jobs. On the **Saved jobs** page you can mark each one
  Saved, Applied, Interviewing, Offer or Rejected, add a note, and filter or sort them.

## Your privacy

The text of your resume is sent to Google Gemini to be read. Try the app with the sample resume
(`resumes/sample_resume_fintech.docx`) first, and remove private details such as your home address or NRIC before
using your own. Your API key and saved jobs stay on your computer.

## Getting started

You need a Google Gemini API key, and either Docker Desktop (easiest) or Python 3.11 or newer.
[GETTING_STARTED.md](GETTING_STARTED.md) has step-by-step instructions and help if something goes wrong. In short:

1. Put your key in a file named `.env` in the project folder: `GEMINI_API_KEY=your-key`
2. Start the app and its database:

   ```
   docker compose up --build web
   ```
3. Open http://127.0.0.1:5000 in your browser.

Stop it with `Ctrl+C`. Your saved jobs are kept for next time.

## Settings

Settings go in the `.env` file. Only the API key is required; `.env.example` lists every setting.

| Setting | What it does |
|---|---|
| `GEMINI_API_KEY` | Your Google Gemini key (required) |
| `AI_MODEL` | Use a different Gemini model than the default |
| `DATABASE_URL` | Where your saved jobs are kept, if you run your own PostgreSQL database instead of the Docker one |
| `POSTGRES_PASSWORD` | The password of the Docker database (default `jobmatcher`) |
| `AI_CACHE` | `on` saves the AI's answers and replays them, so a demo can run again the same way without the internet |

## Good to know

- Search results are forgotten when the app restarts; your saved jobs are kept.
- MyCareersFuture listings change every day, so the same search on another day can find different jobs.
- The app is meant for one person on their own computer.
- If the database is not running, searches still work, but the Saved jobs page cannot open your saved jobs.
