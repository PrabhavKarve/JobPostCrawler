#!/usr/bin/env python3
"""
workable_watch.py - daily "new jobs" tracker for Workable boards.

Twin of the other watchers, adapted to Workable's public widget API:
    https://apply.workable.com/api/v1/widget/accounts/<slug>   ({"jobs":[...]}, no auth)

Keeps only roles that match your target role types AND are in the US, diffs
against state_workable.json, writes new roles as clickable links.

US filtering prefers Workable's structured country_code ("US") when present and
falls back to the text matcher otherwise - so it's the most precise of the set.

Own files, so it never collides with the other ATS setups:
    seeds_workable.txt, state_workable.json,
    latest_workable.html, newjobs-workable-DATE.*

No third-party dependencies. Python 3.8+.

Usage:
    python3 workable_watch.py            # normal run (first = baseline)
    python3 workable_watch.py --selftest # offline check of filters + diff

NOTE: filter CONFIG is identical to the other watchers. Keep them in sync, or
ask to refactor the shared logic into one module.
"""

import datetime
import hashlib
import html as html_lib
import json
import os
import re
import sys
import threading
import time
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
# Workable rate-limits hard (10 requests / 10 seconds), so unlike the other
# ATSes we pace requests globally and keep concurrency low.
MAX_WORKERS = 5
MIN_INTERVAL_SEC = 1.2   # >= ~8 req / 10s, safely under Workable's 10/10s limit
MAX_RETRIES_429 = 4      # retry throttled requests instead of failing them

SENIORITY_EXCLUDE = [
    "senior", "sr", "staff", "principal", "lead", "director",
    "head", "vp", "distinguished", "intern", "internship",
]
SENIORITY_EXEMPT = ["founding engineer"]

# Numbered levels II and above (roman numeral or digit) are treated as
# senior-equivalent and dropped, e.g. "Software Engineer III"/"...3".
# Level I / 1 or unnumbered titles are kept.
LEVEL_EXCLUDE = {"ii", "iii", "iv", "v", "2", "3", "4", "5"}

# =========================================================================

API = "https://apply.workable.com/api/v1/widget/accounts/{slug}"
STATE_FILE = "state_workable.json"
SEEDS_FILE = "seeds_workable.txt"
LATEST_JSON = "latest_workable.json"
LATEST_HTML = "latest_workable.html"
USER_AGENT = "workable-watch/1.0 (personal job tracker)"
TIMEOUT = 20


# ----------------------------- fetching ----------------------------------

_rl_lock = threading.Lock()
_next_start = [0.0]


def _throttle():
    """Global pacing gate: ensures >= MIN_INTERVAL_SEC between request starts."""
    with _rl_lock:
        now = time.monotonic()
        wait = _next_start[0] - now
        if wait > 0:
            time.sleep(wait)
        _next_start[0] = max(now, _next_start[0]) + MIN_INTERVAL_SEC


def fetch_board(slug):
    url = API.format(slug=urllib.parse.quote(slug))
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    for attempt in range(MAX_RETRIES_429 + 1):
        _throttle()
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < MAX_RETRIES_429:
                retry_after = e.headers.get("Retry-After")
                try:
                    delay = float(retry_after)
                except (TypeError, ValueError):
                    delay = MIN_INTERVAL_SEC * (2 ** attempt)  # backoff
                time.sleep(min(delay, 30))
                continue
            raise
    if isinstance(data, dict):
        return data.get("jobs", []) or []
    return data if isinstance(data, list) else []


def fetch_all(slugs):
    """Returns (results, errors, dead). dead = slugs that returned 404."""
    results, errors, dead = {}, [], []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(fetch_board, s): s for s in slugs}
        for fut in as_completed(futs):
            slug = futs[fut]
            try:
                results[slug] = fut.result()
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    dead.append(slug)
                else:
                    errors.append((slug, f"HTTP {e.code}"))
            except Exception as e:  # noqa: BLE001
                errors.append((slug, str(e)))
    return results, errors, dead


# ----------------------------- filters -----------------------------------

