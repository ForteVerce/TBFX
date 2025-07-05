#!/usr/bin/env python3
"""
Fetch and process API data on demand

This snippet prompts for the full API endpoint URL,
fetched via HTTP GET with retry logic, and prints the JSON response or errors.
"""
import requests
import time
import sys

# Configuration
MAX_RETRIES = 3  # number of retry attempts on failure
RATE_LIMIT_WAIT = 60  # seconds to wait when rate limited


def fetch_url(url, retries=MAX_RETRIES):
    """Perform HTTP GET with simple retry and rate-limit handling."""
    for attempt in range(1, retries + 1):
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", RATE_LIMIT_WAIT))
            print(f"Rate limited (429), waiting {wait}s before retry {attempt}/{retries}")
            time.sleep(wait)
        else:
            try:
                resp.raise_for_status()
            except Exception as e:
                print(f"Request failed (attempt {attempt}/{retries}): {e}")
                if attempt < retries:
                    time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed to fetch after {retries} attempts: {url}")


if __name__ == "__main__":
    try:
        endpoint = input("Enter full API endpoint URL: ")
        if not endpoint.startswith("http"):
            print("Please enter a valid URL starting with http or https.")
            sys.exit(1)
        data = fetch_url(endpoint)
        print(data)
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
    except Exception as e:
        print(f"Error fetching data: {e}")
