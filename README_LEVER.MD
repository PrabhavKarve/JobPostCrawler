# Ashby job watcher (filtered)

Track *new, US-based, engineering* job openings across Ashby job boards, daily.

Two scripts, zero dependencies (Python 3.8+, standard library only):

- `harvest.py` - discovers Ashby board slugs from Common Crawl -> `seeds.txt`.
- `ashby_watch.py` - fetches every board in parallel, keeps only roles matching
  your filters, diffs against the previous run, reports what's new.

## Flow

```
harvest.py  --->  seeds.txt  --->  ashby_watch.py  --->  newjobs-YYYY-MM-DD.json
(weekly-ish)     (slug list)        (daily, parallel,     + printed list of new roles
                                     filtered)
```

## Quick start

```bash
python3 harvest.py            # optional: discover boards (or use starter seeds.txt)
python3 ashby_watch.py        # first run = silent baseline
python3 ashby_watch.py        # second run onward = new matching roles only
```

## Your filters (edit the CONFIG block at the top of ashby_watch.py)

Currently set to:

- Roles (title must contain one of): Software Engineer, AI Engineer,
  Data Engineer, GTM Engineer, Forward Deployed Engineer, Founding Engineer
- Location: United States only

Seniority filter: titles carrying Senior, Sr, Staff, Principal, Lead, Director,
Head, VP, Distinguished, or Intern/Internship are dropped (whole-word match),
keeping base-level roles only. Exception: anything containing "founding engineer"
is kept regardless (e.g. "Senior Founding Engineer"). Tune both lists via
`SENIORITY_EXCLUDE` and `SENIORITY_EXEMPT`.

Matching is literal substring on the title, so "Database Engineer" and
"Full-Stack Engineer" do NOT match "data engineer"/"software engineer", and a
title like "Software Engineer II" is kept (no seniority keyword). Add keywords
to `ROLE_KEYWORDS` if you want more. A role listed only as bare "Remote" (no
country) is treated as not-confirmed-US and dropped.

IMPORTANT: if you change the filters, delete `state.json` once to re-baseline.
Otherwise the next diff compares old filtered data against new filtered data.

## Speed

`ashby_watch.py` fetches boards concurrently (`MAX_WORKERS = 12` at the top).
That turns thousands of sequential requests into a few minutes of parallel ones.
Raise MAX_WORKERS for more speed; lower it if you ever get rate-limited.

## Run daily (cron, macOS/Linux)

```bash
crontab -e
```

```
# new matching roles every morning at 8am
0 8 * * * cd /path/to/folder && /usr/bin/python3 ashby_watch.py >> watch.log 2>&1
# refresh the board list weekly (Mon 7am), scanning 2 crawls
0 7 * * 1 cd /path/to/folder && /usr/bin/python3 harvest.py 2 >> harvest.log 2>&1
```

## Outputs

- stdout / `watch.log` - human-readable list of new matching roles
- `latest.html` - clickable links to the new roles; open in a browser, bookmark it
- `newjobs-YYYY-MM-DD.html` - same as latest.html but dated (an archive per run)
- `newjobs-YYYY-MM-DD.json` - machine-readable report (pipe into email/Slack)
- `state.json` - current snapshot; delete only to re-baseline
- `errors-YYYY-MM-DD.log` - boards that failed that day (only if any did)

## Test the logic offline

```bash
python3 ashby_watch.py --selftest
```

## Limits (by design)

- Coverage is a large chunk, not all of Ashby. Common Crawl only sees boards it
  has crawled and can't see companies that embed Ashby on their own domain.
- "Georgia" as a country (not the US state) is correctly treated as non-US;
  US filtering trusts the structured country field before any text guess.

---

## Lever (same system, second ATS)

`lever_watch.py` + `lever_harvest.py` are the Lever twins of the Ashby scripts.
Identical filters (roles, seniority, US-only) and identical HTML/JSON outputs,
but they hit Lever's public postings API (`api.lever.co/v0/postings/<slug>?mode=json`)
and use their OWN files so nothing collides with the Ashby run:

- `seeds_lever.txt`, `state_lever.json`
- `latest_lever.html`, `newjobs-lever-YYYY-MM-DD.{html,json}`

Run them the same way:

```bash
python3 lever_harvest.py        # discover Lever slugs -> seeds_lever.txt
python3 lever_watch.py          # first run = silent baseline, then daily deltas
```

Cron, alongside the Ashby jobs:

```
15 8 * * * cd /path/to/folder && /usr/bin/python3 lever_watch.py >> watch_lever.log 2>&1
15 7 * * 1 cd /path/to/folder && /usr/bin/python3 lever_harvest.py 2 >> harvest_lever.log 2>&1
```

Notes specific to Lever:
- The filter CONFIG block is duplicated in both watchers. Keep them in sync, or
  ask to refactor the shared logic into one module.
- Lever has a separate EU instance (api.eu.lever.co). These scripts use the
  global instance; EU-only boards will 404 and be skipped. Can be added if needed.
- Lever location data is plain text (no structured country), so US filtering
  relies on the text matcher; a role listed only as bare "Remote" is dropped.