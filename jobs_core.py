"""
jobs_core.py
Contains configurations, data models, DB helpers, and all scraping functions.
"""

from __future__ import annotations

import constants
import html
import logging
import random
import re
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from queue import Queue
from typing import Any, Iterable

import requests
from bs4 import BeautifulSoup
from jobspy import scrape_jobs  # type: ignore[import-untyped]
from pydantic import BaseModel, Field
from runtime_config import runtime_value

# =============================================================================
# LOGGING
# =============================================================================
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("job-pipeline")


# =============================================================================
# DATA MODELS
# =============================================================================
class LLMScore(BaseModel):
    score: int = Field(ge=0, le=100)
    reason: str


@dataclass
class Job:
    title: str
    company: str
    url: str
    description: str
    location: str
    date_posted: str
    employment_type: str
    source: str


@dataclass
class ScrapeBatch:
    """One completed discovery unit emitted while the other searches continue."""

    source: str
    jobs: list[dict[str, Any]]


# =============================================================================
# SQLITE & UTILS
# =============================================================================
def init_db(path: str | Path = constants.DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS seen_jobs (url TEXT PRIMARY KEY, evaluated_at TEXT)"
    )
    conn.commit()
    return conn


def load_seen_urls(conn: sqlite3.Connection, days: int = 7) -> set[str]:
    """
    Return URLs evaluated within the last `days` days.

    Why a window instead of all-time?
    After multiple runs the seen_jobs table accumulates every URL ever evaluated,
    including jobs that scored below the threshold. On subsequent runs those jobs
    are skipped in prefilter before the LLM ever sees them — even if the scoring
    criteria or filters have since improved. A 7-day window means:
      • Jobs seen this week are skipped (no point re-scoring unchanged postings).
      • Jobs from 8+ days ago are re-evaluated — useful when thresholds/logic change.
      • Qualified jobs reappear in apply_list after 7 days but master_list deduplicates
        by URL so they don't accumulate there.
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return {
        row[0]
        for row in conn.execute(
            "SELECT url FROM seen_jobs WHERE evaluated_at >= ?", (cutoff,)
        ).fetchall()
    }


def update_seen(conn: sqlite3.Connection, urls: list[str]) -> None:
    if not urls:
        return
    conn.executemany(
        """
        INSERT INTO seen_jobs (url, evaluated_at) VALUES (?, ?)
        ON CONFLICT(url) DO UPDATE SET evaluated_at = excluded.evaluated_at
        """,
        [(url, date.today().isoformat()) for url in urls],
    )
    conn.commit()


def safe_get(url: str, timeout: int = 20) -> requests.Response:
    # Use the full User-Agent to avoid getting blocked by WWR
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Accept-Language": "en-US,en;q=0.9",
    }
    last_exc: Exception | None = None
    time.sleep(random.uniform(*constants.REQUEST_JITTER_RANGE_SECS))

    for attempt in range(1, constants.REQUEST_MAX_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=timeout, headers=headers)
            if resp.status_code in {403, 408, 425, 429, 500, 502, 503, 504}:
                if attempt == constants.REQUEST_MAX_RETRIES:
                    return resp
                retry_after = resp.headers.get("Retry-After", "").strip()
                sleep_for = (
                    float(retry_after)
                    if retry_after.isdigit()
                    else constants.REQUEST_BACKOFF_BASE_SECS * attempt
                )
                time.sleep(sleep_for + random.uniform(0.1, 0.75))
                continue
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            if attempt == constants.REQUEST_MAX_RETRIES:
                raise
            time.sleep(
                constants.REQUEST_BACKOFF_BASE_SECS * attempt
                + random.uniform(0.1, 0.75)
            )
    if last_exc:
        raise last_exc
    raise RuntimeError(f"request failed for {url}")


def html_to_text(text: str) -> str:
    raw = str(text or "")
    if not raw:
        return ""
    cleaned = html.unescape(raw)
    if "<" in cleaned and ">" in cleaned:
        cleaned = BeautifulSoup(cleaned, "lxml").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_text(text: str) -> str:
    cleaned = html_to_text(text).lower()
    for o, n in [("u.s.a", "usa"), ("u.s.", "us"), ("u.s", "us")]:
        cleaned = cleaned.replace(o, n)
    return re.sub(r"\s+", " ", re.sub(r"[,|()]+", " ", cleaned)).strip()


def join_text_bits(*parts: Any) -> str:
    return " | ".join(str(part).strip() for part in parts if str(part or "").strip())


def normalize_job(item: dict[str, Any], source: str) -> Job | None:
    title = html_to_text(str(item.get("title", "") or "")).strip()
    url = str(item.get("url", "") or "").strip()
    if not title or not url:
        return None

    return Job(
        title=title,
        company=html_to_text(str(item.get("company", "") or "")).strip(),
        url=url,
        description=html_to_text(str(item.get("description", "") or "")).strip(),
        location=html_to_text(str(item.get("location", "") or "")).strip(),
        date_posted=str(item.get("date_posted", "") or "").strip(),
        employment_type=html_to_text(
            str(item.get("employment_type", "") or item.get("job_type", "") or "")
        ).strip(),
        source=source,
    )


# =============================================================================
# SCRAPERS
# =============================================================================
def scrape_jobspy_combo(
    term: str, location: str, is_remote: bool
) -> list[dict[str, Any]]:
    out = []
    search_location = None if is_remote else location

    # Generate the explicit syntax string required by Google Jobs
    google_term = f"{term} jobs remote" if is_remote else f"{term} jobs near {location}"

    for site in constants.JOBSPY_SITES:
        time.sleep(random.uniform(*constants.JOBSPY_SITE_PAUSE_RANGE_SECS))
        kwargs = (
            {constants.JOBSPY_SITE_COUNTRY_KWARGS[site]: constants.JOBSPY_COUNTRY}
            if site in constants.JOBSPY_SITE_COUNTRY_KWARGS
            else {}
        )
        if site == "linkedin":
            kwargs["linkedin_fetch_description"] = True

        try:
            df = scrape_jobs(
                site_name=[site],
                search_term=term,
                google_search_term=google_term,
                location=search_location,
                is_remote=is_remote,
                results_wanted=constants.RESULTS_PER_SITE,
                **kwargs,
            )
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                out.append(
                    {
                        "title": str(row.get("title", "") or ""),
                        "company": str(row.get("company", "") or ""),
                        "url": str(row.get("job_url", "") or ""),
                        "description": str(row.get("description", "") or ""),
                        "location": str(row.get("location", "") or ""),
                        "date_posted": str(row.get("date_posted", "") or ""),
                        "employment_type": str(
                            row.get("job_type", "")
                            or row.get("employment_type", "")
                            or ""
                        ),
                        "source": str(row.get("site", site) or site),
                    }
                )
        except Exception as exc:
            logger.warning(
                f"jobspy site '{site}' combo '{term}'/'{location}' failed: {exc}"
            )
    return out


def scrape_jobspy(
    search_terms: Iterable[str] | None = None,
    locations: Iterable[str] | None = None,
    include_remote: bool = True,
) -> list[dict[str, Any]]:
    results = []
    combos = []
    active_terms = list(search_terms or constants.SEARCH_TERMS)
    active_locations = list(locations or constants.JOBSPY_LOCATIONS)
    for term in active_terms:
        # Add physical location tasks
        for loc in active_locations:
            combos.append((term, loc, False))
        # Add one remote-only task per search term
        if include_remote:
            combos.append((term, None, True))

    total = len(combos)
    started_at = time.monotonic()
    logger.info(
        f"JobSpy: starting {total} (term, location) combos "
        f"across sites {constants.JOBSPY_SITES} "
        f"with {constants.MAX_WORKERS_JOBSPY} parallel workers"
    )

    completed = 0
    pool = ThreadPoolExecutor(max_workers=constants.MAX_WORKERS_JOBSPY)
    try:
        futures = {
            pool.submit(scrape_jobspy_combo, term, loc, remote): (term, loc, remote)
            for term, loc, remote in combos
        }
        pending = set(futures)
        stall_cycles = 0

        while pending:
            done, pending = wait(
                pending,
                timeout=constants.SCRAPER_TIMEOUT_SECS,
                return_when=FIRST_COMPLETED,
            )

            if not done:
                stall_cycles += 1
                logger.warning(
                    f"JobSpy: no combo has finished in the last "
                    f"{constants.SCRAPER_TIMEOUT_SECS}s "
                    f"({len(pending)} still in flight, "
                    f"{completed}/{total} done so far). "
                    f"Likely a hung request to a job board (stall {stall_cycles}/3)."
                )
                if stall_cycles >= 3:
                    logger.warning(
                        f"JobSpy: giving up on the {len(pending)} stuck combo(s) "
                        f"after {3 * constants.SCRAPER_TIMEOUT_SECS}s of no progress. "
                        f"Proceeding with {len(results)} jobs collected so far."
                    )
                    break
                continue

            stall_cycles = 0
            for future in done:
                term, loc, remote = futures[future]
                label = "remote" if remote else loc
                completed += 1
                elapsed = time.monotonic() - started_at
                avg = elapsed / completed
                remaining = (total - completed) * avg

                try:
                    combo_results = future.result()
                    results.extend(combo_results)
                    logger.info(
                        f"JobSpy [{completed}/{total}] '{term}' @ {label}: "
                        f"+{len(combo_results)} jobs (running total: {len(results)}) | "
                        f"elapsed {elapsed / 60:.1f}m | est. remaining {remaining / 60:.1f}m"
                    )
                except Exception as exc:
                    logger.warning(
                        f"JobSpy [{completed}/{total}] '{term}' @ {label} failed: {exc} | "
                        f"elapsed {elapsed / 60:.1f}m | est. remaining {remaining / 60:.1f}m"
                    )
    finally:
        # Don't block on threads stuck inside a hung HTTP call. Any leftover
        # threads become daemons-in-spirit; the process-level os._exit at the
        # end of main() ensures we don't hang at interpreter shutdown waiting
        # for them to join.
        pool.shutdown(wait=False, cancel_futures=True)

    unique_results = {job["url"]: job for job in results}.values()
    logger.info(
        f"JobSpy: done. {len(unique_results)} unique jobs from "
        f"{len(results)} total results across {completed}/{total} combos"
    )

    return list(unique_results)


def scrape_remoteok() -> list[dict[str, Any]]:
    try:
        data = safe_get("https://remoteok.com/api").json()
        return [
            {
                "title": item.get("position", ""),
                "company": item.get("company", ""),
                "url": item.get("url", ""),
                "description": join_text_bits(
                    item.get("description", ""),
                    f"Tags: {', '.join(item.get('tags', []))}",
                ),
                "location": item.get("location", "") or "Remote",
                "date_posted": str(item.get("date", "")),
                "employment_type": (
                    "Contract"
                    if "contract" in [t.lower() for t in item.get("tags", [])]
                    else ""
                ),
                "source": "remoteok",
            }
            for item in data
            if isinstance(item, dict) and "position" in item
        ]
    except Exception as exc:
        logger.warning(f"remoteok failed: {exc}")
        return []


def scrape_remotive() -> list[dict[str, Any]]:
    try:
        data = safe_get("https://remotive.com/api/remote-jobs").json().get("jobs", [])
        return [
            {
                "title": item.get("title", ""),
                "company": item.get("company_name", ""),
                "url": item.get("url", ""),
                "description": join_text_bits(
                    item.get("description", ""), f"Category: {item.get('category', '')}"
                ),
                "location": item.get("candidate_required_location", "") or "Remote",
                "date_posted": item.get("publication_date", ""),
                "employment_type": item.get("job_type", ""),
                "source": "remotive",
            }
            for item in data
        ]
    except Exception as exc:
        logger.warning(f"remotive failed: {exc}")
        return []


def scrape_himalayas() -> list[dict[str, Any]]:
    try:
        data = (
            safe_get("https://himalayas.app/jobs/api?limit=100").json().get("jobs", [])
        )
        return [
            {
                "title": item.get("title", ""),
                "company": item.get("companyName", ""),
                "url": item.get("url", "") or item.get("applicationLink", ""),
                "description": join_text_bits(
                    item.get("description", ""),
                    f"Seniority: {item.get('seniority', '')}",
                ),
                "location": join_text_bits(
                    ", ".join(
                        str(p) for p in item.get("locationRestrictions", []) or []
                    ),
                    ", ".join(
                        str(p) for p in item.get("timezoneRestrictions", []) or []
                    ),
                )
                or "Remote",
                "date_posted": item.get("publishedAt", ""),
                "employment_type": item.get("employmentType", ""),
                "source": "himalayas",
            }
            for item in data
        ]
    except Exception as exc:
        logger.warning(f"himalayas failed: {exc}")
        return []


def scrape_weworkremotely() -> list[dict[str, Any]]:
    jobs = []
    for feed_url in constants.WWR_RSS_FEEDS:
        try:
            resp = safe_get(feed_url)
            body = (resp.text or "").strip()
            if resp.status_code != 200 or not body:
                logger.warning(
                    f"weworkremotely feed {feed_url} returned "
                    f"status={resp.status_code}, body_len={len(body)}; skipping"
                )
                continue
            root = ET.fromstring(body)
            for item in root.findall("./channel/item"):
                raw_title = html_to_text(item.findtext("title", default=""))
                maybe_comp, sep, maybe_title = raw_title.partition(":")
                title = maybe_title.strip() if sep else raw_title
                company = maybe_comp.strip() if sep else ""
                link = (
                    item.findtext("link", default="")
                    or item.findtext("guid", default="")
                    or ""
                ).strip()
                if link:
                    jobs.append(
                        {
                            "title": title,
                            "company": company,
                            "url": link,
                            "description": item.findtext("description", default=""),
                            "location": join_text_bits(
                                item.findtext("region", ""),
                                item.findtext("country", ""),
                            ),
                            "date_posted": item.findtext("pubDate", ""),
                            "employment_type": item.findtext("type", ""),
                            "source": "weworkremotely",
                        }
                    )
        except Exception as exc:
            logger.warning(f"weworkremotely feed {feed_url} failed: {exc}")
    return jobs


def scrape_pdxpipeline() -> list[dict[str, Any]]:
    """Load Portland-area listings from PDX Pipeline's official job RSS feed."""
    try:
        response = safe_get(constants.PDX_PIPELINE_JOB_FEED)
        body = (response.text or "").strip()
        if response.status_code != 200 or not body:
            logger.warning(
                "pdxpipeline feed returned "
                f"status={response.status_code}, body_len={len(body)}; skipping"
            )
            return []

        root = ET.fromstring(body)
        namespace = "{https://www.pdxpipeline.com}"
        content_namespace = "{http://purl.org/rss/1.0/modules/content/}"
        jobs = []
        for item in root.findall("./channel/item"):
            title = html_to_text(item.findtext("title", default=""))
            link = (item.findtext("link", default="") or "").strip()
            if not title or not link:
                continue
            jobs.append(
                {
                    "title": title,
                    "company": item.findtext(f"{namespace}company", default=""),
                    "url": link,
                    "description": item.findtext(
                        f"{content_namespace}encoded", default=""
                    )
                    or item.findtext("description", default=""),
                    "location": item.findtext(f"{namespace}location", default="")
                    or "Portland, OR",
                    "date_posted": item.findtext("pubDate", default=""),
                    "employment_type": item.findtext(
                        f"{namespace}job_type", default=""
                    ),
                    "source": "pdxpipeline",
                }
            )
        return jobs
    except Exception as exc:
        logger.warning(f"pdxpipeline failed: {exc}")
        return []


