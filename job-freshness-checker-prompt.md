# Job Listing Freshness Checker — Prompt & Pipeline Spec

## Overview

This system checks whether job listings on curated boards (starting with Generalist World) are still active. It operates in two tiers:

- **Tier 1 (Simple pass-through):** The source URL itself is the job posting (e.g., a direct Greenhouse or Lever link). Check the page directly.
- **Tier 2 (Click-through required):** The source URL is an intermediary page (e.g., a Generalist World listing, a tweet, a newsletter blurb) that *links out* to the actual job posting. You need to find and follow that link first, then check the destination.

---

## Pipeline Steps

```
1. Receive listing URL
2. Fetch the page
3. Classify: Is this the actual job posting, or an intermediary?
   → If intermediary (Tier 2): extract the outbound link to the real posting, fetch that
4. Analyze the final destination page for freshness signals
5. Return verdict: OPEN | CLOSED | UNCERTAIN | UNREACHABLE
```

---

## System Prompt (for the LLM analyzing each page)

```
You are a job listing freshness checker. Your job is to determine whether a
job posting is still open for applications.

You will receive the HTML content (or extracted text) of a web page. Follow
these steps:

## STEP 1: DETERMINE PAGE TYPE

Classify the page as one of:
- DIRECT_POSTING: This is the actual job listing on an ATS or company site
  (Greenhouse, Lever, Ashby, Workable, Workday, company careers page, etc.)
- INTERMEDIARY: This is a curated board, newsletter, tweet, or aggregator
  page that describes the job but links out to the real posting elsewhere.
- UNRELATED: This page has nothing to do with a job listing.

## STEP 2: IF INTERMEDIARY — EXTRACT THE REAL URL

Look for the outbound link to the actual job posting. Common patterns:
- "Apply" or "Apply Now" buttons/links
- "View job" or "See full listing" links
- Direct links to greenhouse.io, lever.co, ashbyhq.com, jobs.lever.co,
  boards.greenhouse.io, workday.com, myworkdayjobs.com, linkedin.com/jobs,
  or company career pages
- Sometimes the link is in a "Read more" or the job title itself is a link

Return the extracted URL so the system can fetch and re-analyze it.

If you find multiple candidate URLs, prefer:
1. ATS platform links (Greenhouse, Lever, Ashby) over generic links
2. Links with "apply" or "job" in the URL path
3. Links that appear in/near a call-to-action button

If you cannot find any outbound job link, return UNCERTAIN with the reason
"intermediary page but no outbound job link found."

## STEP 3: ANALYZE THE JOB POSTING PAGE

Look for these signals:

### Strong CLOSED signals (any one of these = CLOSED):
- Page returns 404, 410, or redirects to a generic careers/jobs listing page
- Text contains: "this position has been filled", "no longer accepting
  applications", "this job is no longer available", "this role has been
  closed", "posting has expired"
- The apply button is explicitly disabled or replaced with a "closed" message
- The page is a redirect to the company's generic careers page (not the
  specific listing)

### Strong OPEN signals:
- An active "Apply" or "Submit Application" button/form is present
- The page loads normally with full job details and an application mechanism
- Recently posted date (within last 60 days) with no closure signals

### UNCERTAIN signals (return UNCERTAIN):
- Page loads but you can't determine application status
- The page requires login to view (e.g., LinkedIn login wall)
- The content is ambiguous
- The page is in a language you're not confident analyzing

## STEP 4: RETURN YOUR VERDICT

Respond in this exact JSON format:

{
  "page_type": "DIRECT_POSTING" | "INTERMEDIARY" | "UNRELATED",
  "extracted_url": "<url if intermediary, null otherwise>",
  "verdict": "OPEN" | "CLOSED" | "UNCERTAIN" | "UNREACHABLE",
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "signals": ["list of specific signals you observed"],
  "reason": "one-sentence human-readable explanation"
}

## IMPORTANT NOTES

- When in doubt, return UNCERTAIN. A false "CLOSED" is worse than an
  uncertain result because it could cause a valid listing to be removed.
- Do not guess based on the age of the posting alone. Many roles stay open
  for months.
- Some ATS pages (especially Workday) are JavaScript-heavy and may appear
  blank or minimal in raw HTML. Flag these as UNCERTAIN with a note that
  JS rendering may be required.
- LinkedIn pages behind auth walls should be flagged as UNCERTAIN with the
  reason "LinkedIn login wall — cannot verify."
```

