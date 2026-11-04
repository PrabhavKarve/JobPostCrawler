#!/usr/bin/env python3
"""
ashby_watch.py - daily "new jobs" tracker for Ashby boards (filtered edition).

Reads Ashby job-board slugs from seeds.txt, pulls each board's public posting
API IN PARALLEL, keeps only roles that (a) match your target role types and
(b) are located in the US, diffs against the previous run in state.json, and
reports what's new.

No third-party dependencies. Python 3.8+.

Usage:
    python3 ashby_watch.py            # normal daily run
    python3 ashby_watch.py --selftest # offline check of filters + diff logic

First run establishes a baseline silently (no giant dump). From the second run
on you get the true day-over-day delta of *matching* roles.

EDIT YOUR FILTERS in the CONFIG block below. NOTE: if you change the filters,
delete state.json once to re-baseline, otherwise the diff compares apples to
oranges for one run.
"""

import datetime
import hashlib
import html as html_lib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================ CONFIG (edit me) ============================

# Keep a role only if its title contains one of these phrases (lowercase).
ROLE_KEYWORDS = [
    "software engineer",
    "ai engineer",
    "data engineer",
    "gtm engineer",
    "forward deployed engineer",
    "founding engineer",
    "fullstack",
    "full stack",
    "data analytics",
    "data operations analyst",
    "data analyst",
    "data scientist",
]

US_ONLY = True       # keep only roles located in the United States
MAX_WORKERS = 12     # parallel board fetches; raise for speed, lower if rate-limited

# Drop roles whose title carries any of these seniority/level markers
# (whole-word match). Base-level roles are what survive.
SENIORITY_EXCLUDE = [
    "senior", "sr", "staff", "principal", "lead", "leadership",
    "director", "head", "vp", "distinguished", "intern", "internship",
    "manager", "management",
]
# Titles containing any of these are kept even if they look senior
# (e.g. "Senior Founding Engineer" stays, because founding roles are senior
# by nature and you asked for them explicitly).
SENIORITY_EXEMPT = ["founding engineer"]

# Numbered levels II and above (roman numeral or digit) are treated as
# senior-equivalent and dropped, e.g. "Software Engineer III"/"...3".
# Level I / 1 or unnumbered titles are kept.
LEVEL_EXCLUDE = {"ii", "iii", "iv", "v", "2", "3", "4", "5"}

# =========================================================================

API = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
STATE_FILE = "state.json"
SEEDS_FILE = "seeds.txt"
USER_AGENT = "ashby-watch/1.0 (personal job tracker)"
TIMEOUT = 20         # seconds per request


# ----------------------------- fetching ----------------------------------

def fetch_board(slug):
    """Return the list of job dicts for one board, or raise on network error."""
    url = API.format(slug=urllib.parse.quote(slug))
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = json.load(r)
    return data.get("jobs", []) or []


def fetch_all(slugs):
    """Fetch every board concurrently. Returns (results dict, errors list)."""
    results, errors = {}, []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(fetch_board, s): s for s in slugs}
        for fut in as_completed(futs):
            slug = futs[fut]
            try:
                results[slug] = fut.result()
            except urllib.error.HTTPError as e:
                if e.code != 404:  # dead/renamed board -> skip quietly
                    errors.append((slug, f"HTTP {e.code}"))
            except Exception as e:  # noqa: BLE001 - one bad board shouldn't stop the run
                errors.append((slug, str(e)))
    return results, errors


# ----------------------------- filters -----------------------------------

def title_matches(title):
    t = re.sub(r"[-\s]+", " ", (title or "").lower())
    if not any(kw in t for kw in ROLE_KEYWORDS):
        return False
    if any(ex in t for ex in SENIORITY_EXEMPT):  # founding roles bypass seniority
        return True
    tokens = set(re.findall(r"[a-z0-9]+", t))
    if any(ex in tok for tok in tokens for ex in SENIORITY_EXCLUDE):
        return False
    if tokens & LEVEL_EXCLUDE:
        return False
    return True


