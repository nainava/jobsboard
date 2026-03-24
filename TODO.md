# Things to come back to

## Notion pages (JS-rendered)
Notion pages return HTTP 200 with just the app shell — no actual content is visible without JS execution. This means we can't extract outbound apply links (e.g. Typeform, Google Forms) from Notion-hosted job listings. The GoodWork Operations Officer listing is an example: the Notion page links to a closed Typeform (`goodworkuk.typeform.com/to/caMTvR6l`) but we can't see that link without rendering JS.

**Fix options:**
- Use a headless browser (Playwright/Puppeteer) for `notion.site` URLs
- Maintain a lookup of known Notion page -> apply URL mappings
- Check if Notion has an API for public page content

## Typeform closed detection
Typeform embeds `"isFormClosed":true` in the page JSON and includes "this typeform is no longer accepting new submissions" in the HTML. We already check for form links on non-Notion pages, but this pattern could be added to CLOSED_PATTERNS for broader coverage.

## Duplicate URL detection
Some listings share the same URL (e.g. Senja Growth Marketer and Senja Full-Stack Product Engineer both point to `jobs.senja.io/`). Could automate detection: if 2+ listings share a URL, check whether the page content matches each title.

## LinkedIn /jobs/view/ URLs
Currently we skip all LinkedIn URLs as "Likely Live" or "Uncertain" due to auth walls. But `/jobs/view/` pages sometimes return useful content without login — including "No longer accepting applications" banners. The Gut Wealth Creative Content Manager is an example. Could try fetching these with a simple UA and checking for closed signals before giving up.

## Title mismatch detection
Tried checking if the job title appears on the destination page — too many false positives because many ATS pages are JS-rendered. Could work with headless browser or by being more selective about which pages to check (e.g. only static HTML pages with >5KB of text content).

## Workable board — role not listed
Finisterre and Founders Forum both link to their Workable board root (not a specific job URL). The listed role doesn't appear on the board. Scraper should check if the specific job title exists on Workable board pages.

## Generic careers/homepage links
Several listings link to a company homepage or generic careers page instead of the specific job. Need to detect when a URL is just a homepage (short path, no job ID). Examples:
- The Leap (`the-leap.org.uk/`) — homepage, no job link
- BeAngels (`beangels.eu/en`) — homepage
- ComPsych (`compsych.com/careers/`) — generic careers page
- Citizens Advice (`citizensadvice.org.uk/.../job-and-voluntary-opportunities/`) — general vacancies listing

## Multi-page careers site search
Citizens Advice has a paginated careers site. We checked pages 1-5 and the role wasn't on any of them. Scraper should handle pagination when checking careers sites.

---

## Hardcoded overrides currently in checker.py
These were manually verified and hardcoded for the demo. Each represents a detection gap to automate:

| Company | Role | Verdict | Why hardcoded |
|---------|------|---------|---------------|
| GoodWork | Operations Officer | CLOSED | Notion page hides Typeform link; Typeform is closed |
| Citizens Advice | Human Centred Designer | CLOSED | Role not found on any page of paginated careers site |
| Senja | Full-Stack Product Engineer | CLOSED | Duplicate URL — same link as Growth Marketer role |
| Finisterre | Head of Creative | CLOSED | Role not listed on Workable board |
| Gut Wealth | Creative Content Manager | CLOSED | LinkedIn /jobs/view/ shows "No longer accepting applications" |
| Founders Forum | Senior Event Operations Manager 2026 | CLOSED | Role not listed on Workable board |
| heva | AI Engineer | CLOSED | Role not found on company hiring page |
| The Leap | National Network Manager | NEEDS_UPDATE | URL is just the homepage |
| BeAngels | Chief of Staff | NEEDS_UPDATE | URL is just the homepage |
| ComPsych | Director of Product Marketing | NEEDS_UPDATE | URL is generic careers page |
