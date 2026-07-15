# Job Discovery Pipeline

Local job-search pipeline that:
1. Runs official APIs, RSS feeds, and JobSpy searches concurrently.
2. Normalizes, deduplicates, and filters each completed search batch immediately.
3. Scores candidates with Ollama while the remaining job-board searches continue.
4. Writes each match and seen URL immediately so interrupted runs retain completed work.

It also includes a local web dashboard for starting and stopping runs, watching
live progress and logs, reviewing ranked jobs, and managing generated state.

---

## Job profiles

Every run targets one job profile. A profile owns its search terms, locations,
remote policy, résumé, role filters, scoring priorities, threshold, and outreach
style. `search_locations` contains a compact set of metro hubs sent to job
boards, while `locations` contains the wider nearby area accepted by the local
filter. This keeps suburb coverage broad without scraping every search term once
per suburb. Each profile also gets an isolated cache, shortlist, master list,
and seen-job database under `data/profiles/<profile>/`.

Profiles contain personal search strategy and are deliberately ignored by Git.
Create a local profile from the neutral template:

```bash
mkdir -p profiles/resumes
cp examples/job-profile.example.json profiles/my-search.json
```

Edit `profiles/my-search.json`, then add the résumé path configured inside it.
Both profile JSON and résumé text remain local, as do generated results under
`data/`.

---

## Prerequisites
 * this has only been tested on mac
 
