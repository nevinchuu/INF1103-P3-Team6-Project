# Resume Job Matcher: one image for the web app (the default) and the tests.
# Build from the project root:  docker build -t jobmatcher .
# (or use docker-compose.yml, which also starts the PostgreSQL database and keeps logs and resumes on your computer)
FROM python:3.11-slim

# Show log lines straight away, don't write .pyc files, and let the web app be
# reached from outside the container (see WEB_HOST in web_app.py)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WEB_HOST=0.0.0.0

WORKDIR /app

# Dependencies first, so changing the code doesn't reinstall them on every build
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The app and its tests: no personal resumes, saved jobs, logs or .env go into the image
COPY src/ src/
COPY pytest.ini .
COPY tests/ tests/
# The UML diagrams and the README: tests check that the diagrams and the README's evidence tables match the code
COPY docs/ docs/
COPY README.md .

# Empty folders for the AI cache and logs, and a resumes folder holding only a sample resume to
# try the app with. docker-compose.yml mounts a volume or your computer's own folders over them, so
# nothing is lost when the container is removed (the sample is also in the project's resumes/).
# Saved jobs are not here: they live in the PostgreSQL service (DATABASE_URL)
RUN mkdir data logs
COPY resumes/sample_resume_fintech.docx resumes/

EXPOSE 5000

# The web app by default: docker compose up web (starts the database too).
# docker run -p 127.0.0.1:5000:5000 jobmatcher also works, but without DATABASE_URL the Saved page says it cannot open the database
# The tests: docker compose run --rm --build tests
CMD ["python", "src/web_app.py"]