_US_COUNTRY_EXACT = {
    "us", "usa", "u.s.", "u.s.a.", "united states",
    "united states of america", "united states (us)",
}
_US_COUNTRY_SUBSTR = ("united states of america", "united states", "usa")
_US_STATE_CODES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il",
    "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt",
    "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri",
    "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy", "dc",
}
# Full state names EXCEPT "georgia" (collides with the country Georgia) and
# "washington" is kept (also the city/DC, all US anyway).
_US_STATE_NAMES = (
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "hawaii", "idaho", "illinois",
    "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland",
    "massachusetts", "michigan", "minnesota", "mississippi", "missouri",
    "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico",
    "new york", "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee",
    "texas", "utah", "vermont", "virginia", "washington", "west virginia",
    "wisconsin", "wyoming",
)
_US_CITIES = (
    "new york", "san francisco", "los angeles", "seattle", "boston", "chicago",
    "austin", "denver", "atlanta", "miami", "dallas", "houston", "philadelphia",
    "san diego", "portland", "salt lake city", "brooklyn", "palo alto",
    "mountain view", "san jose",
)
_STATE_CODE_RE = re.compile(r",\s*([a-z]{2})(?![a-z])")


def _country_of(addr):
    """Pull a country out of either address shape Ashby uses."""
    if not isinstance(addr, dict):
        return None
    if addr.get("addressCountry"):
        return addr["addressCountry"]
    pa = addr.get("postalAddress")
    if isinstance(pa, dict) and pa.get("addressCountry"):
        return pa["addressCountry"]
    return None


def _text_is_us(s):
    t = re.sub(r"\s+", " ", (s or "").strip().lower())
    if not t:
        return False
    if any(sub in t for sub in _US_COUNTRY_SUBSTR):
        return True
    if any(m.group(1) in _US_STATE_CODES for m in _STATE_CODE_RE.finditer(t)):
        return True
    if any(name in t for name in _US_STATE_NAMES):
        return True
    if any(city in t for city in _US_CITIES):
        return True
    if "us" in re.findall(r"[a-z]+", t):  # word-boundary "us", e.g. "Remote - US"
        return True
    return False


def _location_pairs(job):
    pairs = [(job.get("location", ""), _country_of(job.get("address")))]
    for s in job.get("secondaryLocations") or []:
        if isinstance(s, dict):
            pairs.append((s.get("location", ""), _country_of(s.get("address"))))
    return pairs


def is_us(job):
    """US if ANY of the job's locations is US. Trust structured country first."""
    for loc, country in _location_pairs(job):
        if country:
            if country.strip().lower() in _US_COUNTRY_EXACT:
                return True
            continue  # explicit non-US country -> don't fuzzy-match this one
        if _text_is_us(loc):
            return True
    return False


def keep(job):
    if job.get("isListed") is False:
        return False
    if not title_matches(job.get("title", "")):
        return False
    if US_ONLY and not is_us(job):
        return False
    return True


# ----------------------------- state/diff ---------------------------------

def job_key(slug, job):
    """Stable, case-insensitive id so solvd/Solvd collapse to one."""
    for k in ("id", "jobUrl", "applyUrl"):
        v = job.get(k)
        if v:
            return f"{slug}::{v}".lower()
    raw = "|".join(str(job.get(f, "")) for f in ("title", "location", "department", "team"))
    return (f"{slug}::" + hashlib.sha1(raw.encode("utf-8")).hexdigest()).lower()


def normalize(slug, job):
    return {
        "company": slug,
        "title": job.get("title", ""),
        "location": job.get("location", ""),
        "department": job.get("department", ""),
        "url": job.get("jobUrl") or job.get("applyUrl") or "",
        "publishedAt": job.get("publishedAt", ""),
    }


def load_seeds():
    if not os.path.exists(SEEDS_FILE):
        sys.exit(f"No {SEEDS_FILE} found. Run harvest.py first, or add slugs by hand.")
    seen, out = set(), []
    with open(SEEDS_FILE) as f:
        for ln in f:
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            s = s.lower()  # case-insensitive dedupe (solvd == Solvd)
            if s not in seen:
                seen.add(s)
                out.append(s)
    return out


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)  # atomic


