#!/usr/bin/env python3
"""
workable_harvest.py - discover Workable account slugs -> seeds_workable.txt.

Common Crawl approach. Workable boards appear in two URL shapes, so one domain
query (*.workable.com) covers both:
    apply.workable.com/<slug>/...     (slug = first path segment)
    <slug>.workable.com/...           (slug = subdomain)

Lowercases + dedupes, merges into seeds_workable.txt.

No third-party dependencies. Python 3.8+.

Usage:
    python3 workable_harvest.py          # 5 most recent crawls (default)
    python3 workable_harvest.py 10       # scan deeper if coverage is thin
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

COLLINFO = "https://index.commoncrawl.org/collinfo.json"
URL_QUERY = "*.workable.com"   # domain match -> all subdomains incl. apply.workable.com
SEEDS_FILE = "seeds_workable.txt"
UA = "workable-harvest/1.0 (personal job tracker)"
TIMEOUT = 90

# path segments under apply.workable.com that aren't account slugs
NON_PATH = {"j", "api", "widget", "backend", "spi", "assets", "static",
            "_next", "favicon.ico", "robots.txt", "view", "jobs"}
# subdomains of workable.com that aren't customer accounts
NON_SUB = {"www", "apply", "help", "blog", "resources", "careers", "www2",
           "cdn", "status", "developers", "support", "app", "go", "info",
           "email", "links", "marketing", "get", "api", "static", "assets"}


def get_request(url):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": UA}), timeout=TIMEOUT
    )


def recent_indexes(n):
    with get_request(COLLINFO) as r:
        cols = json.load(r)
    return [c["cdx-api"] for c in cols[:n]]


def num_pages(cdx_api):
    q = urllib.parse.urlencode({"url": URL_QUERY, "output": "json", "showNumPages": "true"})
    try:
        with get_request(cdx_api + "?" + q) as r:
            return int(json.load(r).get("pages", 1))
    except Exception:
        return 1


def iter_urls(cdx_api, page):
    q = urllib.parse.urlencode(
        {"url": URL_QUERY, "output": "json", "fl": "url", "page": str(page)}
    )
    with get_request(cdx_api + "?" + q) as r:
        for line in r:
            line = line.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                yield json.loads(line).get("url", "")
            except json.JSONDecodeError:
                continue


def slug_from_url(u):
    parsed = urllib.parse.urlparse(u)
    host = parsed.netloc.lower()
    if host == "apply.workable.com":
        parts = [seg for seg in parsed.path.split("/") if seg]
        if not parts:
            return None
        s = urllib.parse.unquote(parts[0]).strip().lower()
        return s if s and s not in NON_PATH else None
    if host.endswith(".workable.com"):
        sub = host[: -len(".workable.com")].split(".")[0].strip().lower()
        return sub if sub and sub not in NON_SUB else None
    return None


def load_existing():
    try:
        with open(SEEDS_FILE, encoding="utf-8") as f:
            return {ln.strip().lower() for ln in f
                    if ln.strip() and not ln.lstrip().startswith("#")}
    except FileNotFoundError:
        return set()


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    found = set()
    for cdx in recent_indexes(n):
        pages = num_pages(cdx)
        print(f"index {cdx} : {pages} page(s)", file=sys.stderr)
        for page in range(pages):
            try:
                for url in iter_urls(cdx, page):
                    s = slug_from_url(url)
                    if s:
                        found.add(s)
            except urllib.error.HTTPError as e:
                print(f"  page {page}: HTTP {e.code}", file=sys.stderr)
            except Exception as e:  # noqa: BLE001
                print(f"  page {page}: {e}", file=sys.stderr)
            print(f"  page {page + 1}/{pages}: {len(found)} unique slugs so far",
                  file=sys.stderr)
            time.sleep(1)

    merged = sorted(load_existing() | found)
    with open(SEEDS_FILE, "w", encoding="utf-8") as f:
        f.write("# Workable account slugs - one per line (lowercased). Merged by workable_harvest.py\n")
        for s in merged:
            f.write(s + "\n")
    print(f"Wrote {len(merged)} slugs to {SEEDS_FILE} ({len(found)} discovered this run).")
    if not found:
        print(f"Found 0 in the {n} most recent crawls. Try more (e.g. "
              f"`python3 workable_harvest.py 12`).", file=sys.stderr)


if __name__ == "__main__":
    main()