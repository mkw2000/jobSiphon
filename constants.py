"""Shared, profile-agnostic runtime settings.

Search strategy, locations, skills, employers, and candidate-specific scoring
rules belong in ignored local files under ``profiles/``.
"""

CSV_FIELDS = [
    "profile",
    "score",
    "llm_score",
    "title",
    "company",
    "location",
    "employment_type",
    "source",
    "date_found",
    "fit_signals",
    "reason",
    "url",
    "description",
]

# Neutral fallbacks used only when a caller does not supply a local profile.
SEARCH_TERMS = ["Job"]
JOBSPY_LOCATIONS = ["United States"]

RESULTS_PER_SITE = 100
JOBSPY_SITES = ["indeed", "google", "linkedin"]
JOBSPY_COUNTRY = "USA"
JOBSPY_SITE_COUNTRY_KWARGS = {
    "indeed": "country_indeed",
    "linkedin": "country_linkedin",
    "zip_recruiter": "country_zip_recruiter",
}

SCRAPER_TIMEOUT_SECS = 120
MAX_WORKERS_SCRAPE = 6
MAX_WORKERS_JOBSPY = 3
REQUEST_MAX_RETRIES = 3
REQUEST_BACKOFF_BASE_SECS = 1.5
REQUEST_JITTER_RANGE_SECS = (0.35, 1.05)
JOBSPY_SITE_PAUSE_RANGE_SECS = (0.15, 0.45)

MAX_JOBS_TO_SCORE = 300
MAX_STREAM_SCORE_QUEUE = MAX_JOBS_TO_SCORE + 1
MAX_SCORING_SECONDS = 28800
MAX_PER_COMPANY = 6
MASTER_LIST_MAX_AGE_DAYS = 30

DB_PATH = "seen_jobs.db"
DESCRIPTION_OPTIONAL_SOURCES = {"weworkremotely"}

WWR_RSS_FEEDS = [
    "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-front-end-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-customer-support-jobs.rss",
    "https://weworkremotely.com/categories/remote-sales-and-marketing-jobs.rss",
    "https://weworkremotely.com/categories/remote-product-jobs.rss",
    "https://weworkremotely.com/categories/remote-all-other-jobs.rss",
]
PDX_PIPELINE_JOB_FEED = "https://www.pdxpipeline.com/?feed=job_feed"
REMOTE_ONLY_SOURCES = {"remoteok", "remotive", "himalayas", "weworkremotely"}

REMOTE_MODE_TERMS = [
    "remote",
    "hybrid",
    "work from home",
    "wfh",
    "distributed",
    "remote-first",
    "remote first",
]
US_ONLY_TERMS = [
    "united states",
    "united states of america",
    "usa",
    "us only",
    "usa only",
    "us-based",
    "us based",
    "u.s.-based",
    "continental us",
    "contiguous us",
    "remote us",
    "us remote",
    "remote united states",
    "remote, united states",
    "united states only",
    "must be based in the us",
    "must be located in the us",
    "must be located in the united states",
    "within the united states",
    "authorized to work in the united states",
]
GLOBAL_REMOTE_TERMS = [
    "anywhere in the world",
    "worldwide",
    "remote anywhere",
    "work from anywhere",
    "global remote",
]
NON_US_COUNTRY_TERMS = [
    "canada",
    "mexico",
    "united kingdom",
    " uk ",
    "emea",
    "europe",
    "european",
    "latam",
    "latin america",
    "apac",
    "australia",
    "new zealand",
    "ireland",
    "germany",
    "france",
    "spain",
    "portugal",
    "italy",
    "switzerland",
    "netherlands",
    "belgium",
    "austria",
    "poland",
    "czechia",
    "czech republic",
    "slovakia",
    "slovenia",
    "croatia",
    "romania",
    "bulgaria",
    "hungary",
    "greece",
    "turkey",
    "ukraine",
    "india",
    "philippines",
    "singapore",
    "japan",
    "south korea",
    "sri lanka",
    "south africa",
    "argentina",
    "brazil",
    "colombia",
]

SCORING_SYSTEM_PROMPT = (
    "You are a strict, objective recruiter matching profiles against job descriptions. "
    "Output valid JSON only with 'score' and 'reason'."
)
SCORING_DESCRIPTION_MAX_CHARS = 8000
