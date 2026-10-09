import json
import requests
from urllib.parse import urlparse

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
        if is_url_live(url):
            valid_url_list.append(item)
    return valid_url_list

def is_url_live(url: str, timeout: int = 15) -> bool:
# 1. Check basic syntax / scheme
    parsed = urlparse(url)
    # Check if the URL has both a scheme (http/https) and a domain name (netloc)
    if not parsed.scheme or not parsed.netloc:
        print(f"[-] Invalid URL structure or scheme missing: '{url}'")
        return False
    if parsed.scheme not in ("http", "https"):
        print(f"[-] Unsupported scheme ({parsed.scheme}): '{url}'")
        return False

    # 2. Check live connection
    try:
        # Use HEAD request first to save bandwidth
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.head(url, allow_redirects=True, timeout=timeout, headers=headers)

        # Some servers block HEAD requests; fallback to GET if HEAD returns 405/403
        if response.status_code in (403, 405):
            print(f"[!] HEAD request blocked ({response.status_code}), retrying with GET...")
            response = requests.get(url, allow_redirects=True, timeout=timeout, headers=headers, stream=True)

        is_live = response.status_code < 400
    
        if is_live:
            print(f"[+] URL is live! Status code: {response.status_code} ({url})")
        else:
            print(f"[-] URL returned error status: {response.status_code} ({url})")

        return is_live
        
    except requests.RequestException as e:
        print(f"[-] Connection failed for '{url}': {e}")
        return False
    except Exception as e:
        print(f"[-] Unexpected error for '{url}': {e}")
        return False
        
listing_json = load_inventory()
#display_listings(listing_json)