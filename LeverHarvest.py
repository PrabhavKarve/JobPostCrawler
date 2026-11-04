#!/usr/bin/env python3
"""
lever_harvest.py - discover Lever board slugs and write them to seeds_lever.txt.

Same approach as the Ashby harvester, pointed at jobs.lever.co. Pulls every
jobs.lever.co/<slug>/... URL Common Crawl has captured, extracts <slug>,
lowercases + dedupes, merges into seeds_lever.txt.

No third-party dependencies. Python 3.8+.

Usage:
    python3 lever_harvest.py          # most recent crawl
    python3 lever_harvest.py 3        # 3 most recent crawls (more, slower)
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

COLLINFO = "https://index.commoncrawl.org/collinfo.json"
HOST = "jobs.lever.co"
SEEDS_FILE = "seeds_lever.txt"
UA = "lever-harvest/1.0 (personal job tracker)"
TIMEOUT = 90
NON_SLUGS = {"api", "embed", "_next", "favicon.ico", "robots.txt", "assets", "static"}


def get_request(url):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": UA}), timeout=TIMEOUT
    )


def recent_indexes(n):
    with get_request(COLLINFO) as r:
        cols = json.load(r)
    return [c["cdx-api"] for c in cols[:n]]


def num_pages(cdx_api):
    q = urllib.parse.urlencode({"url": f"{HOST}/*", "output": "json", "showNumPages": "true"})
    try:
        with get_request(cdx_api + "?" + q) as r:
            return int(json.load(r).get("pages", 1))
    except Exception:
        return 1


def iter_urls(cdx_api, page):
    q = urllib.parse.urlencode(
        {"url": f"{HOST}/*", "output": "json", "fl": "url", "page": str(page)}
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
    parts = [seg for seg in urllib.parse.urlparse(u).path.split("/") if seg]
    if not parts:
        return None
    slug = urllib.parse.unquote(parts[0]).strip().lower()
    if not slug or slug in NON_SLUGS:
        return None
    return slug


def load_existing():
    try:
        with open(SEEDS_FILE) as f:
            return {ln.strip().lower() for ln in f
                    if ln.strip() and not ln.lstrip().startswith("#")}
    except FileNotFoundError:
        return set()


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
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
    with open(SEEDS_FILE, "w") as f:
        f.write("# Lever board slugs - one per line (lowercased). Merged by lever_harvest.py\n")
        for s in merged:
            f.write(s + "\n")
    print(f"Wrote {len(merged)} slugs to {SEEDS_FILE} ({len(found)} discovered this run).")


if __name__ == "__main__":
    main()