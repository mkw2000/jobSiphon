"""Local web dashboard for operating the JobSiphon pipeline."""

from __future__ import annotations

import argparse
import atexit
import csv
import json
import os
import re
import signal
import sqlite3
import subprocess
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from job_profiles import JobProfile, default_profile_slug, load_profiles
from ollama_config import installed_models, select_model
from runtime_config import runtime_value


ROOT = Path(__file__).resolve().parent


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PipelineBusyError(RuntimeError):
    """Raised when an operation conflicts with a running pipeline."""


class JobSiphonService:
    """Owns the pipeline subprocess and reads dashboard data from disk."""

    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.profiles = load_profiles(root)
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._logs: deque[dict[str, Any]] = deque(maxlen=1200)
        self._log_sequence = 0
        self._mode: str | None = None
        self._profile_slug = default_profile_slug(self.profiles)
        self._stage = "idle"
        self._percent: int | None = None
        self._progress_detail = "Ready for the next run"
        self._started_at: str | None = None
        self._ended_at: str | None = None
        self._exit_code: int | None = None
        self._stop_requested = False
        self._counters = {
            "searches_done": 0,
            "searches_total": 0,
            "found": 0,
            "unique": 0,
            "queued": 0,
            "scored": 0,
            "matches": 0,
        }

    def _running_unlocked(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running_unlocked()

    def _append_log(self, line: str, stream: str = "pipeline") -> None:
        clean = line.rstrip()
        if not clean:
            return
        with self._lock:
            self._log_sequence += 1
            self._logs.append(
                {
                    "id": self._log_sequence,
                    "time": utc_now(),
                    "stream": stream,
                    "message": clean,
                }
            )
            self._update_progress_unlocked(clean)

    def _update_progress_unlocked(self, line: str) -> None:
        if "Starting job discovery pipeline" in line:
            self._stage, self._percent = "starting", 2
            self._progress_detail = "Initializing local state"
        elif "Verifying " in line and "jobs" in line:
            self._stage, self._percent = "verifying", 5
            self._progress_detail = "Checking previously saved jobs"
        elif "Streaming pipeline:" in line:
            self._stage, self._percent = "streaming", None
            self._progress_detail = "Searching and scoring concurrently"
        elif match := re.search(
            r"Pipeline progress: searches (\d+)/(\d+) \| found (\d+) \| "
            r"unique (\d+) \| queued (\d+) \| scored (\d+) \| matches (\d+)",
            line,
        ):
            values = [int(value) for value in match.groups()]
            self._counters = dict(
                zip(
                    (
                        "searches_done",
                        "searches_total",
                        "found",
                        "unique",
                        "queued",
                        "scored",
                        "matches",
                    ),
                    values,
                )
            )
            discovery_done = values[1] > 0 and values[0] >= values[1]
            self._stage, self._percent = (
                "scoring" if discovery_done else "streaming",
                None,
            )
            prefix = "Search complete" if discovery_done else f"{values[0]}/{values[1]} searches"
            self._progress_detail = (
                f"{prefix} · {values[5]}/{values[4]} scored · {values[6]} matches"
            )
        elif "Discovery complete" in line:
            self._stage, self._percent = "scoring", None
            self._progress_detail = "Discovery complete; finishing queued scoring"
        elif "JobSpy: starting" in line:
            if self._stage != "streaming":
                self._stage, self._percent = "scraping", 10
                self._progress_detail = "Searching job boards"
        elif match := re.search(r"JobSpy \[(\d+)/(\d+)\]", line):
            if self._stage != "streaming":
                done, total = int(match.group(1)), max(1, int(match.group(2)))
                self._stage = "scraping"
                self._percent = min(48, 10 + round((done / total) * 38))
                self._progress_detail = f"Search batches {done} of {total}"
        elif "Cached " in line and "raw jobs" in line:
            self._stage, self._percent = "filtering", 50
            self._progress_detail = "Normalizing and filtering discoveries"
        elif "Prefilter:" in line:
            self._stage, self._percent = "ranking", 55
            self._progress_detail = "Ranking viable matches"
        elif "Loading cached jobs" in line:
            self._stage, self._percent = "loading", 20
            self._progress_detail = "Loading the most recent scrape"
        elif "Scoring phase:" in line and "queued" in line:
            self._stage, self._percent = "scoring", 58
            self._progress_detail = "Scoring matches with the configured model"
        elif match := re.search(r"Scoring progress: (\d+)/(\d+)", line):
            done, total = int(match.group(1)), max(1, int(match.group(2)))
            self._stage = "scoring"
            self._percent = min(96, 58 + round((done / total) * 38))
            self._progress_detail = f"Scored {done} of {total} candidates"
        elif "Pipeline complete" in line:
            self._stage, self._percent = "complete", 100
            self._progress_detail = "Run completed successfully"

    def _profile(self, slug: str | None = None) -> JobProfile:
        selected = slug or self._profile_slug
        try:
            return self.profiles[selected]
        except KeyError as exc:
            raise ValueError(f"Unknown job profile: {selected}") from exc

    def start(self, mode: str, profile_slug: str | None = None) -> dict[str, Any]:
        if mode not in {"full", "score-only"}:
            raise ValueError("mode must be 'full' or 'score-only'")
        profile = self._profile(profile_slug)

        with self._lock:
            if self._running_unlocked():
                raise PipelineBusyError("A pipeline run is already active")

            command = ["bash", "start.sh"]
            if mode == "score-only":
                command.append("--score-only")
            command.extend(["--profile", profile.slug])

            self._mode = mode
            self._profile_slug = profile.slug
            self._stage = "starting"
            self._percent = 1
            self._progress_detail = (
                "Starting a full discovery run"
                if mode == "full"
                else "Starting scoring from the cached scrape"
            )
            self._started_at = utc_now()
            self._ended_at = None
            self._exit_code = None
            self._stop_requested = False
            self._counters = {
                "searches_done": 0,
                "searches_total": 0,
                "found": 0,
                "unique": 0,
                "queued": 0,
                "scored": 0,
                "matches": 0,
            }
            self._logs.clear()
            self._append_log(
                f"Dashboard requested {'full discovery' if mode == 'full' else 'score-only'} run for {profile.name}",
                "dashboard",
            )

            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            self._process = subprocess.Popen(
                command,
                cwd=self.root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
            self._reader = threading.Thread(
                target=self._read_process_output,
                name="jobsiphon-pipeline-output",
                daemon=True,
            )
            self._reader.start()
            return self.pipeline_status()

    def _read_process_output(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return

        for line in process.stdout:
            self._append_log(line)

        process.stdout.close()
        exit_code = process.wait()
        with self._lock:
            self._exit_code = exit_code
            self._ended_at = utc_now()
            if self._stop_requested:
                self._stage = "stopped"
                self._percent = None
                self._progress_detail = "Run stopped by the operator"
            elif exit_code == 0:
                self._stage = "complete"
                self._percent = 100
                self._progress_detail = "Run completed successfully"
            else:
                self._stage = "failed"
                self._percent = None
                self._progress_detail = f"Run exited with code {exit_code}"
            self._append_log(self._progress_detail, "dashboard")

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if not self._running_unlocked() or self._process is None:
                return self.pipeline_status()
            process = self._process
            self._stop_requested = True
            self._stage = "stopping"
            self._percent = None
            self._progress_detail = "Stopping the active run"
            self._append_log("Stop requested from dashboard", "dashboard")

        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return self.pipeline_status()

    def pipeline_status(self) -> dict[str, Any]:
        with self._lock:
            running = self._running_unlocked()
            return {
                "running": running,
                "pid": self._process.pid if running and self._process else None,
                "mode": self._mode,
                "profile": self._profile_slug,
                "stage": self._stage,
                "percent": self._percent,
                "detail": self._progress_detail,
                "started_at": self._started_at,
                "ended_at": self._ended_at,
                "exit_code": self._exit_code,
                "counters": dict(self._counters),
            }

    def logs(self, after: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return [entry for entry in self._logs if entry["id"] > after]

    def _csv_rows(self, filename: str | Path) -> list[dict[str, str]]:
        path = self.root / filename
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                return list(csv.DictReader(handle))
        except (OSError, csv.Error):
            return []

    def jobs(
        self,
        profile_slug: str,
        list_name: str,
        query: str = "",
        min_score: int = 0,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        paths = self._profile(profile_slug).paths(self.root)
        filenames = {"current": paths.apply_csv, "master": paths.master_csv}
        if list_name not in filenames:
            raise ValueError("list must be 'current' or 'master'")

        rows = self._csv_rows(filenames[list_name])
        needle = query.casefold().strip()

        def included(row: dict[str, str]) -> bool:
            try:
                score = int(row.get("score", "0") or 0)
            except ValueError:
                score = 0
            if score < min_score:
                return False
            if not needle:
                return True
            haystack = " ".join(
                row.get(field, "")
                for field in ("title", "company", "location", "fit_signals", "reason")
            ).casefold()
            return needle in haystack

        filtered = [row for row in rows if included(row)]
        return {
            "items": filtered[offset : offset + limit],
            "total": len(filtered),
            "list": list_name,
        }

    def _seen_stats(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {"count": 0, "latest": None}
        try:
            with sqlite3.connect(path) as conn:
                count, latest = conn.execute(
                    "SELECT COUNT(*), MAX(evaluated_at) FROM seen_jobs"
                ).fetchone()
            return {"count": int(count), "latest": latest}
        except (sqlite3.Error, OSError):
            return {"count": 0, "latest": None}

    def _ollama_status(self) -> dict[str, Any]:
        names = installed_models(timeout=0.65)
        if names is None:
            return {"online": False, "models": []}
        return {"online": True, "models": list(names)}

    def overview(self, profile_slug: str | None = None) -> dict[str, Any]:
        profile = self._profile(profile_slug)
        paths = profile.paths(self.root)
        cache = paths.cache
        resume = paths.resume
        cache_stats = cache.stat() if cache.exists() else None
        ollama_status = self._ollama_status()
        selected_model = select_model(ollama_status["models"])
        return {
            "pipeline": self.pipeline_status(),
            "selected_profile": profile.public_dict(self.root),
            "profiles": [
                item.public_dict(self.root) for item in self.profiles.values()
            ],
            "counts": {
                "current": len(self._csv_rows(paths.apply_csv)),
                "master": len(self._csv_rows(paths.master_csv)),
                "seen": self._seen_stats(paths.database),
            },
            "model": selected_model or "Auto-select installed model",
            "ollama": ollama_status,
            "wellfound": {
                "enabled": bool(profile.wellfound.get("enabled", False)),
                "configured": bool(runtime_value("APIFY_TOKEN")),
                "actor": runtime_value("WELLFOUND_APIFY_ACTOR")
                or "blackfalcondata/wellfound-scraper",
            },
            "resume": {
                "configured": resume.exists(),
                "filename": profile.resume_path,
            },
            "cache": {
                "available": cache_stats is not None,
                "updated_at": (
                    datetime.fromtimestamp(
                        cache_stats.st_mtime, timezone.utc
                    ).isoformat()
                    if cache_stats
                    else None
                ),
                "size_bytes": cache_stats.st_size if cache_stats else 0,
            },
        }

    def clean_outputs(self, profile_slug: str) -> list[str]:
        with self._lock:
            if self._running_unlocked():
                raise PipelineBusyError("Stop the pipeline before cleaning outputs")
        paths = self._profile(profile_slug).paths(self.root)
        removed = []
        for path in (paths.apply_markdown, paths.apply_csv):
            if path.exists():
                path.unlink()
                removed.append(path.name)
        self._append_log(
            f"Cleaned {len(removed)} current-run output files", "dashboard"
        )
        return removed

    def reset_seen_database(self, profile_slug: str) -> bool:
        with self._lock:
            if self._running_unlocked():
                raise PipelineBusyError("Stop the pipeline before resetting seen jobs")
        path = self._profile(profile_slug).paths(self.root).database
        existed = path.exists()
        if existed:
            path.unlink()
        self._append_log("Seen-job database reset", "dashboard")
        return existed


def create_app(service: JobSiphonService | None = None) -> Flask:
    app = Flask(
        __name__,
        template_folder="dashboard/templates",
        static_folder="dashboard/static",
    )
    app.config["JSON_SORT_KEYS"] = False
    dashboard = service or JobSiphonService()
    app.extensions["jobsiphon_service"] = dashboard

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.get("/api/overview")
    def overview():
        try:
            return jsonify(dashboard.overview(request.args.get("profile")))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/logs")
    def logs():
        after = request.args.get("after", default=0, type=int)
        return jsonify({"items": dashboard.logs(max(0, after))})

    @app.get("/api/jobs")
    def jobs():
        list_name = request.args.get("list", "current")
        profile_slug = request.args.get("profile", dashboard._profile_slug)
        query = request.args.get("q", "")
        min_score = max(0, min(100, request.args.get("min_score", default=0, type=int)))
        offset = max(0, request.args.get("offset", default=0, type=int))
        limit = max(1, min(250, request.args.get("limit", default=100, type=int)))
        try:
            return jsonify(
                dashboard.jobs(profile_slug, list_name, query, min_score, offset, limit)
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/pipeline/start")
    def start_pipeline():
        payload = request.get_json(silent=True) or {}
        try:
            return jsonify(
                {
                    "pipeline": dashboard.start(
                        payload.get("mode", "full"), payload.get("profile")
                    )
                }
            ), 202
        except (ValueError, PipelineBusyError) as exc:
            return jsonify({"error": str(exc)}), 409

    @app.post("/api/pipeline/stop")
    def stop_pipeline():
        return jsonify({"pipeline": dashboard.stop()})

    @app.post("/api/maintenance/clean")
    def clean():
        payload = request.get_json(silent=True) or {}
        try:
            return jsonify(
                {"removed": dashboard.clean_outputs(payload.get("profile", ""))}
            )
        except (PipelineBusyError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 409

    @app.post("/api/maintenance/reset-seen")
    def reset_seen():
        payload = request.get_json(silent=True) or {}
        try:
            return jsonify(
                {"removed": dashboard.reset_seen_database(payload.get("profile", ""))}
            )
        except (PipelineBusyError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 409

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local JobSiphon dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()

    service = JobSiphonService()
    atexit.register(service.stop)
    app = create_app(service)
    print(f"JobSiphon dashboard: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
