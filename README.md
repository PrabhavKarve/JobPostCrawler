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
- `latest.json` - machine-readable report (pipe into email/Slack); overwritten each run
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
- `latest_lever.html`, `latest_lever.json`

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

---

## Greenhouse (third ATS)

`greenhouse_watch.py` + `greenhouse_harvest.py` are the Greenhouse twins.
Identical filters and outputs, hitting Greenhouse's public Job Board API
(`boards-api.greenhouse.io/v1/boards/<token>/jobs`). Own files:

- `seeds_greenhouse.txt`, `state_greenhouse.json`
- `latest_greenhouse.html`, `latest_greenhouse.json`

Run them the same way:

```bash
python3 greenhouse_harvest.py        # discover board tokens -> seeds_greenhouse.txt
python3 greenhouse_watch.py          # first run = baseline, then deltas
```

Cron, alongside Ashby + Lever (stagger the minutes so they don't all fire at once):

```
0  */3 * * * cd /path/to/folder && /usr/bin/python3 ashby_watch.py      >> watch.log 2>&1
30 */3 * * * cd /path/to/folder && /usr/bin/python3 lever_watch.py      >> watch_lever.log 2>&1
45 */3 * * * cd /path/to/folder && /usr/bin/python3 greenhouse_watch.py >> watch_greenhouse.log 2>&1
```

Notes specific to Greenhouse:
- Two hosted domains (boards.greenhouse.io and job-boards.greenhouse.io) and an
  embed form (?for=<token>); the harvester parses all three.
- "board token" = the company id in the board URL (e.g. stripe). It's the same
  thing the other scripts call a slug.
- absolute_url may point to a company's own careers domain - that's still the
  correct apply link, so links work either way.

---

## Workable (fourth ATS)

`workable_watch.py` + `workable_harvest.py`, hitting Workable's public widget API
(`apply.workable.com/api/v1/widget/accounts/<slug>`). Own files:

- `seeds_workable.txt`, `state_workable.json`
- `latest_workable.html`, `latest_workable.json`

```bash
python3 workable_harvest.py        # discover slugs -> seeds_workable.txt
python3 workable_watch.py          # baseline, then deltas
```

Cron (stagger again):

```
0  */3 * * * cd /path/to/folder && /usr/bin/python3 workable_watch.py >> watch_workable.log 2>&1
```

Notes specific to Workable:
- Best US precision of the set: jobs carry a structured `country_code`, so the
  filter matches "US" exactly when present and only falls back to text otherwise.
- Boards appear as apply.workable.com/<slug> and <slug>.workable.com; the
  harvester's single *.workable.com query covers both.
- Uses the PUBLIC widget API (no key). The other Workable API
  (<slug>.workable.com/spi/v3/jobs) needs a Bearer token - not used here.

## ATS coverage summary

| ATS        | Public API | US filter basis              |
|------------|------------|------------------------------|
| Ashby      | yes        | structured country + text    |
| Lever      | yes        | text (location string)       |
| Greenhouse | yes        | text (location.name + office)|
| Workable   | yes        | structured country_code      |
| Jobvite    | NO         | n/a - feed needs API keys     |

Jobvite was researched and skipped: its job feed requires per-company API keys
(companyId/apiKey/secretKey), so there's no public no-auth endpoint to harvest.

---

## Dashboard (backend + frontend)

Requires a running Postgres instance (`DATABASE_URL` env var, defaults to
`postgresql://postgres:root@localhost:5432/jobcrawler`).

Backend (FastAPI, from `backend/`):

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Frontend (React + Vite, from `frontend/`):

```bash
npm install
npm run dev
```

Frontend runs at http://localhost:5173 and expects the backend at http://localhost:8000.