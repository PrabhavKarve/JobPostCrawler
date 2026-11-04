#!/usr/bin/env python3
"""
lever_watch.py - daily "new jobs" tracker for Lever boards.

Twin of ashby_watch.py, adapted to Lever's public postings API:
    https://api.lever.co/v0/postings/<slug>?mode=json   (flat JSON array, no auth)

Reads slugs from seeds_lever.txt, fetches each board in parallel, keeps only
roles that match your target role types AND are in the US, diffs against the
previous run in state_lever.json, and writes new roles as clickable links.

Uses its OWN files so it never collides with the Ashby setup:
    seeds_lever.txt, state_lever.json, latest_lever.html, newjobs-lever-DATE.*

No third-party dependencies. Python 3.8+.

Usage:
    python3 lever_watch.py            # normal daily run (first run = silent baseline)
    python3 lever_watch.py --selftest # offline check of filters + diff logic

NOTE: the filter CONFIG below is intentionally identical to ashby_watch.py.
If you change filters in one, change them in the other (or ask to refactor the
shared logic into one module).
"""

import datetime
import hashlib
import html as html_lib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================ CONFIG (edit me) ============================

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

US_ONLY = True
MAX_WORKERS = 12

SENIORITY_EXCLUDE = [
    "senior", "sr", "staff", "principal", "lead", "leadership",
    "director", "head", "vp", "distinguished", "intern", "internship",
    "manager", "management",
]
SENIORITY_EXEMPT = ["founding engineer"]

# Numbered levels II and above (roman numeral or digit) are treated as
# senior-equivalent and dropped, e.g. "Software Engineer III"/"...3".
# Level I / 1 or unnumbered titles are kept.
LEVEL_EXCLUDE = {"ii", "iii", "iv", "v", "2", "3", "4", "5"}

# =========================================================================

API = "https://api.lever.co/v0/postings/{slug}?mode=json"
STATE_FILE = "state_lever.json"
SEEDS_FILE = "seeds_lever.txt"
LATEST_JSON = "latest_lever.json"
LATEST_HTML = "latest_lever.html"
USER_AGENT = "lever-watch/1.0 (personal job tracker)"
TIMEOUT = 20


# ----------------------------- fetching ----------------------------------

def fetch_board(slug):
    """Return the list of posting dicts for one Lever board."""
    url = API.format(slug=urllib.parse.quote(slug))
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = json.load(r)
    return data if isinstance(data, list) else []  # Lever returns a flat array


def fetch_all(slugs):
    results, errors = {}, []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(fetch_board, s): s for s in slugs}
        for fut in as_completed(futs):
            slug = futs[fut]
            try:
                results[slug] = fut.result()
            except urllib.error.HTTPError as e:
                if e.code != 404:  # board not on this instance / gone -> skip quietly
                    errors.append((slug, f"HTTP {e.code}"))
            except Exception as e:  # noqa: BLE001
                errors.append((slug, str(e)))
    return results, errors


# ----------------------------- filters -----------------------------------

def title_matches(title):
    t = re.sub(r"[-\s]+", " ", (title or "").lower())
    if not any(kw in t for kw in ROLE_KEYWORDS):
        return False
    if any(ex in t for ex in SENIORITY_EXEMPT):
        return True
    tokens = set(re.findall(r"[a-z0-9]+", t))
    if any(ex in tok for tok in tokens for ex in SENIORITY_EXCLUDE):
        return False
    if tokens & LEVEL_EXCLUDE:
        return False
    return True


_US_COUNTRY_SUBSTR = ("united states of america", "united states", "usa")
_US_STATE_CODES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il",
    "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt",
    "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri",
    "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy", "dc",
}
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
_STATE_CODE_RE = re.compile(r",\s*([a-z]{2})\s*(?:,|$)")
_NON_US_COUNTRY_NAMES = (
    "canada", "mexico", "peru", "costa rica", "colombia", "argentina", "brazil",
    "chile", "ecuador", "guatemala", "honduras", "el salvador", "nicaragua",
    "panama", "dominican republic", "uruguay", "paraguay", "bolivia", "venezuela",
    "united kingdom", "ireland", "germany", "france", "spain", "italy",
    "portugal", "netherlands", "poland", "india", "philippines", "australia",
    "new zealand", "south africa", "nigeria", "kenya",
)