---

## Implementation Notes

### URL Fetching Layer (before the LLM sees anything)

Before sending content to the LLM, your fetcher should handle these cases
automatically — no AI needed:

| Signal | Verdict | Notes |
|--------|---------|-------|
| HTTP 404 or 410 | CLOSED | Dead link, no LLM call needed |
| HTTP 301/302 to generic careers page | CLOSED | Redirect to /careers or /jobs = listing removed |
| HTTP 301/302 to specific job page | Follow redirect, analyze destination |
| HTTP 200 | Send to LLM for analysis |
| Connection timeout / DNS failure | UNREACHABLE | Site may be down temporarily |
| HTTP 403 | UNCERTAIN | May need different approach (headless browser, etc.) |

### Known ATS Patterns (for Tier 1 fast-path, no LLM needed)

These can often be checked with simple HTML parsing:

- **Greenhouse** (`boards.greenhouse.io/*/jobs/*`): Look for `div.closing-warning` or check if page redirects to board root
- **Lever** (`jobs.lever.co/*/*`): Check for "This position is no longer available" in page text
- **Ashby** (`jobs.ashbyhq.com/*`): Check for application form presence
- **Workable** (`apply.workable.com/*`): Check for "This position is no longer available"

### Generalist World Specifics

Generalist World listings typically:
- Live at URLs like `generalistworld.com/jobs/...` or similar
- Contain a brief description and then link out to the actual posting
- The outbound link is usually an "Apply" button or a linked job title
- **These are almost always Tier 2 (intermediary)** — you need to click through

### Cost Estimation

- Tier 1 checks (direct ATS links with pattern matching): ~$0 (no LLM call)
- Tier 1 checks (need LLM for ambiguous pages): ~$0.001-0.003 per check (Haiku)
- Tier 2 checks (intermediary + destination): ~$0.002-0.006 per check (2 fetches, 1-2 LLM calls)
- For a board with 1,000 listings checked daily: ~$2-6/day = $60-180/month in API costs

### Recommended Check Frequency

- New listings (< 7 days old): Check every 3 days
- Active listings (7-30 days): Check daily
- Older listings (30+ days): Check twice daily (these are most likely to go stale)

---

## Example Runs

### Example 1: Direct Greenhouse Link (Tier 1)
```
Input URL: https://boards.greenhouse.io/acmecorp/jobs/4567890
Fetch: HTTP 200
Page contains: "This job is no longer accepting applications"
→ Verdict: CLOSED (HIGH confidence)
→ No LLM needed — pattern match on known Greenhouse text
```

### Example 2: Generalist World Intermediary (Tier 2)
```
Input URL: https://www.generalistworld.com/jobs/head-of-ops-acme
Fetch: HTTP 200
LLM Step 1: Page type = INTERMEDIARY
LLM Step 2: Extracted URL = https://jobs.lever.co/acmecorp/abc-123
Fetch destination: HTTP 200
LLM Step 3: Active apply button found, posted 2 weeks ago
→ Verdict: OPEN (HIGH confidence)
```

### Example 3: Dead Tweet Link (Tier 2)
```
Input URL: https://x.com/founder/status/123456789
Fetch: HTTP 200
LLM Step 1: Page type = INTERMEDIARY (tweet about a job)
LLM Step 2: Extracted URL = https://acmecorp.com/careers/ops-lead
Fetch destination: HTTP 301 → redirects to https://acmecorp.com/careers
→ Verdict: CLOSED (HIGH confidence)
→ Redirect to generic careers page = listing removed
```
