import json

json_database = []


def read_database():
    temp_arr = []
    try:
        with open('job_listings.json', 'r', encoding='utf-8') as file:
            data = json.load(file)
            if data[0]["job_title"] == "Placeholder":
                data.pop(0)
            return data

    except:
        with open("job_listings.json", "w", encoding="utf-8") as file:
            placeholder_listing = {
                "job_title": "top engineer",
                "company": "fake company",
                "location": "somewhere",
                "employment_type": "no time",
                "suitability_reason": "i hate you",
                "pay_range": {
                    "min": 0,
                    "max": 0,
                    "currency": "fake",
                    "period": "nah"
                },
                "job_url": "fakejobportal.com"
            }
            temp_arr.append(placeholder_listing)
            json.dump(temp_arr, file)
            return temp_arr


def write_database(to_write):
    try:
        if to_write[0]["job_title"] == "Placeholder":
            to_write.pop(0)
        to_write = reorder_ids(to_write)
        # dump all listings into a json file for storage
        with open("job_listings.json", "w", encoding="utf-8") as file:
            json.dump(to_write, file)
            return
    except:
        to_write = []
        with open("job_listings.json", "w", encoding="utf-8") as file:
            json.dump(to_write, file)
            return


def display_database(to_print):  # iterate through array of jsons
    to_print = reorder_ids(data_main)
    for i in to_print:
        print(i)
    return


def insert_no_duplicates(to_check, data_main):  # will check by job URL before insertion into database
    current_listings = []
    for i in data_main:
        current_listings.append(i["job_url"])
    for i in to_check:
        if i["job_url"] in current_listings:
            continue
        data_main.append(i)
        current_listings.append(i["job_url"])
    data_main = reorder_ids(data_main)
    return data_main

def reorder_ids(data_main):  # re-order the IDs of job listings after removals
    for i in range(len(data_main)):
        data_main[i]["ID"] = i
    return data_main

def remove_by_ID(to_remove,data_main):
    data_main = reorder_ids(data_main)
    for i in to_remove.split(","):
        if i.isdigit():
            data_main.pop(int(i))
    return reorder_ids(data_main)