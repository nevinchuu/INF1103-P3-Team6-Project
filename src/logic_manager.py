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
                    "job_title": item.get("job_title", "N/A"),
                    "company": item.get("company", "N/A"),
                    "location": item.get("location", "N/A"),
                    "employment_type": item.get("employment_type", "N/A"),
                    "suitability_reason": item.get("suitability_reason", "N/A"),
                    "currency": pay_range.get("currency", "N/A"),
                    "min_pay": pay_range.get("min", "N/A"),
                    "max_pay": pay_range.get("max", "N/A"),
                    "period": pay_range.get("period", "N/A"),
                    "job_url": item.get("job_url", "N/A")
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
        print(f"Min Pay: {item['min_pay']}")
        print(f"Max Pay: {item['max_pay']}")
        print(f"Currency: {item['currency']}")
        print(f"Period: {item['period']}")
        print(f"URL: {item.get('job_url', 'N/A')}")
        print("==========================================")
        
        
listing_json = load_inventory()
display_listings(listing_json)