def compute_report(prev, current, n_boards, errors):
    new_keys = [k for k in current if k not in prev]
    removed_keys = [k for k in prev if k not in current]
    now = datetime.datetime.now()
    return {
        "date": now.date().isoformat(),
        "time": now.strftime("%I:%M %p"),  # 12-hour, e.g. 02:45 PM
        "boards_checked": n_boards,
        "boards_failed": len(errors),
        "total_open": len(current),
        "new": [current[k] for k in new_keys],
        "removed": [prev[k] for k in removed_keys],
    }


def print_report(report):
    print(f"=== Ashby watch {report['date']} ===")
    print(
        f"Boards checked: {report['boards_checked']}  "
        f"failed: {report['boards_failed']}  "
        f"matching open roles: {report['total_open']}"
    )
    print(f"NEW since last run: {len(report['new'])}\n")
    for j in report["new"]:
        loc = f" - {j['location']}" if j["location"] else ""
        print(f"  + [{j['company']}] {j['title']}{loc}")
        if j["url"]:
            print(f"    {j['url']}")
    if report["removed"]:
        print(f"\nClosed/removed since last run: {len(report['removed'])}")


def write_html(report, path):
    """Write the report's roles as clickable links in a simple HTML page."""
    rows = sorted(report["new"], key=lambda j: (j["company"].lower(), j["title"].lower()))
    out = [
        "<!doctype html>",
        "<meta charset='utf-8'>",
        f"<title>Ashby new jobs {report['date']}</title>",
        "<style>"
        "body{font-family:system-ui,Arial,sans-serif;max-width:820px;margin:2rem auto;"
        "padding:0 1rem;line-height:1.5;color:#111}"
        "h1{font-size:1.2rem}li{margin:.4rem 0}"
        ".meta{color:#666;font-size:.88rem}"
        "a{color:#1255cc;text-decoration:none}a:hover{text-decoration:underline}"
        "</style>",
        f"<h1>Ashby new matching roles &mdash; {report['date']} {report.get('time', '')} ({len(rows)})</h1>",
    ]
    if rows:
        out.append("<ul>")
        for j in rows:
            title = html_lib.escape(j["title"] or "(untitled)")
            url = html_lib.escape(j["url"] or "#", quote=True)
            bits = [html_lib.escape(x) for x in (j["company"], j["location"]) if x]
            meta = " &middot; ".join(bits)
            out.append(f"<li><a href=\"{url}\" target=\"_blank\" rel=\"noopener\">{title}</a>"
                       f"<div class='meta'>{meta}</div></li>")
        out.append("</ul>")
    else:
        out.append("<p>No new roles since last run.</p>")
    with open(path, "w") as f:
        f.write("\n".join(out))


def _write_artifacts(report, errors):
    with open("latest.json", "w") as f:
        json.dump(report, f, indent=2)
    write_html(report, "latest.html")
    if errors:
        with open(f"errors-{report['date']}.log", "w") as f:
            for slug, msg in errors:
                f.write(f"{slug}\t{msg}\n")


def run():
    seeds = load_seeds()
    prev = load_state()
    boards, errors = fetch_all(seeds)

    current = {}
    for slug, jobs in boards.items():
        for job in jobs:
            if keep(job):
                current[job_key(slug, job)] = normalize(slug, job)

    report = compute_report(prev, current, len(seeds), errors)

    if not prev:  # first run -> silent baseline
        print(f"=== Ashby watch {report['date']} (baseline) ===")
        print(f"Boards checked: {report['boards_checked']}  failed: {report['boards_failed']}")
        print(f"Matching US open roles right now: {report['total_open']}")
        print("Baseline saved. New matching roles will be reported from the next run.")
    else:
        print_report(report)

    save_state(current)
    _write_artifacts(report, errors)


# ----------------------------- selftest -----------------------------------

