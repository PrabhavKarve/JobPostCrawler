#!/usr/bin/env python3
"""
greenhouse_harvest.py - discover Greenhouse board tokens -> seeds_greenhouse.txt.

Same Common Crawl approach as the other harvesters, but Greenhouse has TWO
hosted domains plus an embed form, so this checks all of them:
    boards.greenhouse.io/<token>
    job-boards.greenhouse.io/<token>
    boards.greenhouse.io/embed/job_board?for=<token>   (token in query string)

Lowercases + dedupes, merges into seeds_greenhouse.txt.

No third-party dependencies. Python 3.8+.

Usage:
    python3 greenhouse_harvest.py          # 5 most recent crawls (default)
    python3 greenhouse_harvest.py 10       # scan deeper if coverage is thin
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

COLLINFO = "https://index.commoncrawl.org/collinfo.json"
HOSTS = ["boards.greenhouse.io", "job-boards.greenhouse.io"]
SEEDS_FILE = "seeds_greenhouse.txt"
UA = "greenhouse-harvest/1.0 (personal job tracker)"
TIMEOUT = 90
NON_SLUGS = {"embed", "api", "v1", "boards", "_next", "favicon.ico",
             "robots.txt", "assets", "static", "job_board"}


def get_request(url):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": UA}), timeout=TIMEOUT
    )


def recent_indexes(n):
    with get_request(COLLINFO) as r:
        cols = json.load(r)
    return [c["cdx-api"] for c in cols[:n]]


def num_pages(cdx_api, host):
    q = urllib.parse.urlencode({"url": f"{host}/*", "output": "json", "showNumPages": "true"})
    try:
        with get_request(cdx_api + "?" + q) as r:
            return int(json.load(r).get("pages", 1))
    except Exception:
        return 1


def iter_urls(cdx_api, host, page):
    q = urllib.parse.urlencode(
        {"url": f"{host}/*", "output": "json", "fl": "url", "page": str(page)}
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


def token_from_url(u):
    parsed = urllib.parse.urlparse(u)
    # 1) embed form: ...?for=<token>
    qs = urllib.parse.parse_qs(parsed.query)
    if qs.get("for"):
        tok = qs["for"][0].strip().lower()
        if tok and tok not in NON_SLUGS:
            return tok
    # 2) path form: <host>/<token>/...
    parts = [seg for seg in parsed.path.split("/") if seg]
    if not parts:
        return None
    tok = urllib.parse.unquote(parts[0]).strip().lower()
    if not tok or tok in NON_SLUGS:
        return None
    return tok


def load_existing():
    try:
        with open(SEEDS_FILE) as f:
            return {ln.strip().lower() for ln in f
                    if ln.strip() and not ln.lstrip().startswith("#")}
    except FileNotFoundError:
        return set()


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    found = set()
    for cdx in recent_indexes(n):
        for host in HOSTS:
            pages = num_pages(cdx, host)
            print(f"index {cdx} host {host}: {pages} page(s)", file=sys.stderr)
            for page in range(pages):
                try:
                    for url in iter_urls(cdx, host, page):
                        t = token_from_url(url)
                        if t:
                            found.add(t)
                except urllib.error.HTTPError as e:
                    print(f"  {host} page {page}: HTTP {e.code}", file=sys.stderr)
                except Exception as e:  # noqa: BLE001
                    print(f"  {host} page {page}: {e}", file=sys.stderr)
                print(f"  {host} page {page + 1}/{pages}: {len(found)} tokens so far",
                      file=sys.stderr)
                time.sleep(1)

    merged = sorted(load_existing() | found)
    with open(SEEDS_FILE, "w") as f:
        f.write("# Greenhouse board tokens - one per line (lowercased). Merged by greenhouse_harvest.py\n")
        for s in merged:
            f.write(s + "\n")
    print(f"Wrote {len(merged)} tokens to {SEEDS_FILE} ({len(found)} discovered this run).")
    if not found:
        print(f"Found 0 in the {n} most recent crawls. Try more (e.g. "
              f"`python3 greenhouse_harvest.py 12`).", file=sys.stderr)


if __name__ == "__main__":
    main()