def _last_segment_is_foreign(t):
    parts = [p.strip() for p in t.split(",") if p.strip()]
    return bool(parts) and parts[-1] in _NON_US_COUNTRY_NAMES


def _text_is_us(s):
    t = re.sub(r"\s+", " ", (s or "").strip().lower())
    if not t:
        return False
    if _last_segment_is_foreign(t):
        return False
    if any(sub in t for sub in _US_COUNTRY_SUBSTR):
        return True
    if any(m.group(1) in _US_STATE_CODES for m in _STATE_CODE_RE.finditer(t)):
        return True
    if any(name in t for name in _US_STATE_NAMES):
        return True
    if any(city in t for city in _US_CITIES):
        return True
    if "us" in re.findall(r"[a-z]+", t):
        return True
    return False


def is_us(job):
    """Lever: trust a 2-letter country code if present, else scan location text."""
    c = (job.get("country") or "").strip().lower()
    if c in ("us", "usa"):
        return True
    cats = job.get("categories") or {}
    locs = []
    if cats.get("location"):
        locs.append(cats["location"])
    for loc in cats.get("allLocations") or []:
        locs.append(loc)
    return any(_text_is_us(s) for s in locs)


def keep(job):
    if not title_matches(job.get("text", "")):
        return False
    if US_ONLY and not is_us(job):
        return False
    return True


# ----------------------------- state/diff ---------------------------------

def job_key(slug, job):
    for k in ("id", "hostedUrl", "applyUrl"):
        v = job.get(k)
        if v:
            return f"{slug}::{v}".lower()
    cats = job.get("categories") or {}
    raw = "|".join([job.get("text", ""), cats.get("location", ""), cats.get("team", "")])
    return (f"{slug}::" + hashlib.sha1(raw.encode("utf-8")).hexdigest()).lower()


def normalize(slug, job):
    cats = job.get("categories") or {}
    published = ""
    ca = job.get("createdAt")
    if isinstance(ca, (int, float)):
        published = datetime.datetime.utcfromtimestamp(ca / 1000).date().isoformat()
    return {
        "company": slug,
        "title": job.get("text", ""),
        "location": cats.get("location", ""),
        "department": cats.get("department", "") or cats.get("team", ""),
        "url": job.get("hostedUrl") or job.get("applyUrl") or "",
        "publishedAt": published,
    }


def load_seeds():
    if not os.path.exists(SEEDS_FILE):
        sys.exit(f"No {SEEDS_FILE} found. Run lever_harvest.py first, or add slugs by hand.")
    seen, out = set(), []
    with open(SEEDS_FILE) as f:
        for ln in f:
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            s = s.lower()
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
    os.replace(tmp, STATE_FILE)


def compute_report(prev, current, n_boards, errors):
    new_keys = [k for k in current if k not in prev]
    removed_keys = [k for k in prev if k not in current]
    now = datetime.datetime.now()
    return {
        "date": now.date().isoformat(),
        "time": now.strftime("%I:%M %p"),
        "boards_checked": n_boards,
        "boards_failed": len(errors),
        "total_open": len(current),
        "new": [current[k] for k in new_keys],
        "removed": [prev[k] for k in removed_keys],
    }