def selftest():
    # role filter (base-level only; seniority dropped, founding exempt)
    assert title_matches("Software Engineer - AI Platform")
    assert title_matches("Applied AI Engineer")
    assert title_matches("Software Engineer (Mid-Level)")
    assert title_matches("Software Engineer II")
    assert title_matches("Data Engineer")
    assert title_matches("GTM Engineer")
    assert title_matches("Senior Founding Engineer")          # exempt: founding stays
    assert not title_matches("Senior Software Engineer")
    assert not title_matches("Staff Software Engineer")
    assert not title_matches("Principal Software Engineer")
    assert not title_matches("Software Engineer (Senior-Level)")
    assert not title_matches("Embedded Software Engineering Intern")
    assert not title_matches("Manual QA Engineer")
    assert not title_matches("Senior Data Scientist")
    assert not title_matches("VP of Sales - Medicaid")

    # location filter
    assert is_us({"location": "Houston, TX"})
    assert is_us({"location": "New York City"})
    assert is_us({"location": "Remote - US"})
    assert is_us({"location": "Remote (US or Canada)"})
    assert is_us({"location": "Cloud Engineer - USA"})
    assert is_us({"location": "Berlin", "secondaryLocations": [{"location": "San Francisco, CA"}]})
    assert is_us({"location": "x", "address": {"postalAddress": {"addressCountry": "USA"}}})
    assert not is_us({"location": "London"})
    assert not is_us({"location": "Argentina"})
    assert not is_us({"location": "Georgia"})  # country, not the US state
    assert not is_us({"location": "Tbilisi", "address": {"addressCountry": "Georgia"}})
    assert not is_us({"location": "Austria"})

    # combined keep()
    assert keep({"title": "Software Engineer (Java/GraphQL)", "location": "Remote - US"})
    assert not keep({"title": "Senior Software Engineer (Java/GraphQL)", "location": "Remote - US"})
    assert not keep({"title": "Software Engineer", "location": "Poland"})
    assert not keep({"title": "Manual QA Engineer", "location": "Houston, TX"})

    # casing dedupe via key
    j = {"jobUrl": "https://jobs.ashbyhq.com/X/abc"}
    assert job_key("solvd", j) == job_key("Solvd", j)

    # diff
    prev = {"a": {"company": "x", "title": "Old", "location": "", "department": "", "url": "", "publishedAt": ""}}
    cur = {"b": {"company": "x", "title": "New SWE", "location": "Remote - US", "department": "", "url": "", "publishedAt": ""}}
    rep = compute_report(prev, cur, 1, [])
    assert len(rep["new"]) == 1 and rep["new"][0]["title"] == "New SWE"
    assert len(rep["removed"]) == 1
    print("selftest: PASS")


def list_current(days=None):
    """Print every matching role currently saved in state.json (no fetch)."""
    state = load_state()
    if not state:
        print("state.json is empty - run ashby_watch.py once first.")
        return

    rows = list(state.values())
    if days is not None:
        cutoff = datetime.date.today() - datetime.timedelta(days=days)
        kept = []
        for r in rows:
            ts = (r.get("publishedAt") or "")[:10]
            try:
                if datetime.date.fromisoformat(ts) >= cutoff:
                    kept.append(r)
            except ValueError:
                pass  # no/!bad date -> drop when a day window is requested
        rows = kept

    rows.sort(key=lambda r: (r["company"].lower(), r["title"].lower()))
    print(f"{len(rows)} matching open role(s)"
          + (f" posted in the last {days} day(s)" if days is not None else "")
          + ":\n")
    current_company = None
    for r in rows:
        if r["company"] != current_company:
            current_company = r["company"]
            print(f"[{current_company}]")
        loc = f" - {r['location']}" if r["location"] else ""
        print(f"  {r['title']}{loc}")
        if r["url"]:
            print(f"    {r['url']}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif "--list" in sys.argv:
        # optional: --days N to show only recently posted roles
        days = None
        if "--days" in sys.argv:
            i = sys.argv.index("--days")
            if i + 1 < len(sys.argv):
                days = int(sys.argv[i + 1])
        list_current(days)
    else:
        run()