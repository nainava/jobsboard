#!/usr/bin/env python3
"""
Re-check UNCERTAIN Greenhouse jobs using the free Greenhouse Board API.
Merges results back into buildlist_results.json.
"""
import requests
import json
import re
import time
from urllib.parse import urlparse
from collections import defaultdict

HEADERS = {"User-Agent": "Mozilla/5.0"}
TIMEOUT = 15

# Load current results
with open("buildlist_results.json") as f:
    results = json.load(f)

print(f"Loaded {len(results)} results")

# Separate uncertain Greenhouse jobs from the rest
greenhouse_uncertain = []
other_results = []
uncertain_non_gh = []

for r in results:
    url = r.get("url", "")
    if r["verdict"] == "UNCERTAIN" and re.match(
        r"https?://(?:boards|job-boards)\.greenhouse\.io/", url
    ):
        greenhouse_uncertain.append(r)
    elif r["verdict"] == "UNCERTAIN":
        uncertain_non_gh.append(r)
        other_results.append(r)
    else:
        other_results.append(r)

print(f"Greenhouse UNCERTAIN: {greenhouse_uncertain}")
print(f"Other UNCERTAIN (Waymo, ICON, etc.): {len(uncertain_non_gh)}")

# Group Greenhouse jobs by company slug
by_slug = defaultdict(list)
for r in greenhouse_uncertain:
    m = re.match(
        r"https?://(?:boards|job-boards)\.greenhouse\.io/([^/]+)/jobs/(\d+)",
        r["url"],
    )
    if m:
        slug, job_id = m.group(1), m.group(2)
        by_slug[slug].append((job_id, r))

print(f"\nChecking {len(by_slug)} companies via Greenhouse Board API...")
print("=" * 60)

resolved = 0
still_uncertain = 0

for i, (slug, jobs) in enumerate(sorted(by_slug.items()), 1):
    try:
        resp = requests.get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
            headers=HEADERS,
            timeout=TIMEOUT,
        )

        if resp.status_code != 200:
            print(f"[{i:3d}/{len(by_slug)}] {slug:30s} | API returned {resp.status_code} — skipping")
            still_uncertain += len(jobs)
            for _, r in jobs:
                other_results.append(r)
            continue

        data = resp.json()
        # Build set of active job IDs
        active_ids = set()
        for j in data.get("jobs", []):
            active_ids.add(str(j["id"]))

        open_count = 0
        closed_count = 0
        for job_id, r in jobs:
            # Strip gh_jid query param — use the path ID
            if job_id in active_ids:
                r["verdict"] = "OPEN"
                r["confidence"] = "HIGH"
                r["reason"] = "Confirmed active via Greenhouse API"
                open_count += 1
            else:
                r["verdict"] = "CLOSED"
                r["confidence"] = "HIGH"
                r["reason"] = "Job ID not found in Greenhouse API — listing removed"
                closed_count += 1
            other_results.append(r)
            resolved += 1

        total_api = len(data.get("jobs", []))
        print(
            f"[{i:3d}/{len(by_slug)}] {slug:30s} | API: {total_api:4d} active | "
            f"Checked: {len(jobs):3d} → {open_count} open, {closed_count} closed"
        )

        # Be nice — small delay between API calls
        time.sleep(0.2)

    except Exception as e:
        print(f"[{i:3d}/{len(by_slug)}] {slug:30s} | ERROR: {str(e)[:60]}")
        still_uncertain += len(jobs)
        for _, r in jobs:
            other_results.append(r)

print("=" * 60)
print(f"Resolved: {resolved} | Still uncertain: {still_uncertain}")

# Also re-check Waymo and ICON with a simple retry
print(f"\nRetrying {len(uncertain_non_gh)} non-Greenhouse uncertain jobs...")
for i, r in enumerate(uncertain_non_gh, 1):
    # These are already in other_results, we just update in place
    url = r.get("url", "")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True, verify=False)
        if resp.status_code == 200:
            text = resp.text.lower()
            if re.search(r"apply\s+(now|for\s+this|today|here)|submit\s+(your\s+)?application", text):
                r["verdict"] = "OPEN"
                r["confidence"] = "HIGH"
                r["reason"] = "Active application mechanism found (retry)"
            elif len(text) > 2000:
                r["verdict"] = "OPEN"
                r["confidence"] = "MEDIUM"
                r["reason"] = "Page loads with content (retry)"
            if i % 50 == 0:
                print(f"  [{i}/{len(uncertain_non_gh)}]")
        elif resp.status_code in (404, 410):
            r["verdict"] = "CLOSED"
            r["confidence"] = "HIGH"
            r["reason"] = f"HTTP {resp.status_code} — page not found (retry)"
    except Exception:
        pass

# Save merged results
# Restore original order
job_key = lambda r: r.get("title", "") + r.get("company", "")
with open("buildlist_all_jobs.json") as f:
    all_jobs = json.load(f)
order = {j["title"] + j["company"]: i for i, j in enumerate(all_jobs)}
other_results.sort(key=lambda r: order.get(job_key(r), 99999))

with open("buildlist_results.json", "w") as f:
    json.dump(other_results, f, indent=2)

# Print final stats
from collections import Counter
verdicts = Counter(r["verdict"] for r in other_results)
print(f"\nFinal results ({len(other_results)} total):")
for v, c in verdicts.most_common():
    print(f"  {v:15s} {c}")

print(f"\nSaved to buildlist_results.json")