| Requirement                  | Version |
| ---------------------------- | ------- |
| Python                       | 3.10+   |
| [Ollama](https://ollama.com) | latest  |
| `make`                       | any     |

---

## Quick Start

```bash
# 1. Install Ollama  (macOS - pick one)
brew install ollama
# or
curl -fsSL https://ollama.com/install.sh | sh

# 2. Install any Ollama model you want to use, then confirm it is available
ollama list
# JobSiphon automatically uses an installed model. If several are installed:
# OLLAMA_MODEL=your-model-name make run

# 3. Create a private local profile and résumé
mkdir -p profiles/resumes
cp examples/job-profile.example.json profiles/my-search.json
# Edit the profile, then create the résumé path named inside it:
touch profiles/resumes/my-search.txt

# 4. Install dependencies
make setup

# Optional: enable Wellfound in your local profile and configure Apify
cp .env.example .env
# Add APIFY_TOKEN to .env. The default actor run is capped at $1.00.

# 5. Run the local profile
make run PROFILE=my-search
```

`make run` starts Ollama if it is not already running, then launches the pipeline.
Profile JSON, résumés, `.env`, caches, databases, and generated job lists are
ignored by Git.

### Web dashboard

After completing `make setup`, launch the local operations dashboard:

```bash
make dashboard
```

Then open [http://127.0.0.1:8765](http://127.0.0.1:8765). The dashboard can:

- Start a full discovery run or score the latest cached scrape.
- Stop an active run and clean up the Ollama process started for it.
- Display live search, raw-result, unique-job, scoring, and match counts.
- Select a job profile before starting a run.
- Search and filter that profile's current shortlist or cumulative master list.
- Show résumé, scrape-cache, and Ollama readiness.
- Show whether the optional Wellfound/Apify source is configured.
- Clean current-run outputs or reset seen-job history with confirmation.

The server binds to localhost by default and does not submit applications.

During a full run, the dashboard reports concrete counters instead of a guessed
completion percentage:

- **Searches** is completed source/term/location batches out of the configured total.
- **Found** counts raw listings returned by those batches, including duplicates.
- **Unique** counts distinct URLs discovered during the run.
- **Scored** is completed Ollama evaluations out of the candidates queued so far.
- **Matches** is the number meeting the selected profile's minimum score.

Discovery and scoring overlap, so the number queued may continue increasing
while Ollama is working. A full run queues at most 300 candidates for local-model
scoring. Stopping a run keeps raw cache records, completed evaluations, matches,
master-list updates, and seen-job history already written to disk.

---

## All Make Targets

| Command         | What it does                                                      |
| --------------- | ----------------------------------------------------------------- |
| `make setup`    | Create a uv-managed `.venv` and install all Python dependencies   |
| `make run PROFILE=<slug>` | Start Ollama and run the selected profile              |
| `make dashboard` | Launch the local web operations dashboard at port 8765           |
| `make clean`    | Remove `apply_list.md` and `apply_list.csv`                       |
| `make reset-db` | Clear `seen_jobs.db` so all URLs are re-evaluated on the next run |
| `make help`     | Show the target list in the terminal                              |

---

## Usage

```bash
uv run python main.py --profile my-search
# or:
make run PROFILE=my-search
```

The pipeline:
1. Starts RemoteOK, Remotive, Himalayas, We Work Remotely, local feeds, and JobSpy searches in background worker pools.
2. Emits each source or term/location result batch as soon as it completes.
3. Normalizes, deduplicates, and prefilters that batch by seen URL, seniority, role, location, description, and experience rules.
4. Adds viable candidates to a relevance-prioritized queue consumed by one Ollama worker.
5. Saves every qualifying result to the current and master lists immediately while discovery continues.
6. Appends raw batches to an interruption-safe cache and updates `seen_jobs.db` after each successful evaluation.

---

## Output Files

| File             | Description                                                                                                  |
| ---------------- | ------------------------------------------------------------------------------------------------------------ |
| `data/profiles/<slug>/apply_list.md`  | Current qualifying jobs for one profile |
| `data/profiles/<slug>/apply_list.csv` | Current scored-job CSV for one profile  |
| `data/profiles/<slug>/master_list.*`  | Cumulative active jobs for one profile  |
| `data/profiles/<slug>/seen_jobs.db`   | Per-profile evaluated-URL history       |
| `data/profiles/<slug>/scraped_jobs_cache.json` | Append-only JSONL raw scrape cache; legacy JSON arrays remain readable |

---

## Sources

| Source           | Method             | Notes                                                        |
| ---------------- | ------------------ | ------------------------------------------------------------ |
| LinkedIn         | JobSpy             | Queried via city-based searches                              |
| Indeed           | JobSpy             | Queried via city-based searches                              |
| Google Jobs      | JobSpy             | Queried via city-based searches                              |
| PDX Pipeline     | Official job RSS   | Optional local source enabled with `sources: ["pdxpipeline"]` |
| Wellfound        | Apify Actor API     | Optional profile configuration; requires `APIFY_TOKEN`       |
| RemoteOK         | Public JSON API    | Remote board; filtered locally for explicit U.S. eligibility |
| Remotive         | Public JSON API    | Single full-feed request per run                             |
| Himalayas        | Public JSON API    | Strong structured location restrictions                      |
| We Work Remotely | Official RSS feeds | Uses category feeds instead of HTML scraping                 |

---

## Troubleshooting

**`ollama` not found**
Make sure Ollama is in your PATH. `make run` calls `start.sh`, which checks this first.

**Empty `apply_list.md`**
Adjust `minimum_score`, `search_terms`, `max_required_years`, or `locations`
inside the selected profile JSON.

**Model responses fail validation**
Try another installed model by setting `OLLAMA_MODEL=your-model-name`. The
pipeline does not require a specific Ollama model family.

**A source keeps timing out**
Increase `SCRAPER_TIMEOUT_SECS` or disable that source in
`stream_scraper_batches()`. JobSpy deliberately uses modest parallelism to reduce
job-board rate limiting.

**Score cache reports incomplete JSONL records**
The cache loader recovers every valid record and warns about genuinely partial
records left by an interrupted write. It also supports caches from older
versions that stored one JSON array. A new full search replaces the profile's
raw cache.

---

## Architecture

```text
main()
└── run_streaming_discovery()
    ├── stream_scraper_batches()
    │   ├── direct-source pool   RSS, JSON APIs, PDX Pipeline, optional Wellfound
    │   └── JobSpy pool          LinkedIn, Indeed, and Google term/location searches
    ├── normalize + deduplicate  process each completed search batch immediately
    ├── prefilter()              profile role, location, experience, and company rules
    ├── priority score queue     bounded relevance ordering and backpressure
    ├── single Ollama worker     score while discovery continues
    └── incremental persistence  JSONL cache, current/master lists, and seen-job SQLite

dashboard_server.py
├── local Flask API              profiles, pipeline control, jobs, and maintenance
└── dashboard/                   minimal browser interface and live counters
```
