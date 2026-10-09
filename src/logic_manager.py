import json
import urllib.request
import urllib.error

def load_inventory():
    try:
        nested_list = []
        file_path = "src/sample_job_listings.json"
        with open(file_path, "r") as file:
            data = json.load(file)
            job_listings = data.get("job_listings", [])
            
            valid_job_listings = check_list_url(job_listings)
            data["job_listings"] = valid_job_listings
            print(valid_job_listings)
            
            # Sort jobs by maximum salary, from highest to lowest
            valid_job_listings.sort(
                key=lambda item: item.get("pay_range", {}).get("max", 0),
                reverse=True
            )
            
            for item in valid_job_listings:
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
            display_listings(nested_list)
            with open("src/updated_job_listings.json", "w") as file:
                json.dump(nested_list, file, indent=2)

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
        
        
def check_list_url(listings):
    valid_url_list = []
    for item in listings:
        url = item.get("job_url", "NaN")
        if check_url(url):
            valid_url_list.append(item)
    return valid_url_list



def check_url(url):
    try:
        # Create request to URL
        req = urllib.request.Request(
            url,
            method="HEAD",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        # Try to connect to the URL
        with urllib.request.urlopen(req, timeout=10) as response:
            print("URL is reachable!")
            print("Status code:", response.status)
            return True

    # Checking HTTPError, URLError, and ValueError to handle different types of URL issues
    except urllib.error.HTTPError as e:
        print("HTTP error:", e.code)
        return False

    except urllib.error.URLError as e:
        print("URL is unreachable:", e.reason)
        return False

    except ValueError as e:
        print("Invalid URL:", e)
        return False
        
listing_json = load_inventory()
#display_listings(listing_json)