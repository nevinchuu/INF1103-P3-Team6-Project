import json
import logging
import os
import tempfile
import time

json_database = []

# The database file lives in <project root>/data/, wherever the program is run from
DATABASE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "job_listings.json")

# How often to retry swapping in a new file when Windows reports it as locked
# (e.g. an antivirus or OneDrive scan has it open for a moment), and the wait between tries
REPLACE_ATTEMPTS = 5
REPLACE_WAIT_SECONDS = 0.2


# keeps a broken database as a dated backup instead of overwriting it,
# e.g. job_listings.json.20261009-153000.bak
def back_up_database(reason):
    backup_path = f"{DATABASE_PATH}.{time.strftime('%Y%m%d-%H%M%S')}.bak"
    os.replace(DATABASE_PATH, backup_path)
    logging.warning(f"Saved jobs file was {reason}; kept it as {backup_path} and started a new one")


# reads the database. A missing file is created empty; a corrupt file, or one that is not a list
# of records, is backed up and replaced by an empty list. Any other error (e.g. no permission to
# read the file) is raised, so a save never overwrites a file it could not read
def read_database():
    temp_arr = []

    # attempts to read database
    try:
        with open(DATABASE_PATH, 'r', encoding='utf-8') as file:
            data = json.load(file)

    # if the database doesnt exist yet, creates it with an empty list
    except FileNotFoundError:
        write_database(temp_arr)
        return temp_arr

    # if the database is corrupted, back it up and start again with an empty list
    except (json.JSONDecodeError, UnicodeDecodeError):
        back_up_database("not valid JSON")
        return temp_arr

    # valid JSON but not a list (e.g. edited by hand): same as corrupt
    if not isinstance(data, list):
        back_up_database(f"a JSON {type(data).__name__}, not a list of jobs")
        return temp_arr
    return data


# any error is raised to the caller (main.py / web_app.py), which tells the user the save failed
def write_database(to_write):
    # create IDs for entries before writing to database
    to_write = reorder_ids(to_write)

    # write to a temporary file first, then swap it in. If writing fails part way,
    # the old database is left untouched instead of being emptied or half-written.
    # Each save gets its own temporary file, so two saves never write into the same one
    # (indent=2 keeps the file readable)
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    file_descriptor, temp_path = tempfile.mkstemp(dir=os.path.dirname(DATABASE_PATH), suffix=".tmp")
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as file:
            json.dump(to_write, file, indent=2)
        for attempt in range(1, REPLACE_ATTEMPTS + 1):
            try:
                os.replace(temp_path, DATABASE_PATH)
                break
            except PermissionError:
                # Windows: another program has the file open for a moment; wait and try again
                if attempt == REPLACE_ATTEMPTS:
                    raise
                time.sleep(REPLACE_WAIT_SECONDS)
    finally:
        # after a failed save, don't leave the temporary file behind
        if os.path.exists(temp_path):
            os.remove(temp_path)


# loads every saved job, skipping anything in the file that is not a job record.
# Used on startup. A missing or corrupt file gives an empty list; a file that cannot be
# opened at all raises OSError, which the caller reports
def read_saved_jobs():
    return [job for job in read_database() if isinstance(job, dict)]


# query: saved jobs whose title, company or location contain every word in keyword,
# e.g. "data analyst" finds "Senior Data Analyst" but not "Data Engineer". Case does not matter
def search_jobs(data_main, keyword):
    words = keyword.lower().split()
    results = []
    for job in data_main:
        text = " ".join(str(job.get(field, "")) for field in ("title", "company", "location")).lower()
        if all(word in text for word in words):
            results.append(job)
    return results


# saves the jobs that are not saved yet, noting which resume found them (resume_name),
# and returns how many were new. Any error is raised to the caller, which tells the user the save failed
def save_new_jobs(to_save, resume_name):
    data_main = read_database()
    saved_urls = {job.get("job_url") for job in data_main if isinstance(job, dict)}
    new_jobs = [job for job in to_save if job.get("job_url") not in saved_urls]
    if new_jobs:
        for job in new_jobs:
            job["resume"] = resume_name  # which resume to tailor for this job later
        write_database(insert_no_duplicates(new_jobs, data_main))
    return len(new_jobs)


# query: the saved job with this link, or None
def find_by_url(data_main, job_url):
    for job in data_main:
        if job.get("job_url") == job_url:
            return job
    return None


# the saved jobs without the one with this link (IDs change after every removal, so the link is used)
def remove_by_url(data_main, job_url):
    return [job for job in data_main if job.get("job_url") != job_url]


# removes one saved job from the file, found by its link
def remove_saved_job(job_url):
    write_database(remove_by_url(read_saved_jobs(), job_url))


# removes every saved job from the file
def clear_saved_jobs():
    write_database([])


# will check by job URL before insertion into database
def insert_no_duplicates(to_check, data_main):
    current_listings = []

    # Retrieve all job URLs and put into current_listing
    for i in data_main:
        current_listings.append(i["job_url"])

    # loop through listings to input, check if job url is in current_listings
    for i in to_check:

        # if job url is in, it is a duplicate and skip
        if i["job_url"] in current_listings:
            continue

        # else append listing to database and add that job url to current_listings
        data_main.append(i)
        current_listings.append(i["job_url"])

    # set database to new database and create IDs for them
    data_main = reorder_ids(data_main)
    return data_main


# re-order the IDs of job listings after removals
def reorder_ids(data_main):
    # loop through data_main and assign IDs to each JSON within the list
    for i in range(len(data_main)):
        data_main[i]["ID"] = i

    return data_main


# iterate through to_remove, an array of integers and remove specified IDs from database
# example input [1,12,35,22]
def remove_by_ID(to_remove, data_main):
    # Create IDs for entries if IDs doesnt exist
    data_main = reorder_ids(data_main)

    # Remove the highest IDs first, so a removal never shifts the IDs still to be removed
    # ([1, 3] would otherwise remove jobs 1 and 4). IDs that don't exist are skipped: a negative
    # one would remove a job counted from the end, and one past the end would raise IndexError
    # (type() rather than isinstance(), because True counts as 1)
    valid_ids = {job_id for job_id in to_remove if type(job_id) is int and 0 <= job_id < len(data_main)}
    for job_id in sorted(valid_ids, reverse=True):
        data_main.pop(job_id)

    return reorder_ids(data_main)
