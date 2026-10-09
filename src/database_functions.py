import json
import os

json_database = []

def read_database():
    temp_arr = []

    # attempts to read database
    try:
        with open('job_listings.json', 'r', encoding='utf-8') as file:
            data = json.load(file)
            return data

    # if failure or database doesnt exist, creates it and writes a placeholder entry into it
    except:
        with open("job_listings.json", "w", encoding="utf-8") as file:
            json.dump(temp_arr, file)
            return temp_arr


def write_database(to_write):

    # try to write database if fails, writes empty array
    try:

        # create IDs for entries before writing to database
        to_write = reorder_ids(to_write)

        # dump all listings into a json file for storage
        with open("job_listings.json", "w", encoding="utf-8") as file:
            json.dump(to_write, file)
            return

    # catch any errors, write empty array instead if error exists
    except:
        to_write = []
        with open("job_listings.json", "w", encoding="utf-8") as file:
            json.dump(to_write, file)
            return

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