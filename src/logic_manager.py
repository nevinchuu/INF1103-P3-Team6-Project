import json

def load_inventory():
    try:
        nested_list = []
        with open("src/sample_job_listings.json", "r") as file:
            data = json.load(file)
        for item in data:
            nested_list.append({"job_title": item["job_title"], "company": item["company"], "location": item["location"], "employment_type": item["employment_type"], "suitability_reason": item["suitability_reason"], "min_pay": item["min_pay"], "max_pay": item["max_pay"], "currency": item["currency"], "job_url": item["job_url"], "comments": item["comments"]})
        return nested_list

    except FileNotFoundError:
        print("Error: The file 'sample_job_listings.json' was not found.")
        return []

def display_listings(listings):
    print("=====================================================")
    for item in listings:
        print(f"Job Title: {item['job_title']}")
        print(f"Company: {item['company']}")
        print(f"Location: {item['location']}")
        print(f"Employment Type: {item['employment_type']}")
        print(f"Suitability Reason: {item['suitability_reason']}")
        print(f"Min Pay: {item['min_pay']}")
        print(f"Max Pay: {item['max_pay']}")
        print(f"Currency: {item['currency']}")
        print(f"Job URL: {item['job_url']}")
        print(f"Comments: {item['comments']}")
        print("=====================================================\n")

listing_json = load_inventory()
display_listings(listing_json)