def title_matches(title):
    t = re.sub(r"[-\s]+", " ", (title or "").lower())
    if not any(kw in t for kw in ROLE_KEYWORDS):
        return False
    if any(ex in t for ex in SENIORITY_EXEMPT):
        return True
    tokens = set(re.findall(r"[a-z0-9]+", t))
    if tokens & set(SENIORITY_EXCLUDE):
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


def _country_codes(job):
    """Collect any country_code values (handles nested + flat + locations[])."""
    codes = []
    loc = job.get("location")
    if isinstance(loc, dict) and loc.get("country_code"):
        codes.append(loc["country_code"])
    if job.get("country_code"):
        codes.append(job["country_code"])
    for l in job.get("locations") or []:
        if isinstance(l, dict) and l.get("country_code"):
            codes.append(l["country_code"])
    return [c.strip().upper() for c in codes if isinstance(c, str) and c.strip()]


def _location_texts(job):
    texts = []
    loc = job.get("location")
    if isinstance(loc, dict):
        for k in ("location_str", "country", "city", "region", "state"):
            if loc.get(k):
                texts.append(str(loc[k]))
    elif isinstance(loc, str):
        texts.append(loc)
    for k in ("country", "city", "state", "region", "location_str"):
        v = job.get(k)
        if isinstance(v, str) and v:
            texts.append(v)
    for l in job.get("locations") or []:
        if isinstance(l, dict):
            for k in ("country_name", "country", "city", "state_code", "region"):
                if l.get(k):
                    texts.append(str(l[k]))
    return texts


def is_us(job):
    codes = _country_codes(job)
    if codes:                       # trust structured country_code when present
        return "US" in codes
    return any(_text_is_us(t) for t in _location_texts(job))


def keep(job):
    if not title_matches(job.get("title", "")):
        return False
    if US_ONLY and not is_us(job):
        return False
    return True


# ----------------------------- state/diff ---------------------------------

def job_key(slug, job):
    for k in ("shortcode", "id", "url", "shortlink"):
        v = job.get(k)
        if v:
            return f"{slug}::{v}".lower()
    raw = "|".join([job.get("title", ""), _display_location(job)])
    return (f"{slug}::" + hashlib.sha1(raw.encode("utf-8")).hexdigest()).lower()


def _display_location(job):
    loc = job.get("location")
    if isinstance(loc, dict):
        if loc.get("location_str"):
            return loc["location_str"]
        parts = [loc.get("city"), loc.get("region") or loc.get("state"), loc.get("country")]
        s = ", ".join(p for p in parts if p)
        if s:
            return s
    elif isinstance(loc, str) and loc:
        return loc
    parts = [job.get("city"), job.get("state") or job.get("region"), job.get("country")]
    s = ", ".join(p for p in parts if p)
    if s:
        return s
    if job.get("telecommuting") or job.get("remote"):
        return "Remote"
    return ""


def normalize(slug, job):
    dept = job.get("department", "")
    if not dept:
        dh = job.get("department_hierarchy") or []
        if dh and isinstance(dh[-1], dict):
            dept = dh[-1].get("name", "")
    return {
        "company": slug,
        "title": job.get("title", ""),
        "location": _display_location(job),
        "department": dept,
        "url": job.get("shortlink") or job.get("url") or job.get("application_url", ""),
        "publishedAt": job.get("published_on") or job.get("created_at", ""),
    }