def normalize_wellfound_items(payload: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize an Apify Wellfound dataset, including previously completed runs."""
    jobs = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        locations = item.get("locationNames") or item.get("locations") or []
        if isinstance(locations, str):
            locations = [locations]
        remote_locations = item.get("remoteLocations") or []
        if isinstance(remote_locations, str):
            remote_locations = [remote_locations]
        location = join_text_bits(
            item.get("location", ""),
            ", ".join(str(value) for value in locations if value),
            ", ".join(str(value) for value in remote_locations if value),
        )
        if item.get("remote") and "remote" not in location.lower():
            location = join_text_bits(location, "Remote")

        description = (
            item.get("description")
            or item.get("descriptionText")
            or item.get("descriptionMarkdown")
            or ""
        )
        compensation = item.get("compensation", "")
        equity = item.get("equity", "")
        jobs.append(
            {
                "title": item.get("title", ""),
                "company": item.get("companyName", ""),
                "url": item.get("portalUrl")
                or item.get("jobUrl")
                or item.get("detailUrl")
                or item.get("url", ""),
                "description": join_text_bits(
                    description,
                    f"Compensation: {compensation}" if compensation else "",
                    f"Equity: {equity}" if equity else "",
                ),
                "location": location or "Remote",
                "date_posted": item.get("postedAtIso") or item.get("postedAt") or "",
                "employment_type": item.get("jobType", ""),
                "source": "wellfound",
            }
        )
    return jobs


def scrape_wellfound(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Run the configured Wellfound actor through Apify and normalize its output."""
    settings = dict(config or {})
    if not settings.get("enabled", False):
        return []

    token = runtime_value("APIFY_TOKEN")
    if not token:
        logger.warning(
            "wellfound skipped: APIFY_TOKEN is not configured in .env or ~/.env"
        )
        return []

    actor = runtime_value("WELLFOUND_APIFY_ACTOR") or (
        "blackfalcondata/wellfound-scraper"
    )
    actor_id = actor.strip().replace("/", "~")
    try:
        max_results = max(1, min(500, int(settings.get("maxResults", 100))))
    except (TypeError, ValueError):
        max_results = 100
    settings["maxResults"] = max_results
    settings.pop("enabled", None)

    try:
        max_charge = float(runtime_value("WELLFOUND_MAX_CHARGE_USD") or "1.00")
    except ValueError:
        max_charge = 1.0
    max_charge = max(0.10, min(25.0, max_charge))

    endpoint = (
        f"https://api.apify.com/v2/actors/{actor_id}/"
        "run-sync-get-dataset-items"
    )
    try:
        response = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            params={
                "timeout": 180,
                "maxItems": max_results,
                "maxTotalChargeUsd": f"{max_charge:.2f}",
            },
            json=settings,
            timeout=200,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            logger.warning("wellfound actor returned a non-list dataset; skipping")
            return []

        return normalize_wellfound_items(payload)
    except Exception as exc:
        logger.warning(f"wellfound via Apify failed: {exc}")
        return []


def stream_scraper_batches(
    search_terms: Iterable[str] | None = None,
    locations: Iterable[str] | None = None,
    include_remote: bool = True,
    wellfound: dict[str, Any] | None = None,
    enabled_sources: Iterable[str] | None = None,
) -> tuple[int, Iterable[ScrapeBatch]]:
    """Return the search-unit count and an iterator yielding results as they finish.

    Direct feeds/APIs and JobSpy use separate worker pools. Their producer threads
    place completed batches onto a bounded queue, allowing normalization and LLM
    scoring to overlap the remaining network searches.
    """
    configured_sources = set(enabled_sources or ())
    sources = {}
    if "pdxpipeline" in configured_sources:
        sources["pdxpipeline"] = scrape_pdxpipeline
    if wellfound and wellfound.get("enabled", False):
        sources["wellfound"] = lambda: scrape_wellfound(wellfound)
    if include_remote:
        sources.update(
            {
                "remoteok": scrape_remoteok,
                "weworkremotely": scrape_weworkremotely,
                "remotive": scrape_remotive,
                "himalayas": scrape_himalayas,
            }
        )

    active_terms = list(search_terms or constants.SEARCH_TERMS)
    active_locations = list(locations or constants.JOBSPY_LOCATIONS)
    combos = [
        (term, location, False)
        for term in active_terms
        for location in active_locations
    ]
    if include_remote:
        combos.extend((term, "", True) for term in active_terms)

    total_units = len(sources) + len(combos)
    events: Queue[ScrapeBatch | None] = Queue(maxsize=32)

    def produce_sources() -> None:
        pool = ThreadPoolExecutor(max_workers=constants.MAX_WORKERS_SCRAPE)
        try:
            futures = {pool.submit(fn): name for name, fn in sources.items()}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    jobs = future.result()
                except Exception as exc:
                    logger.warning(f"{name} failed: {exc}")
                    jobs = []
                events.put(ScrapeBatch(name, jobs))
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
            events.put(None)

    def produce_jobspy() -> None:
        logger.info(
            f"JobSpy: starting {len(combos)} (term, location) combos "
            f"across sites {constants.JOBSPY_SITES} "
            f"with {constants.MAX_WORKERS_JOBSPY} parallel workers"
        )
        started_at = time.monotonic()
        completed = 0
        pool = ThreadPoolExecutor(max_workers=constants.MAX_WORKERS_JOBSPY)
        try:
            futures = {
                pool.submit(scrape_jobspy_combo, term, location, remote): (
                    term,
                    location,
                    remote,
                )
                for term, location, remote in combos
            }
            pending = set(futures)
            stall_cycles = 0
            while pending:
                done, pending = wait(
                    pending,
                    timeout=constants.SCRAPER_TIMEOUT_SECS,
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    stall_cycles += 1
                    logger.warning(
                        f"JobSpy: no combo has finished in the last "
                        f"{constants.SCRAPER_TIMEOUT_SECS}s "
                        f"({len(pending)} still in flight, {completed}/{len(combos)} "
                        f"done so far; stall {stall_cycles}/3)"
                    )
                    if stall_cycles >= 3:
                        logger.warning(
                            f"JobSpy: abandoning {len(pending)} stuck combo(s)"
                        )
                        break
                    continue

                stall_cycles = 0
                for future in done:
                    term, location, remote = futures[future]
                    label = "remote" if remote else location
                    completed += 1
                    try:
                        jobs = future.result()
                    except Exception as exc:
                        logger.warning(
                            f"JobSpy [{completed}/{len(combos)}] '{term}' @ "
                            f"{label} failed: {exc}"
                        )
                        jobs = []
                    elapsed = time.monotonic() - started_at
                    remaining = (
                        (len(combos) - completed) * (elapsed / completed)
                        if completed
                        else 0
                    )
                    logger.info(
                        f"JobSpy [{completed}/{len(combos)}] '{term}' @ {label}: "
                        f"+{len(jobs)} jobs | elapsed {elapsed / 60:.1f}m | "
                        f"est. search remaining {remaining / 60:.1f}m"
                    )
                    events.put(ScrapeBatch(f"jobspy:{term}@{label}", jobs))
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
            events.put(None)

    def batches() -> Iterable[ScrapeBatch]:
        producers = (
            threading.Thread(
                target=produce_sources, name="jobsiphon-direct-sources", daemon=True
            ),
            threading.Thread(
                target=produce_jobspy, name="jobsiphon-jobspy", daemon=True
            ),
        )
        for producer in producers:
            producer.start()

        finished = 0
        while finished < len(producers):
            event = events.get()
            if event is None:
                finished += 1
            else:
                yield event

    return total_units, batches()


def run_scrapers_parallel(
    search_terms: Iterable[str] | None = None,
    locations: Iterable[str] | None = None,
    include_remote: bool = True,
    wellfound: dict[str, Any] | None = None,
    enabled_sources: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Compatibility wrapper that collects the new streaming scraper output."""
    _, batches = stream_scraper_batches(
        search_terms, locations, include_remote, wellfound, enabled_sources
    )
    return [job for batch in batches for job in batch.jobs]
