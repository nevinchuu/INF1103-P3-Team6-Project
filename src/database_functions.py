import json
import os
import time

json_database = []

# The database file lives in <project root>/data/, wherever the program is run from
DATABASE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "job_listings.json")

def read_database():
    temp_arr = []

    # attempts to read database
    try:
        with open(DATABASE_PATH, 'r', encoding='utf-8') as file:
            data = json.load(file)
            return data

    # if the database doesnt exist yet, creates it with an empty list
    except FileNotFoundError:
        os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
        with open(DATABASE_PATH, "w", encoding="utf-8") as file:
            json.dump(temp_arr, file)
            return temp_arr

    # if the database is corrupted, keep it as a dated backup instead of overwriting it,
    # e.g. job_listings.json.20261009-153000.bak, then start again with an empty list
    except (json.JSONDecodeError, UnicodeDecodeError):
        os.replace(DATABASE_PATH, f"{DATABASE_PATH}.{time.strftime('%Y%m%d-%H%M%S')}.bak")
        return temp_arr


# any error is raised to the caller (main.py / web_app.py), which tells the user the save failed
def write_database(to_write):

    # create IDs for entries before writing to database
    to_write = reorder_ids(to_write)

    # write to a temporary file first, then swap it in. If writing fails part way,
    # the old database is left untouched instead of being emptied or half-written
    # (indent=2 keeps the file readable)
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    temp_path = DATABASE_PATH + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(to_write, file, indent=2)
    os.replace(temp_path, DATABASE_PATH)

# iterate through array of jsons
def display_database(to_print):

    # create IDs before printing
    to_print = reorder_ids(to_print)

    # Just print lol
    for i in to_print:
        print(i)

    return

# will check by job URL before insertion into database
def insert_no_duplicates(to_check, data_main):
    current_listings = []

    # Retrieve all job URLs and put into current_listing
    for i in data_main:
        current_listings.append(i["job_url"])

    # loop through listings to input, check if job url is in current_listings
    for i in to_check:

        #if job url is in, it is a duplicate and skip
        if i["job_url"] in current_listings:
            continue

        #else append listing to database and add that job url to current_listings
        data_main.append(i)
        current_listings.append(i["job_url"])

    #set database to new database and create IDs for them
    data_main = reorder_ids(data_main)
    return data_main

# re-order the IDs of job listings after removals
def reorder_ids(data_main):

    #loop through data_main and assign IDs to each JSON within the list
    for i in range(len(data_main)):
        data_main[i]["ID"] = i

    return data_main


#iterate through to_remove, an array of integers and remove specified IDs from database
#example input [1,12,35,22]
def remove_by_ID(to_remove,data_main):

    # Create IDs for entries if IDs doesnt exist
    data_main = reorder_ids(data_main)

    # Iterate and remove
    for i in to_remove:
        data_main.pop(i)

    return reorder_ids(data_main)