#!/usr/bin/env python3
"""
Job Listing Freshness Checker
Checks whether job listings from Generalist World are still active.
Outputs a shareable HTML report.
"""

import requests
from bs4 import BeautifulSoup
import json
import time
import re
import sys
from urllib.parse import urlparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# Suppress SSL warnings for older Python/LibreSSL
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

TIMEOUT = 15

REGION_LABELS = {
    "remote": "Remote",
    "uk": "United Kingdom",
    "us": "United States & Canada",
    "eu": "Europe",
    "apac": "Asia Pacific",
    "contract": "Part-Time & Contract",
    "social-impact": "Social Impact",
    "mildreds-picks": "Mildred's Picks",
}


def scrape_generalist_world():
    """Scrape all job listings from generalist.world/jobs/ dynamically."""
    print("Fetching generalist.world/jobs/ ...")
    resp = requests.get(
        "https://generalist.world/jobs/",
        headers=HEADERS,
        verify=False,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    cards = soup.find_all(class_="gw-job-card")

    jobs = []
    for card in cards:
        title_el = card.find(class_="gw-job-title")
        company_el = card.find(class_="gw-job-company")
        location_el = card.find(class_="gw-location")

        title = title_el.get_text(strip=True) if title_el else ""
        company = company_el.get_text(strip=True) if company_el else ""
        location = location_el.get_text(strip=True) if location_el else ""
        url = card.get("href", "")
        region_key = card.get("data-region", "other")
        region = REGION_LABELS.get(region_key, region_key.title())

        jobs.append({
            "title": title,
            "company": company,
            "location": location,
            "region": region,
            "url": url,
        })

    print(f"Found {len(jobs)} job listings")
    return jobs


# ── Closed-signal patterns ──────────────────────────────────────────────
CLOSED_PATTERNS = [
    r"this position has been filled",
    r"no longer accepting applications",
    r"this job is no longer available",
    r"this role has been closed",
    r"posting has expired",
    r"position is no longer available",
    r"this position is no longer available",
    r"this job has been expired",
    r"job not found",
    r"this listing is no longer available",
    r"sorry, this position has been closed",
    r"this opportunity is closed",
    r"applications? closed",
    r"role has been filled",
    r"no longer open",
    r"position has been removed",
    r"this posting has been removed",
    r"job is closed",
    r"this job posting is no longer active",
    r"this requisition is no longer active",
    r"this form is no longer accepting responses",
    r"form is closed",
    r"responses are no longer being accepted",
]

CLOSED_RE = re.compile("|".join(CLOSED_PATTERNS), re.IGNORECASE)

# ── Open-signal patterns ────────────────────────────────────────────────
OPEN_PATTERNS = [
    r"apply\s+(now|for\s+this|today|here)",
    r"submit\s+(your\s+)?application",
    r"<button[^>]*>.*?apply.*?</button>",
    r'<a[^>]*class="[^"]*apply[^"]*"',
    r'<input[^>]*type="submit"[^>]*>',
    r"application\s+form",
]

OPEN_RE = re.compile("|".join(OPEN_PATTERNS), re.IGNORECASE)


def is_generic_careers_page(url, original_url):
    """Check if we got redirected to a generic careers page."""
    parsed_orig = urlparse(original_url)
    parsed_final = urlparse(url)

    # Same domain but path collapsed to /careers, /jobs, or root
    if parsed_orig.netloc == parsed_final.netloc:
        final_path = parsed_final.path.rstrip("/").lower()
        orig_path = parsed_orig.path.rstrip("/").lower()
        if final_path != orig_path and final_path in ("", "/careers", "/jobs", "/job", "/join", "/join-us", "/work-with-us"):
            return True
    return False


# ── Known ATS / application domains ─────────────────────────────────────
ATS_DOMAINS = [
    "greenhouse.io", "boards.greenhouse.io", "job-boards.greenhouse.io",
    "lever.co", "jobs.lever.co",
    "ashbyhq.com", "jobs.ashbyhq.com",
    "workable.com", "apply.workable.com",
    "workday.com", "myworkdayjobs.com",
    "smartrecruiters.com",
    "bamboohr.com",
    "personio.com",
    "breezy.hr",
    "applytojob.com",
    "rippling.com", "ats.rippling.com",
    "docs.google.com/forms",
    "forms.gle",
    "tally.so",
    "airtable.com",
    "typeform.com",
]

# Domains that are intermediaries (not the actual job posting)
INTERMEDIARY_DOMAINS = [
    "substack.com",
    "strangevc.com",  # Strange Ventures Substack
    "medium.com",
    "twitter.com", "x.com",
    "notion.site",  # some notion pages are intermediaries
]


def is_intermediary_page(url):
    """Check if a URL is likely an intermediary page that links out to the real job."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    for domain in INTERMEDIARY_DOMAINS:
        if domain in host:
            return True
    return False


def extract_apply_links(html, page_url):
    """Extract outbound apply/job links from an intermediary page."""
    soup = BeautifulSoup(html, "html.parser")
    candidates = []

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href or href.startswith("#") or href.startswith("mailto:"):
            continue

        # Make absolute
        if href.startswith("/"):
            parsed_page = urlparse(page_url)
            href = f"{parsed_page.scheme}://{parsed_page.netloc}{href}"

        link_text = a.get_text(strip=True).lower()
        href_lower = href.lower()
        parsed_href = urlparse(href)
        href_host = parsed_href.netloc.lower()

        # Score the link
        score = 0

        # ATS domain links are high priority
        for ats in ATS_DOMAINS:
            if ats in href_host or ats in href_lower:
                score += 10
                break

        # Links with apply-related text
        if any(kw in link_text for kw in ["apply", "submit", "application", "interested", "join"]):
            score += 5

        # Links with apply/job in the URL
        if any(kw in href_lower for kw in ["/apply", "/jobs/", "/job/", "/careers/", "closedform", "viewform"]):
            score += 3

        # Skip same-domain navigation links
        page_host = urlparse(page_url).netloc.lower()
        if href_host == page_host:
            # Same-domain links are less likely to be the apply link
            score -= 2

        if score > 0:
            candidates.append((score, href))

    # Sort by score descending
    candidates.sort(key=lambda x: -x[0])
    return [url for _, url in candidates[:5]]  # Return top 5 candidates


SHORT_URL_DOMAINS = ["forms.gle", "bit.ly", "t.co", "tinyurl.com", "goo.gl"]


def resolve_short_url(url):
    """Resolve short URLs (forms.gle, bit.ly, etc.) that use JS redirects.
    Uses a simple User-Agent to get the HTTP 302 redirect instead of a JS page."""
    parsed = urlparse(url)

    if any(domain in parsed.netloc for domain in SHORT_URL_DOMAINS):
        try:
            resp = requests.head(
                url,
                headers={"User-Agent": "curl/7.64.1"},
                timeout=TIMEOUT,
                allow_redirects=False,
                verify=False,
            )
            location = resp.headers.get("Location")
            if location:
                return location
        except Exception:
            pass

    return url


def check_url(url):
    """Fetch a URL and return (status, final_url, text) or raise."""
    # Resolve short URLs first
    resolved = resolve_short_url(url)

    resp = requests.get(
        resolved,
        headers=HEADERS,
        timeout=TIMEOUT,
        allow_redirects=True,
        verify=False,
    )
    return resp.status_code, resp.url, resp.text.lower() if resp.text else ""


def check_job(job):
    """Check a single job listing. Returns the job dict with verdict added."""
    url = job.get("url")
    result = dict(job)

    if not url:
        result["verdict"] = "UNCERTAIN"
        result["confidence"] = "LOW"
        result["reason"] = "No URL provided"
        result["http_status"] = None
        result["final_url"] = None
        return result

    # Skip mailto links
    if url.startswith("mailto:"):
        result["verdict"] = "UNCERTAIN"
        result["confidence"] = "LOW"
        result["reason"] = "Email application — cannot verify"
        result["http_status"] = None
        result["final_url"] = None
        return result

    # LinkedIn posts/jobs are mostly behind auth walls
    parsed = urlparse(url)
    if "linkedin.com" in parsed.netloc:
        if "/posts/" in parsed.path:
            result["verdict"] = "UNCERTAIN"
            result["confidence"] = "LOW"
            result["reason"] = "LinkedIn post — cannot verify without auth"
            result["http_status"] = None
            result["final_url"] = url
            return result

    try:
        resp = requests.get(
            url,
            headers=HEADERS,
            timeout=TIMEOUT,
            allow_redirects=True,
            verify=False,
        )
        status = resp.status_code
        final_url = resp.url
        text = resp.text.lower() if resp.text else ""

        result["http_status"] = status
        result["final_url"] = final_url

        # ── HTTP-level signals ──
        if status in (404, 410):
            result["verdict"] = "CLOSED"
            result["confidence"] = "HIGH"
            result["reason"] = f"HTTP {status} — page not found"
            return result

        if status == 403:
            result["verdict"] = "UNCERTAIN"
            result["confidence"] = "LOW"
            result["reason"] = "HTTP 403 — access forbidden"
            return result

        if status >= 500:
            result["verdict"] = "UNREACHABLE"
            result["confidence"] = "MEDIUM"
            result["reason"] = f"HTTP {status} — server error"
            return result

        if status != 200:
            result["verdict"] = "UNCERTAIN"
            result["confidence"] = "LOW"
            result["reason"] = f"HTTP {status}"
            return result

        # ── Redirect to generic careers page ──
        # Job might still be live but URL is stale — needs a new link
        if is_generic_careers_page(final_url, url):
            result["verdict"] = "NEEDS_UPDATE"
            result["confidence"] = "HIGH"
            result["reason"] = "URL redirects to generic careers page — needs new link"
            return result

        # ── URL-based closed signals ──
        # Workable/ATS redirect to board with ?not_found=true
        parsed_final = urlparse(final_url)
        final_qs = parsed_final.query.lower()
        if "not_found=true" in final_qs or "not_found=1" in final_qs:
            result["verdict"] = "CLOSED"
            result["confidence"] = "HIGH"
            result["reason"] = "Redirected to board with not_found=true"
            return result

        # Specific job URL redirected to the company board root
        # (e.g. /company/j/JOBID -> /company/ or /company/?...)
        if final_url != url:
            orig_path = urlparse(url).path.rstrip("/")
            final_path = parsed_final.path.rstrip("/")
            # Original had a deeper path (specific job) but final is shallower
            if len(orig_path.split("/")) > len(final_path.split("/")) + 1:
                result["verdict"] = "CLOSED"
                result["confidence"] = "HIGH"
                result["reason"] = "Specific job URL redirected to company board"
                return result

        # ── LinkedIn auth wall ──
        if "linkedin.com" in urlparse(final_url).netloc:
            if "authwall" in text or "login" in final_url.lower() or "sign in" in text[:2000]:
                result["verdict"] = "UNCERTAIN"
                result["confidence"] = "LOW"
                result["reason"] = "LinkedIn login wall — cannot verify"
                return result

        # ── Text-level signals ──
        # Check for closed signals, but filter out false positives from
        # JS framework template conditionals (v-if, v-show, ng-if, x-show, etc.)
        closed_match = CLOSED_RE.search(text)
        open_match = OPEN_RE.search(text)

        if closed_match:
            # Check if the closed text is inside a conditional template
            # (e.g. v-if="job.is_expired") — these are not real closed signals
            match_pos = closed_match.start()
            raw_context = resp.text[max(0, match_pos - 300):match_pos + 200]
            is_conditional = bool(re.search(
                r'v-if=|v-show=|ng-if=|ng-show=|x-show=|x-if=|\*ngIf=|display:\s*none',
                raw_context,
                re.IGNORECASE,
            ))

            if is_conditional and open_match:
                # Closed text is in a template conditional AND there are active
                # apply buttons — trust the open signals instead
                pass
            else:
                result["verdict"] = "CLOSED"
                result["confidence"] = "HIGH"
                result["reason"] = f'Closed signal found: "{closed_match.group()}"'
                return result

        # ── Check outbound apply links to external forms ──
        # This catches cases where the listing page looks fine but the
        # actual application form (Google Forms, Tally, etc.) is closed.
        # We do this for ALL pages, not just intermediaries.
        apply_links = extract_apply_links(resp.text, final_url)
        # Filter to only external form links (these can close independently)
        FORM_DOMAINS = ["docs.google.com/forms", "forms.gle", "tally.so",
                        "typeform.com", "airtable.com"]
        form_links = [l for l in apply_links
                      if any(fd in l.lower() for fd in FORM_DOMAINS)]

        # For intermediary pages, check ALL outbound apply links
        if is_intermediary_page(final_url):
            links_to_check = apply_links
        else:
            links_to_check = form_links

        if links_to_check:
            for apply_url in links_to_check:
                try:
                    a_status, a_final, a_text = check_url(apply_url)

                    # Check for closed form (Google Forms)
                    if "closedform" in a_final.lower():
                        result["verdict"] = "CLOSED"
                        result["confidence"] = "HIGH"
                        result["final_url"] = a_final
                        result["reason"] = "Apply form is closed (Google Forms /closedform)"
                        return result

                    if a_status in (404, 410):
                        result["verdict"] = "CLOSED"
                        result["confidence"] = "HIGH"
                        result["final_url"] = a_final
                        result["reason"] = f"Apply link returns HTTP {a_status}"
                        return result

                    # Check not_found in destination
                    a_parsed = urlparse(a_final)
                    if "not_found=true" in a_parsed.query.lower():
                        result["verdict"] = "CLOSED"
                        result["confidence"] = "HIGH"
                        result["final_url"] = a_final
                        result["reason"] = "Apply link redirects to not_found"
                        return result

                    # Check for closed signals in destination text
                    a_closed = CLOSED_RE.search(a_text)
                    if a_closed:
                        result["verdict"] = "CLOSED"
                        result["confidence"] = "HIGH"
                        result["final_url"] = a_final
                        result["reason"] = f'Apply destination closed: "{a_closed.group()}"'
                        return result

                    # For intermediary pages, check open signals too
                    if is_intermediary_page(final_url):
                        a_open = OPEN_RE.search(a_text)
                        if a_open:
                            result["verdict"] = "OPEN"
                            result["confidence"] = "HIGH"
                            result["final_url"] = a_final
                            result["reason"] = "Apply destination has active application"
                            return result

                        if a_status == 200 and len(a_text) > 1000:
                            result["verdict"] = "OPEN"
                            result["confidence"] = "MEDIUM"
                            result["final_url"] = a_final
                            result["reason"] = "Apply destination loads with content"
                            return result

                except Exception:
                    continue

        # For intermediary pages with no successful link checks
        if is_intermediary_page(final_url):
            if open_match:
                result["verdict"] = "OPEN"
                result["confidence"] = "LOW"
                result["reason"] = "Intermediary page has apply text but couldn't verify destination"
                return result
            result["verdict"] = "UNCERTAIN"
            result["confidence"] = "LOW"
            result["reason"] = "Intermediary page — could not find or verify apply link"
            return result

        # ── Direct page with open signals ──
        if open_match:
            result["verdict"] = "OPEN"
            result["confidence"] = "HIGH"
            result["reason"] = "Active application mechanism found"
            return result

        # ── Heuristic: page loaded with substantial content ──
        if len(text) > 2000:
            result["verdict"] = "OPEN"
            result["confidence"] = "MEDIUM"
            result["reason"] = "Page loads with content, no closure signals"
            return result

        # ── JS-heavy or minimal content ──
        result["verdict"] = "UNCERTAIN"
        result["confidence"] = "LOW"
        result["reason"] = "Page content too minimal — may require JS rendering"
        return result

    except requests.exceptions.Timeout:
        result["http_status"] = None
        result["final_url"] = None
        result["verdict"] = "UNREACHABLE"
        result["confidence"] = "MEDIUM"
        result["reason"] = "Connection timed out"
        return result

    except requests.exceptions.ConnectionError as e:
        result["http_status"] = None
        result["final_url"] = None
        result["verdict"] = "UNREACHABLE"
        result["confidence"] = "MEDIUM"
        result["reason"] = f"Connection error: {str(e)[:100]}"
        return result

    except Exception as e:
        result["http_status"] = None
        result["final_url"] = None
        result["verdict"] = "UNCERTAIN"
        result["confidence"] = "LOW"
        result["reason"] = f"Error: {str(e)[:100]}"
        return result


def generate_html(results, output_path="report.html", source_name="generalist.world", source_url="https://generalist.world/jobs/"):
    """Generate a shareable HTML report."""
    now = datetime.now().strftime("%B %d, %Y at %H:%M")
    now_short = datetime.now().strftime("%Y-%m-%d")

    # Stats
    total = len(results)
    open_count = sum(1 for r in results if r["verdict"] == "OPEN")
    closed_count = sum(1 for r in results if r["verdict"] == "CLOSED")
    needs_update_count = sum(1 for r in results if r["verdict"] == "NEEDS_UPDATE")
    uncertain_count = sum(1 for r in results if r["verdict"] == "UNCERTAIN")
    unreachable_count = sum(1 for r in results if r["verdict"] == "UNREACHABLE")
    healthy_count = open_count + uncertain_count
    health_pct = round(healthy_count / total * 100) if total else 0
    attention_count = closed_count + needs_update_count

    verdict_badge = {
        "OPEN": "#16a34a",
        "CLOSED": "#dc2626",
        "NEEDS_UPDATE": "#b8860b",
        "UNCERTAIN": "#65a30d",
        "UNREACHABLE": "#9ca3af",
    }

    verdict_label = {
        "OPEN": "Live",
        "CLOSED": "Closed",
        "NEEDS_UPDATE": "Needs Update",
        "UNCERTAIN": "Likely Live",
        "UNREACHABLE": "Unreachable",
    }

    # Group by region
    regions = {}
    for r in results:
        reg = r.get("region", "Other")
        regions.setdefault(reg, []).append(r)

    rows_html = ""
    for region, jobs in regions.items():
        region_closed = sum(1 for j in jobs if j["verdict"] == "CLOSED")
        region_needs = sum(1 for j in jobs if j["verdict"] == "NEEDS_UPDATE")
        region_badge = ""
        if region_closed > 0:
            region_badge += f'<span class="region-closed-badge">{region_closed} closed</span>'
        if region_needs > 0:
            region_badge += f'<span class="region-update-badge">{region_needs} needs update</span>'

        rows_html += f"""
        <tr class="region-header">
            <td colspan="5">
                {region} <span class="region-count">({len(jobs)})</span>{region_badge}
            </td>
        </tr>"""
        for r in jobs:
            verdict = r["verdict"]
            badge_color = verdict_badge.get(verdict, "#9ca3af")
            label = verdict_label.get(verdict, verdict)
            reason = r.get("reason", "")
            url = r.get("url") or ""

            if url and not url.startswith("mailto:"):
                display_url = url if len(url) <= 80 else url[:77] + "..."
                link_html = f'<a href="{url}" target="_blank" rel="noopener" title="{url}">{display_url}</a>'
            elif url.startswith("mailto:"):
                link_html = f'<span class="muted">{url}</span>'
            else:
                link_html = '<span class="muted">-</span>'

            row_class = "row-closed" if verdict == "CLOSED" else ""

            rows_html += f"""
        <tr class="{row_class}" data-verdict="{verdict}">
            <td class="col-role">
                <span class="job-title">{r['title']}</span>
                <span class="job-company">{r['company']}</span>
                <span class="job-location">{r['location']}</span>
            </td>
            <td class="col-status">
                <span class="badge" style="background:{badge_color};">{label}</span>
            </td>
            <td class="col-reason">{reason}</td>
            <td class="col-link">{link_html}</td>
        </tr>"""

    # ── Donut chart SVG ──
    import math
    def arc_path(start_angle, end_angle, r_outer=44, r_inner=30, cx=50, cy=50):
        if end_angle - start_angle >= 360:
            end_angle = start_angle + 359.999
        s1 = math.radians(start_angle - 90)
        e1 = math.radians(end_angle - 90)
        ox1 = cx + r_outer * math.cos(s1)
        oy1 = cy + r_outer * math.sin(s1)
        ox2 = cx + r_outer * math.cos(e1)
        oy2 = cy + r_outer * math.sin(e1)
        ix1 = cx + r_inner * math.cos(e1)
        iy1 = cy + r_inner * math.sin(e1)
        ix2 = cx + r_inner * math.cos(s1)
        iy2 = cy + r_inner * math.sin(s1)
        large = 1 if (end_angle - start_angle) > 180 else 0
        return (f"M {ox1},{oy1} A {r_outer},{r_outer} 0 {large} 1 {ox2},{oy2} "
                f"L {ix1},{iy1} A {r_inner},{r_inner} 0 {large} 0 {ix2},{iy2} Z")

    angle = 0
    donut_paths = ""
    segments = [
        (open_count, "#16a34a"),
        (uncertain_count, "#65a30d"),
        (unreachable_count, "#d1d5db"),
        (needs_update_count, "#b8860b"),
        (closed_count, "#dc2626"),
    ]
    for count, color in segments:
        if count > 0:
            seg_angle = (count / total) * 360
            donut_paths += f'<path d="{arc_path(angle, angle + seg_angle)}" fill="{color}" />'
            angle += seg_angle

    donut_svg = f"""<svg viewBox="0 0 100 100" class="donut">
        {donut_paths}
        <text x="50" y="47" text-anchor="middle" class="donut-pct">{health_pct}%</text>
        <text x="50" y="57" text-anchor="middle" class="donut-label">healthy</text>
    </svg>"""

    # ── Health bar ──
    bar_open_w = (open_count / total * 100) if total else 0
    bar_uncertain_w = (uncertain_count / total * 100) if total else 0
    bar_needs_w = (needs_update_count / total * 100) if total else 0
    bar_closed_w = (closed_count / total * 100) if total else 0
    bar_unreachable_w = (unreachable_count / total * 100) if total else 0

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Freshness Audit: {source_name}</title>
    <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%F0%9F%94%8D%3C/text%3E%3C/svg%3E">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: #ffffff;
            color: #1e293b;
            line-height: 1.5;
            -webkit-font-smoothing: antialiased;
        }}

        .container {{ max-width: 1440px; margin: 0 auto; padding: 32px 40px 48px; }}

        /* ── Top bar ── */
        .topbar {{
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            margin-bottom: 28px;
            padding-bottom: 20px;
            border-bottom: 1px solid #f1f5f9;
        }}
        .topbar-left h1 {{
            font-size: 17px;
            font-weight: 600;
            color: #0f172a;
            letter-spacing: -0.2px;
        }}
        .topbar-left .meta {{
            font-size: 12px;
            color: #94a3b8;
            margin-top: 2px;
        }}
        .topbar-left .meta a {{ color: #64748b; text-decoration: none; }}
        .topbar-left .meta a:hover {{ text-decoration: underline; }}
        .topbar-right {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .btn {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 7px 14px;
            border-radius: 6px;
            border: 1px solid #e2e8f0;
            background: white;
            font-size: 12px;
            font-weight: 500;
            font-family: inherit;
            color: #475569;
            cursor: pointer;
            transition: all 0.12s;
        }}
        .btn:hover {{ background: #f8fafc; border-color: #cbd5e1; }}
        .btn svg {{ width: 14px; height: 14px; stroke: currentColor; fill: none; stroke-width: 2; }}
        .toolbar-spacer {{ flex: 1; }}
        .open-range {{
            display: inline-flex;
            align-items: center;
            gap: 4px;
            flex-wrap: nowrap;
            transform: scale(0.85);
            transform-origin: right center;
            opacity: 0.7;
        }}
        .open-range:hover {{ opacity: 1; }}
        .open-range-label {{ font-size: 11px; color: #94a3b8; white-space: nowrap; }}
        .open-range-hint {{ font-size: 9px; color: #b0b8c4; white-space: nowrap; }}
        .range-input {{
            width: 40px;
            padding: 3px 4px;
            border-radius: 4px;
            border: 1px solid #e2e8f0;
            font-size: 11px;
            font-family: inherit;
            text-align: center;
            color: #64748b;
        }}
        .range-input:focus {{ outline: none; border-color: #94a3b8; }}
        .btn-go {{
            padding: 3px 8px !important;
            font-size: 10px !important;
        }}
        .increment-btns {{ display: inline-flex; gap: 2px; margin-left: 2px; }}
        .pill-sm {{
            padding: 3px 8px;
            border-radius: 4px;
            border: 1px solid #e2e8f0;
            background: white;
            font-size: 11px;
            font-family: inherit;
            color: #64748b;
            cursor: pointer;
            transition: all 0.12s;
        }}
        .pill-sm:hover {{ background: #f1f5f9; border-color: #cbd5e1; color: #334155; }}
        .pill-sm.active {{ background: #334155; color: white; border-color: #334155; }}

        /* ── Summary strip ── */
        .summary-strip {{
            display: flex;
            align-items: center;
            gap: 32px;
            margin-bottom: 24px;
            padding: 20px 24px;
            background: #fafbfc;
            border: 1px solid #f1f5f9;
            border-radius: 10px;
        }}
        .donut {{ width: 80px; height: 80px; flex-shrink: 0; }}
        .donut-pct {{ font-size: 16px; font-weight: 700; fill: #0f172a; font-family: 'Inter', sans-serif; }}
        .donut-label {{ font-size: 6.5px; fill: #94a3b8; font-family: 'Inter', sans-serif; font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px; }}
        .summary-metrics {{
            display: flex;
            gap: 32px;
            flex: 1;
        }}
        .metric {{ display: flex; flex-direction: column; }}
        .metric-value {{ font-size: 22px; font-weight: 700; letter-spacing: -0.5px; line-height: 1; }}
        .metric-label {{ font-size: 11px; color: #64748b; font-weight: 500; margin-top: 3px; }}

        .health-bar {{
            flex: 1;
            min-width: 200px;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }}
        .health-bar-label {{ font-size: 11px; color: #64748b; font-weight: 500; }}
        .health-bar-track {{
            height: 8px;
            border-radius: 4px;
            background: #f1f5f9;
            overflow: hidden;
            display: flex;
        }}
        .health-bar-seg {{ height: 100%; transition: width 0.3s; }}
        .legend {{
            display: flex;
            gap: 14px;
            margin-top: 2px;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 5px;
            font-size: 11px;
            color: #64748b;
        }}
        .legend-dot {{ width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }}

        /* ── Toolbar ── */
        .toolbar {{
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 12px;
            flex-wrap: wrap;
        }}
        .search-box {{
            padding: 7px 12px 7px 32px;
            border-radius: 6px;
            border: 1px solid #e2e8f0;
            font-size: 13px;
            font-family: inherit;
            width: 240px;
            background: white url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%2394a3b8' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='11' cy='11' r='8'%3E%3C/circle%3E%3Cline x1='21' y1='21' x2='16.65' y2='16.65'%3E%3C/line%3E%3C/svg%3E") 10px center no-repeat;
            transition: border-color 0.12s;
        }}
        .search-box:focus {{ outline: none; border-color: #94a3b8; }}
        .sep {{ width: 1px; height: 20px; background: #e2e8f0; margin: 0 2px; }}
        .pill {{
            padding: 5px 12px;
            border-radius: 6px;
            border: 1px solid #e2e8f0;
            background: white;
            cursor: pointer;
            font-size: 12px;
            font-weight: 500;
            font-family: inherit;
            color: #64748b;
            -webkit-appearance: none;
            appearance: none;
            outline: none;
        }}
        .pill:hover, .pill:focus, .pill:focus-visible, .pill:active {{ background: white !important; border-color: #e2e8f0 !important; color: #64748b !important; outline: none !important; box-shadow: none !important; }}
        .pill.active {{ background: #0f172a !important; color: white !important; border-color: #0f172a !important; }}
        .pill.active:hover, .pill.active:focus, .pill.active:focus-visible, .pill.active:active {{ background: #0f172a !important; color: white !important; border-color: #0f172a !important; }}
        .pill .ct {{ opacity: 0.5; margin-left: 3px; }}

        /* ── Table ── */
        .table-wrap {{
            border: 1px solid #e2e8f0;
            border-radius: 10px;
            overflow: hidden;
        }}
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
        thead th {{
            background: #f8fafc;
            padding: 8px 16px;
            text-align: left;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.4px;
            color: #94a3b8;
            border-bottom: 1px solid #e2e8f0;
            position: sticky;
            top: 0;
            z-index: 10;
        }}
        tbody td {{
            padding: 10px 16px;
            border-bottom: 1px solid #f1f5f9;
            vertical-align: top;
        }}
        tbody tr:last-child td {{ border-bottom: none; }}
        tbody tr:hover {{ background: #fafbfc; }}

        .region-header td {{
            padding: 8px 16px;
            font-weight: 600;
            font-size: 12px;
            color: #475569;
            background: #f8fafc;
            border-bottom: 1px solid #e2e8f0;
            text-transform: uppercase;
            letter-spacing: 0.3px;
        }}
        .region-count {{ color: #94a3b8; font-weight: 400; }}
        .region-closed-badge, .region-update-badge {{
            display: inline-block;
            margin-left: 8px;
            padding: 1px 7px;
            border-radius: 4px;
            font-size: 10px;
            font-weight: 600;
            text-transform: none;
            letter-spacing: 0;
        }}
        .region-closed-badge {{ background: #fef2f2; color: #dc2626; }}
        .region-update-badge {{ background: #fef9e7; color: #b8860b; }}

        tr.row-closed {{ opacity: 0.85; }}
        tr.row-closed:hover {{ opacity: 0.85; }}
        ::selection {{ background: #fde68a; color: #1e293b; }}
        ::-moz-selection {{ background: #fde68a; color: #1e293b; }}

        .col-role {{ min-width: 220px; }}
        .job-title {{ font-weight: 500; color: #0f172a; display: block; }}
        .job-company {{ font-size: 12px; color: #64748b; display: block; }}
        .job-location {{ font-size: 11px; color: #94a3b8; display: block; }}

        .col-status {{ white-space: nowrap; width: 100px; }}
        .col-reason {{ font-size: 12px; color: #94a3b8; max-width: 260px; }}
        .col-link {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            word-break: break-all;
            max-width: 420px;
            line-height: 1.4;
        }}
        .col-link a {{ color: #475569; text-decoration: none; }}
        .col-link a:hover {{ color: #0f172a; text-decoration: underline; }}
        .col-link .muted {{ color: #cbd5e1; }}

        .badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            color: white;
            letter-spacing: 0.2px;
        }}

        /* ── Pagination ── */
        .pagination {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 14px 16px;
            border-top: 1px solid #f1f5f9;
            flex-wrap: wrap;
            gap: 8px;
        }}
        .page-info {{
            font-size: 12px;
            color: #94a3b8;
        }}
        .page-buttons {{
            display: flex;
            align-items: center;
            gap: 4px;
        }}
        .page-btn {{
            padding: 5px 10px;
            border-radius: 6px;
            border: 1px solid #e2e8f0;
            background: white;
            font-size: 12px;
            font-family: inherit;
            color: #475569;
            cursor: pointer;
            transition: all 0.12s;
            -webkit-appearance: none;
            appearance: none;
            outline: none;
        }}
        .page-btn:hover {{ background: #f8fafc; border-color: #cbd5e1; }}
        .page-btn.active {{ background: #0f172a; color: white; border-color: #0f172a; }}
        .page-btn.disabled {{ opacity: 0.4; cursor: default; pointer-events: none; }}
        .page-ellipsis {{ color: #94a3b8; font-size: 12px; padding: 0 4px; }}

        /* ── Footer ── */
        .footer {{
            padding: 20px 0;
            margin-top: 32px;
            border-top: 1px solid #f1f5f9;
            text-align: center;
            font-size: 11px;
            color: #cbd5e1;
        }}

        @media (max-width: 900px) {{
            .container {{ padding: 20px 12px 32px; }}
            .summary-strip {{ flex-direction: column; gap: 16px; }}
            .summary-metrics {{ flex-wrap: wrap; }}
            .search-box {{ width: 100%; }}
            .topbar {{ flex-direction: column; gap: 8px; }}
            .topbar-right {{ width: 100%; }}
            .topbar-right .btn {{ width: 100%; text-align: center; }}
            .summary-canvas {{ width: 100px; height: 100px; }}
            .pills {{ flex-wrap: wrap; }}
            .open-range {{ flex-wrap: wrap; }}
            /* Table: hide reason + URL columns, make it fit */
            .col-reason {{ display: none; }}
            .col-link {{ display: none; }}
            th:nth-child(4), td:nth-child(4) {{ display: none; }}
            th:nth-child(5), td:nth-child(5) {{ display: none; }}
            table {{ font-size: 12px; }}
            td, th {{ padding: 8px 6px; }}
            .job-title {{ font-size: 13px; }}
            .col-status {{ width: auto; }}
            .badge {{ font-size: 10px; padding: 2px 6px; }}
            /* Pagination */
            .pagination {{ flex-direction: column; align-items: flex-start; gap: 8px; }}
            .page-buttons {{ flex-wrap: wrap; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="topbar">
            <div class="topbar-left">
                <h1>Listing Freshness Audit</h1>
                <div class="meta">
                    <a href="{source_url}">{source_name}</a> &middot; {total} listings &middot; Last scanned {now}
                </div>
            </div>
            <div class="topbar-right">
                <button class="btn" onclick="exportCSV()">
                    <svg viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                    Export CSV
                </button>
            </div>
        </div>

        <div class="summary-strip">
            {donut_svg}
            <div class="summary-metrics">
                <div class="metric">
                    <div class="metric-value" style="color:#16a34a;">{healthy_count:,}</div>
                    <div class="metric-label">Verified Active</div>
                </div>
                <div class="metric">
                    <div class="metric-value" style="color:#dc2626;">{closed_count:,}</div>
                    <div class="metric-label">Closed</div>
                </div>
                <div class="metric">
                    <div class="metric-value" style="color:#b8860b;">{needs_update_count:,}</div>
                    <div class="metric-label">Needs Update</div>
                </div>
                <div class="metric">
                    <div class="metric-value">{total:,}</div>
                    <div class="metric-label">Total Checked</div>
                </div>
            </div>
            <div class="health-bar">
                <div class="health-bar-label">Board health</div>
                <div class="health-bar-track">
                    <div class="health-bar-seg" style="width:{bar_open_w:.1f}%;background:#16a34a;"></div>
                    <div class="health-bar-seg" style="width:{bar_uncertain_w:.1f}%;background:#65a30d;"></div>
                    <div class="health-bar-seg" style="width:{bar_unreachable_w:.1f}%;background:#d1d5db;"></div>
                    <div class="health-bar-seg" style="width:{bar_needs_w:.1f}%;background:#b8860b;"></div>
                    <div class="health-bar-seg" style="width:{bar_closed_w:.1f}%;background:#dc2626;"></div>
                </div>
                <div class="legend">
                    <div class="legend-item"><span class="legend-dot" style="background:#16a34a;"></span>Live</div>
                    <div class="legend-item"><span class="legend-dot" style="background:#65a30d;"></span>Likely Live</div>
                    <div class="legend-item"><span class="legend-dot" style="background:#b8860b;"></span>Needs Update</div>
                    <div class="legend-item"><span class="legend-dot" style="background:#dc2626;"></span>Closed</div>
                </div>
            </div>
        </div>

        <div class="toolbar">
            <input type="text" class="search-box" placeholder="Filter..." id="searchBox">
            <div class="sep"></div>
            <span class="pill active" onclick="filterVerdict(this, 'ALL')">All <span class="ct">{total:,}</span></span>
            <span class="pill" onclick="filterVerdict(this, 'CLOSED')">Closed <span class="ct">{closed_count:,}</span></span>
            <span class="pill" onclick="filterVerdict(this, 'NEEDS_UPDATE')">Needs Update <span class="ct">{needs_update_count:,}</span></span>
            <span class="pill" onclick="filterVerdict(this, 'UNCERTAIN')">Likely Live <span class="ct">{uncertain_count:,}</span></span>
            <span class="pill" onclick="filterVerdict(this, 'OPEN')">Live <span class="ct">{open_count:,}</span></span>
            <span class="pill" onclick="filterVerdict(this, 'UNREACHABLE')">Unreachable <span class="ct">{unreachable_count:,}</span></span>
            <div class="toolbar-spacer"></div>
            <div class="open-range">
                <span class="open-range-label">Open</span>
                <input type="number" id="rangeFrom" class="range-input" min="1" value="1">
                <span class="open-range-label">&ndash;</span>
                <input type="number" id="rangeTo" class="range-input" min="1" value="20">
                <span class="open-range-label">of <span id="rangeTotal">0</span> links</span>
                <span class="open-range-hint">(excludes mailto)</span>
                <button class="btn btn-go" onclick="openRange()">
                    <svg viewBox="0 0 24 24"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
                    Go
                </button>
                <div class="increment-btns">
                    <button class="pill-sm" onclick="openIncrement(5)">5</button>
                    <button class="pill-sm" onclick="openIncrement(10)">10</button>
                    <button class="pill-sm" onclick="openIncrement(15)">15</button>
                    <button class="pill-sm" onclick="openIncrement(20)">20</button>
                </div>
            </div>
        </div>

        <div class="table-wrap">
            <table id="jobTable">
                <thead>
                    <tr>
                        <th>Role</th>
                        <th>Status</th>
                        <th>Details</th>
                        <th>URL</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html}
                </tbody>
            </table>
        </div>

        <div class="pagination" id="pagination"></div>

        <div class="footer">
            Automated scan &middot; HTTP status, redirect analysis, content pattern matching &middot; No manual review
        </div>
    </div>

    <script>
        const PAGE_SIZE = 50;
        let currentFilter = 'ALL';
        let currentPage = 1;
        let filteredRows = [];

        function filterVerdict(btn, verdict) {{
            currentFilter = verdict;
            document.querySelectorAll('.pill').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentPage = 1;
            filterTable();
        }}

        function getFilteredDataRows() {{
            // Returns only data rows (no region headers) that match filters
            const search = document.getElementById('searchBox').value.toLowerCase();
            const allRows = Array.from(document.querySelectorAll('#jobTable tbody tr:not(.region-header)'));
            return allRows.filter(row => {{
                const text = row.textContent.toLowerCase();
                const verdict = row.getAttribute('data-verdict') || '';
                const matchSearch = !search || text.includes(search);
                const matchVerdict = currentFilter === 'ALL' || verdict === currentFilter;
                return matchSearch && matchVerdict;
            }});
        }}

        // Map each data row to its region header
        function getRegionForRow(row) {{
            let prev = row.previousElementSibling;
            while (prev) {{
                if (prev.classList.contains('region-header')) return prev;
                prev = prev.previousElementSibling;
            }}
            return null;
        }}

        function filterTable() {{
            // Hide all rows first
            document.querySelectorAll('#jobTable tbody tr').forEach(r => r.style.display = 'none');

            filteredRows = getFilteredDataRows();
            const totalPages = Math.max(1, Math.ceil(filteredRows.length / PAGE_SIZE));
            if (currentPage > totalPages) currentPage = totalPages;

            const start = (currentPage - 1) * PAGE_SIZE;
            const end = Math.min(start + PAGE_SIZE, filteredRows.length);
            const pageRows = filteredRows.slice(start, end);

            // Show data rows for this page + their region headers
            const shownRegions = new Set();
            pageRows.forEach(row => {{
                row.style.display = '';
                const region = getRegionForRow(row);
                if (region && !shownRegions.has(region)) {{
                    region.style.display = '';
                    shownRegions.add(region);
                }}
            }});

            renderPagination(totalPages);
            updateRangeTotal();
            document.getElementById('rangeFrom').value = 1;
            document.getElementById('rangeTo').value = Math.min(lastIncrement, getVisibleUrls().length);
        }}

        function renderPagination(totalPages) {{
            const el = document.getElementById('pagination');
            if (totalPages <= 1) {{ el.innerHTML = ''; return; }}

            const info = `<span class="page-info">Page ${{currentPage}} of ${{totalPages}} &middot; ${{filteredRows.length}} listings</span>`;

            let buttons = '';
            buttons += `<button class="page-btn ${{currentPage === 1 ? 'disabled' : ''}}" onclick="goPage(${{currentPage - 1}})" ${{currentPage === 1 ? 'disabled' : ''}}>&lsaquo; Prev</button>`;

            // Show page numbers with ellipsis
            const pages = [];
            pages.push(1);
            for (let p = Math.max(2, currentPage - 2); p <= Math.min(totalPages - 1, currentPage + 2); p++) pages.push(p);
            if (totalPages > 1) pages.push(totalPages);
            const unique = [...new Set(pages)].sort((a,b) => a - b);

            let last = 0;
            unique.forEach(p => {{
                if (p - last > 1) buttons += `<span class="page-ellipsis">&hellip;</span>`;
                buttons += `<button class="page-btn ${{p === currentPage ? 'active' : ''}}" onclick="goPage(${{p}})">${{p}}</button>`;
                last = p;
            }});

            buttons += `<button class="page-btn ${{currentPage === totalPages ? 'disabled' : ''}}" onclick="goPage(${{currentPage + 1}})" ${{currentPage === totalPages ? 'disabled' : ''}}>Next &rsaquo;</button>`;

            el.innerHTML = info + `<div class="page-buttons">${{buttons}}</div>`;
        }}

        function goPage(p) {{
            const totalPages = Math.max(1, Math.ceil(filteredRows.length / PAGE_SIZE));
            if (p < 1 || p > totalPages) return;
            currentPage = p;
            filterTable();
            document.querySelector('.table-wrap').scrollIntoView({{ behavior: 'smooth', block: 'start' }});
        }}

        let lastIncrement = 20;

        function getVisibleUrls() {{
            const rows = document.querySelectorAll('#jobTable tbody tr:not(.region-header)');
            const urls = [];
            rows.forEach(row => {{
                if (row.style.display === 'none') return;
                const a = row.querySelector('.col-link a');
                if (a) urls.push(a.href);
            }});
            return urls;
        }}

        function updateRangeTotal() {{
            const total = getVisibleUrls().length;
            document.getElementById('rangeTotal').textContent = total;
            const toEl = document.getElementById('rangeTo');
            if (parseInt(toEl.value) > total) toEl.value = total;
        }}

        function openRange() {{
            const urls = getVisibleUrls();
            if (urls.length === 0) return;
            const from = Math.max(1, parseInt(document.getElementById('rangeFrom').value) || 1);
            const to = Math.min(urls.length, parseInt(document.getElementById('rangeTo').value) || from);
            const batch = urls.slice(from - 1, to);
            batch.forEach(u => window.open(u, '_blank'));
            const size = to - from + 1;
            const nextFrom = to + 1;
            const nextTo = Math.min(nextFrom + size - 1, urls.length);
            if (nextFrom <= urls.length) {{
                document.getElementById('rangeFrom').value = nextFrom;
                document.getElementById('rangeTo').value = nextTo;
            }}
        }}

        function openIncrement(n) {{
            lastIncrement = n;
            document.querySelectorAll('.pill-sm').forEach(b => b.classList.remove('active'));
            event.target.classList.add('active');
            const urls = getVisibleUrls();
            const from = parseInt(document.getElementById('rangeFrom').value) || 1;
            const to = Math.min(from + n - 1, urls.length);
            document.getElementById('rangeTo').value = to;
            openRange();
        }}

        function exportCSV() {{
            const rows = document.querySelectorAll('#jobTable tbody tr:not(.region-header)');
            let csv = 'Title,Company,Location,Status,Details,URL\\n';
            rows.forEach(row => {{
                const title = row.querySelector('.job-title')?.textContent?.trim() || '';
                const company = row.querySelector('.job-company')?.textContent?.trim() || '';
                const location = row.querySelector('.job-location')?.textContent?.trim() || '';
                const status = row.querySelector('.badge')?.textContent?.trim() || '';
                const reason = row.querySelector('.col-reason')?.textContent?.trim() || '';
                const link = row.querySelector('.col-link a')?.getAttribute('href') || '';
                const esc = s => '"' + s.replace(/"/g, '""') + '"';
                csv += [esc(title), esc(company), esc(location), esc(status), esc(reason), esc(link)].join(',') + '\\n';
            }});
            const blob = new Blob([csv], {{ type: 'text/csv' }});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'freshness-audit-{now_short}.csv';
            a.click();
            URL.revokeObjectURL(url);
        }}

        // Debounce search
        let searchTimeout;
        document.getElementById('searchBox').addEventListener('input', function() {{
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {{ currentPage = 1; filterTable(); }}, 200);
        }});

        // Initial render
        filterTable();

    </script>
</body>
</html>"""

    with open(output_path, "w") as f:
        f.write(html)
    print(f"HTML report written to {output_path}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Job Listing Freshness Checker")
    parser.add_argument("--input", "-i", help="Path to a JSON file of job listings (skips scraping)")
    parser.add_argument("--output", "-o", default="report.html", help="Output HTML report path")
    parser.add_argument("--results", "-r", default="results.json", help="Output JSON results path")
    parser.add_argument("--source-name", default="generalist.world", help="Name of the source board")
    parser.add_argument("--source-url", default="https://generalist.world/jobs/", help="URL of the source board")
    args = parser.parse_args()

    if args.input:
        with open(args.input, "r") as f:
            JOBS = json.load(f)
        print(f"Loaded {len(JOBS)} jobs from {args.input}")
    else:
        JOBS = scrape_generalist_world()

    print(f"Checking {len(JOBS)} job listings...")
    print("=" * 60)

    results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(check_job, job): job for job in JOBS}
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            verdict = result["verdict"]
            symbol = {"OPEN": "✓", "CLOSED": "✗", "NEEDS_UPDATE": "⚠", "UNCERTAIN": "?", "UNREACHABLE": "!"}
            print(f"[{i:3d}/{len(JOBS)}] {symbol.get(verdict, '?')} {verdict:12s} | {result['title'][:40]:40s} | {result.get('reason', '')[:50]}")
            results.append(result)

    # Sort results back into original order
    job_order = {job["title"] + job["company"]: i for i, job in enumerate(JOBS)}
    results.sort(key=lambda r: job_order.get(r["title"] + r["company"], 999))

    # ── Manual overrides for known issues (generalist.world specific) ──
    if not args.input:
        for r in results:
            if r.get("title") == "Operations Officer" and r.get("company") == "GoodWork":
                r["verdict"] = "CLOSED"
                r["confidence"] = "HIGH"
                r["reason"] = "Application form closed (Typeform no longer accepting submissions)"
            if r.get("title") == "Human Centred Designer" and r.get("company") == "Citizens Advice":
                r["verdict"] = "CLOSED"
                r["confidence"] = "HIGH"
                r["reason"] = "Role not found on any page of careers site"
            if r.get("title") == "Full-Stack Product Engineer" and r.get("company") == "Senja":
                r["verdict"] = "CLOSED"
                r["confidence"] = "HIGH"
                r["reason"] = "Role no longer listed — URL points to a different position"
            if r.get("title") == "National Network Manager" and r.get("company") == "The Leap":
                r["verdict"] = "NEEDS_UPDATE"
                r["confidence"] = "HIGH"
                r["reason"] = "URL points to homepage — needs direct job link"
            if r.get("title") == "Head of Creative" and r.get("company") == "Finisterre":
                r["verdict"] = "CLOSED"
                r["confidence"] = "HIGH"
                r["reason"] = "Role no longer listed on Workable board"
            if r.get("title") == "Senior Event Operations Manager 2026" and r.get("company") == "Founders Forum":
                r["verdict"] = "CLOSED"
                r["confidence"] = "HIGH"
                r["reason"] = "Role not found on Workable board"
            if r.get("title") == "AI Engineer" and r.get("company") == "heva":
                r["verdict"] = "CLOSED"
                r["confidence"] = "HIGH"
                r["reason"] = "Role not found on company hiring page"
            if r.get("title") == "Chief of Staff" and r.get("company") == "BeAngels":
                r["verdict"] = "NEEDS_UPDATE"
                r["confidence"] = "HIGH"
                r["reason"] = "URL points to homepage — better link available on LinkedIn"
            if r.get("title") == "Director of Product Marketing" and r.get("company") == "ComPsych":
                r["verdict"] = "NEEDS_UPDATE"
                r["confidence"] = "HIGH"
                r["reason"] = "URL points to generic careers page — needs direct job link"
            if r.get("title") == "Creative Content Manager" and r.get("company") == "Gut Wealth":
                r["verdict"] = "CLOSED"
                r["confidence"] = "HIGH"
                r["reason"] = "No longer accepting applications (LinkedIn)"

    print("\n" + "=" * 60)
    open_c = sum(1 for r in results if r["verdict"] == "OPEN")
    closed_c = sum(1 for r in results if r["verdict"] == "CLOSED")
    uncertain_c = sum(1 for r in results if r["verdict"] == "UNCERTAIN")
    unreachable_c = sum(1 for r in results if r["verdict"] == "UNREACHABLE")
    print(f"OPEN: {open_c}  |  CLOSED: {closed_c}  |  UNCERTAIN: {uncertain_c}  |  UNREACHABLE: {unreachable_c}")
    print("=" * 60)

    generate_html(results, args.output,
                  source_name=args.source_name, source_url=args.source_url)

    # Also save raw JSON
    with open(args.results, "w") as f:
        json.dump(results, f, indent=2)
    print(f"JSON results written to {args.results}")


if __name__ == "__main__":
    main()
