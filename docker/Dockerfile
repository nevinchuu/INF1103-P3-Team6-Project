# Resume Job Matcher: one image for the console app (the default) and the web app.
# Build from the project root:  docker build -f docker/Dockerfile -t jobmatcher .
# (or use docker-compose.yml, which also keeps saved jobs, logs and resumes on your computer)
FROM python:3.11-slim

# Show console prompts straight away, don't write .pyc files, and let the web app be
# reached from outside the container (see WEB_HOST in web_app.py)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WEB_HOST=0.0.0.0

WORKDIR /app

# Dependencies first, so changing the code doesn't reinstall them on every build
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Only the app itself: no personal resumes, saved jobs, logs or .env go into the image
COPY src/ src/

# Empty folders for saved jobs and logs, and a resumes folder holding only a sample resume to
# try the app with. docker-compose.yml mounts your computer's own folders over all three, so
# nothing is lost when the container is removed (the sample is also in the project's resumes/)
RUN mkdir data logs
COPY resumes/sample_resume_fintech.docx resumes/

EXPOSE 5000

# The console app by default (needs a keyboard: docker run -it ...).
# The web app: docker compose up web, or docker run -p 127.0.0.1:5000:5000 jobmatcher python src/web_app.py
CMD ["python", "src/main.py"]