def print_report(report):
    print(f"=== Lever watch {report['date']} {report['time']} ===")
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
    rows = sorted(report["new"], key=lambda j: (j["company"].lower(), j["title"].lower()))
    out = [
        "<!doctype html>",
        "<meta charset='utf-8'>",
        f"<title>Lever new jobs {report['date']}</title>",
        "<style>"
        "body{font-family:system-ui,Arial,sans-serif;max-width:820px;margin:2rem auto;"
        "padding:0 1rem;line-height:1.5;color:#111}"
        "h1{font-size:1.2rem}li{margin:.4rem 0}"
        ".meta{color:#666;font-size:.88rem}"
        "a{color:#1255cc;text-decoration:none}a:hover{text-decoration:underline}"
        "</style>",
        f"<h1>Lever new matching roles &mdash; {report['date']} {report.get('time', '')} ({len(rows)})</h1>",
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
    with open(LATEST_JSON, "w") as f:
        json.dump(report, f, indent=2)
    write_html(report, LATEST_HTML)
    if errors:
        with open(f"errors-lever-{report['date']}.log", "w") as f:
            for slug, msg in errors:
                f.write(f"{slug}\t{msg}\n")


def run():
    seeds = load_seeds()
    prev = load_state()
    boards, errors = fetch_all(seeds)

    current = {}
    for slug, postings in boards.items():
        for job in postings:
            if keep(job):
                current[job_key(slug, job)] = normalize(slug, job)

    report = compute_report(prev, current, len(seeds), errors)

    if not prev:
        print(f"=== Lever watch {report['date']} {report['time']} (baseline) ===")
        print(f"Boards checked: {report['boards_checked']}  failed: {report['boards_failed']}")
        print(f"Matching US open roles right now: {report['total_open']}")
        print("Baseline saved. New matching roles will be reported from the next run.")
    else:
        print_report(report)

    save_state(current)
    _write_artifacts(report, errors)


def selftest():
    # Lever-shaped postings
    j_swe = {"text": "Software Engineer, Backend", "id": "1",
             "categories": {"location": "San Francisco, CA"}, "hostedUrl": "https://jobs.lever.co/x/1"}
    j_sr = {"text": "Senior Software Engineer", "id": "2",
            "categories": {"location": "New York"}, "hostedUrl": "https://jobs.lever.co/x/2"}
    j_intl = {"text": "Software Engineer", "id": "3",
              "categories": {"location": "London"}, "hostedUrl": "https://jobs.lever.co/x/3"}
    j_found = {"text": "Senior Founding Engineer", "id": "4",
               "categories": {"location": "Remote - US"}, "hostedUrl": "https://jobs.lever.co/x/4"}
    j_cc = {"text": "Data Engineer", "id": "5", "country": "US",
            "categories": {"location": "Anywhere"}, "hostedUrl": "https://jobs.lever.co/x/5"}
    j_qa = {"text": "Manual QA Engineer", "id": "6",
            "categories": {"location": "Austin, TX"}, "hostedUrl": "https://jobs.lever.co/x/6"}

    assert keep(j_swe)
    assert not keep(j_sr)        # seniority
    assert not keep(j_intl)      # non-US
    assert keep(j_found)         # founding exemption + US
    assert keep(j_cc)            # country code US
    assert not keep(j_qa)        # role mismatch

    # allLocations path
    assert is_us({"categories": {"location": "Berlin", "allLocations": ["San Francisco, CA"]}})
    assert not is_us({"categories": {"location": "Berlin", "allLocations": ["Paris"]}})

    # normalize maps Lever fields correctly
    n = normalize("acme", j_swe)
    assert n["title"] == "Software Engineer, Backend" and n["location"] == "San Francisco, CA"
    assert n["url"] == "https://jobs.lever.co/x/1"

    # diff
    prev = {"acme::https://jobs.lever.co/x/old": {"company": "acme", "title": "Old", "location": "",
            "department": "", "url": "", "publishedAt": ""}}
    cur = {"acme::https://jobs.lever.co/x/1": normalize("acme", j_swe)}
    rep = compute_report(prev, cur, 1, [])
    assert len(rep["new"]) == 1 and len(rep["removed"]) == 1
    print("selftest: PASS")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        run()