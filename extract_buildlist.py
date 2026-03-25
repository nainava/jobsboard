#!/usr/bin/env python3
"""Extract all job listings from buildlist.xyz/jobs page."""
import requests
import re
import json

print("Fetching buildlist.xyz/jobs...")
resp = requests.get(
    "https://www.buildlist.xyz/jobs",
    headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
    timeout=30,
)
html = resp.text
print(f"Page size: {len(html)} chars")

# Extract __next_f.push chunks
chunks = re.findall(r'self\.__next_f\.push\(\[.*?,"(.*?)"\]\)', html, re.DOTALL)
print(f"Found {len(chunks)} chunks")

# Concatenate and unescape
all_text = ''.join(chunks)
all_text = all_text.replace('\\"', '"')

# Extract job objects
jobs = []
pattern = re.compile(
    r'"id":"([^"]+)",'
    r'"company":"([^"]+)",'
    r'"company_slug":"([^"]*)",'
    r'"company_stage":"([^"]*)",'
    r'"title":"([^"]+)",'
    r'"apply_url":"([^"]+)",'
    r'"city":\[([^\]]*)\],'
    r'"sector":"([^"]*)",'
    r'"job_type":"([^"]*)",'
    r'"experience_level":"([^"]*)"'
)

for m in pattern.finditer(all_text):
    id_, company, slug, stage, title, url, cities, sector, jtype, exp = m.groups()
    city = cities.replace('"', '').strip()
    # Unescape unicode
    title = title.encode().decode('unicode_escape', errors='ignore')
    company = company.encode().decode('unicode_escape', errors='ignore')
    city = city.encode().decode('unicode_escape', errors='ignore')

    jobs.append({
        "title": title,
        "company": company,
        "location": city,
        "region": sector,
        "url": url,
    })

print(f"Extracted {len(jobs)} jobs")

if jobs:
    print(f"First: {jobs[0]['title']} @ {jobs[0]['company']}")
    print(f"Last:  {jobs[-1]['title']} @ {jobs[-1]['company']}")

    with open("buildlist_all_jobs.json", "w") as f:
        json.dump(jobs, f, indent=2)
    print("Saved to buildlist_all_jobs.json")

    # Stats
    sectors = {}
    for j in jobs:
        s = j["region"]
        sectors[s] = sectors.get(s, 0) + 1
    print("\nBy sector:")
    for s, c in sorted(sectors.items(), key=lambda x: -x[1]):
        print(f"  {s}: {c}")