def load_seeds():
    if not os.path.exists(SEEDS_FILE):
        sys.exit(f"No {SEEDS_FILE} found. Run workable_harvest.py first, or add slugs by hand.")
    seen, out = set(), []
    with open(SEEDS_FILE, encoding="utf-8") as f:
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
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
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
    print(f"=== Workable watch {report['date']} {report['time']} ===")
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
        f"<title>Workable new jobs {report['date']}</title>",
        "<style>"
        "body{font-family:system-ui,Arial,sans-serif;max-width:820px;margin:2rem auto;"
        "padding:0 1rem;line-height:1.5;color:#111}"
        "h1{font-size:1.2rem}li{margin:.4rem 0}"
        ".meta{color:#666;font-size:.88rem}"
        "a{color:#1255cc;text-decoration:none}a:hover{text-decoration:underline}"
        "</style>",
        f"<h1>Workable new matching roles &mdash; {report['date']} {report.get('time', '')} ({len(rows)})</h1>",
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
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def _write_artifacts(report, errors):
    with open(LATEST_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    write_html(report, LATEST_HTML)
    if errors:
        with open(f"errors-workable-{report['date']}.log", "w", encoding="utf-8") as f:
            for slug, msg in errors:
                f.write(f"{slug}\t{msg}\n")


def prune_seeds(dead):
    """Remove definitively-dead (404) slugs from the seeds file."""
    if not dead:
        return 0
    dead_set = {d.lower() for d in dead}
    kept = [s for s in load_seeds() if s not in dead_set]
    with open(SEEDS_FILE, "w", encoding="utf-8") as f:
        f.write("# Workable account slugs - one per line (lowercased). Auto-pruned by workable_watch.py\n")
        for s in kept:
            f.write(s + "\n")
    return len(dead_set)


def run():
    seeds = load_seeds()
    prev = load_state()
    boards, errors, dead = fetch_all(seeds)

    current = {}
    for slug, jobs in boards.items():
        for job in jobs:
            if keep(job):
                current[job_key(slug, job)] = normalize(slug, job)

    report = compute_report(prev, current, len(seeds), errors)
    pruned = prune_seeds(dead)

    if not prev:
        print(f"=== Workable watch {report['date']} {report['time']} (baseline) ===")
        print(f"Boards checked: {report['boards_checked']}  "
              f"reached: {len(boards)}  failed: {report['boards_failed']}  "
              f"dead/pruned: {pruned}")
        print(f"Matching US open roles right now: {report['total_open']}")
        print("Baseline saved. New matching roles will be reported from the next run.")
    else:
        print_report(report)
        if pruned:
            print(f"(pruned {pruned} dead boards from seeds)")

    save_state(current)
    _write_artifacts(report, errors)


def selftest():
    # nested location shape (spi-style)
    j_swe = {"title": "Software Engineer, Backend", "shortcode": "A1",
             "location": {"location_str": "San Francisco, CA", "country_code": "US"},
             "shortlink": "https://apply.workable.com/j/A1"}
    j_gb = {"title": "Software Engineer", "shortcode": "A2",
            "location": {"location_str": "London, UK", "country_code": "GB"},
            "shortlink": "https://apply.workable.com/j/A2"}
    j_sr = {"title": "Senior Software Engineer", "shortcode": "A3",
            "location": {"country_code": "US"}, "shortlink": "x"}
    j_found = {"title": "Senior Founding Engineer", "shortcode": "A4",
               "location": {"country_code": "US"}, "shortlink": "x"}
    # flat shape, no country_code -> text fallback
    j_flat_us = {"title": "Data Engineer", "shortcode": "A5",
                 "country": "United States", "city": "Austin", "shortlink": "x"}
    j_flat_intl = {"title": "Data Engineer", "shortcode": "A6",
                   "country": "Germany", "city": "Berlin", "shortlink": "x"}
    j_qa = {"title": "QA Engineer", "shortcode": "A7",
            "location": {"country_code": "US"}, "shortlink": "x"}

    assert keep(j_swe)               # US via country_code
    assert not keep(j_gb)            # GB code -> not US
    assert not keep(j_sr)            # seniority
    assert keep(j_found)             # founding exemption + US
    assert keep(j_flat_us)           # text fallback US
    assert not keep(j_flat_intl)     # text fallback non-US
    assert not keep(j_qa)            # role mismatch

    n = normalize("acme", j_swe)
    assert n["title"] == "Software Engineer, Backend"
    assert n["location"] == "San Francisco, CA"
    assert n["url"] == "https://apply.workable.com/j/A1"

    assert job_key("acme", j_swe) == job_key("Acme", j_swe)  # case-insensitive

    prev = {"acme::old": {"company": "acme", "title": "Old", "location": "",
            "department": "", "url": "", "publishedAt": ""}}
    cur = {job_key("acme", j_swe): normalize("acme", j_swe)}
    rep = compute_report(prev, cur, 1, [])
    assert len(rep["new"]) == 1 and len(rep["removed"]) == 1
    print("selftest: PASS")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if "--selftest" in sys.argv:
        selftest()
    else:
        run()