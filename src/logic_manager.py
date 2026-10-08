import json

def load_inventory():
    try:
        nested_list = []
        with open("src/sample_job_listings.json", "r") as file:
            data = json.load(file)
            job_listings = data.get("job_listings", [])
            
            for item in job_listings:
                pay_range = item.get("pay_range", {})
                nested_list.append({
                    "job_title": item.get("job_title", "NaN"),
                    "company": item.get("company", "NaN"),
                    "location": item.get("location", "NaN"),
                    "employment_type": item.get("employment_type", "NaN"),
                    "suitability_reason": item.get("suitability_reason", "NaN"),
                    "min": pay_range.get("min", -1),
                    "max": pay_range.get("max", -1),
                    "currency": pay_range.get("currency", "NaN"),
                    "period": pay_range.get("period", "NaN"),
                    "job_url": item.get("job_url", "NaN")
                })
        return nested_list

    except FileNotFoundError:
        print("Error: The file 'sample_job_listings.json' was not found.")
        return []

def display_listings(listings):
    print("==========================================")
    for item in listings:
        print(f"Job Title: {item['job_title']}")
        print(f"Company: {item['company']}")
        print(f"Location: {item['location']}")
        print(f"Employment Type: {item['employment_type']}")
        print(f"Suitability Reason: {item['suitability_reason']}")
        print(f"Min Pay: {item['min']}")
        print(f"Max Pay: {item['max']}")
        print(f"Currency: {item['currency']}")
        print(f"Period: {item['period']}")
        print(f"URL: {item.get('job_url', 'N/A')}")
        print("==========================================")
        
listing_json = load_inventory()
display_listings(listing_json)