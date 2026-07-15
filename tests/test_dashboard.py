from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from dashboard_server import JobSiphonService, create_app


FIELDS = [
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


class DashboardTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        profile_dir = self.root / "profiles"
        profile_dir.mkdir()
        (profile_dir / "test-profile.json").write_text(
            json.dumps(
                {
                    "slug": "test-profile",
                    "name": "Test Profile",
                    "description": "Profile used by dashboard tests.",
                    "resume_path": "profiles/resumes/test-profile.txt",
                    "search_terms": ["Test Engineer"],
                    "locations": ["Austin, TX"],
                    "role_terms": ["engineer"],
                    "preferred_terms": ["testing"],
                }
            ),
            encoding="utf-8",
        )
        self.service = JobSiphonService(self.root)
        self.paths = self.service._profile("test-profile").paths(self.root)
        self.paths.ensure_directories()
        self.service._llm_status = lambda: {
            "provider": "freellmapi",
            "label": "FreeLLM API",
            "configured": False,
            "online": False,
            "model": "auto",
            "models": [],
        }
        self.app = create_app(self.service)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.service.stop()
        self.tempdir.cleanup()

    def write_jobs(self, filename: str, rows: list[dict[str, str]]) -> None:
        with (self.paths.root / filename).open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def test_overview_reports_files_and_seen_history(self) -> None:
        self.write_jobs(
            "apply_list.csv",
            [
                {
                    "score": "82",
                    "title": "Implementation Engineer",
                    "company": "Signal Works",
                    "url": "https://example.com/job",
                }
            ],
        )
        self.paths.resume.write_text("Candidate resume", encoding="utf-8")
        self.paths.cache.write_text("[]", encoding="utf-8")
        with sqlite3.connect(self.paths.database) as conn:
            conn.execute(
                "CREATE TABLE seen_jobs (url TEXT PRIMARY KEY, evaluated_at TEXT)"
            )
            conn.execute(
                "INSERT INTO seen_jobs VALUES (?, ?)",
                ("https://example.com/job", "2026-07-15"),
            )

        response = self.client.get("/api/overview")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["counts"]["current"], 1)
        self.assertEqual(payload["counts"]["seen"]["count"], 1)
        self.assertTrue(payload["resume"]["configured"])
        self.assertTrue(payload["cache"]["available"])

    def test_jobs_can_be_searched_and_filtered_by_score(self) -> None:
        self.write_jobs(
            "master_list.csv",
            [
                {
                    "score": "88",
                    "title": "Frontend Engineer",
                    "company": "Northstar",
                    "location": "Remote",
                    "reason": "Strong React fit",
                    "url": "https://example.com/frontend",
                },
                {
                    "score": "58",
                    "title": "Support Engineer",
                    "company": "Harbor",
                    "location": "Austin, TX",
                    "reason": "Client-facing role",
                    "url": "https://example.com/support",
                },
            ],
        )

        response = self.client.get("/api/jobs?list=master&q=northstar&min_score=70")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["title"], "Frontend Engineer")

    def test_maintenance_endpoints_remove_only_requested_state(self) -> None:
        self.paths.apply_csv.write_text("score\n", encoding="utf-8")
        self.paths.apply_markdown.write_text("# Apply", encoding="utf-8")
        self.paths.master_csv.write_text("score\n", encoding="utf-8")
        with sqlite3.connect(self.paths.database) as conn:
            conn.execute(
                "CREATE TABLE seen_jobs (url TEXT PRIMARY KEY, evaluated_at TEXT)"
            )

        payload = {"profile": "test-profile"}
        clean_response = self.client.post("/api/maintenance/clean", json=payload)
        reset_response = self.client.post("/api/maintenance/reset-seen", json=payload)

        self.assertEqual(clean_response.status_code, 200)
        self.assertEqual(reset_response.status_code, 200)
        self.assertFalse(self.paths.apply_csv.exists())
        self.assertFalse(self.paths.apply_markdown.exists())
        self.assertFalse(self.paths.database.exists())
        self.assertTrue(self.paths.master_csv.exists())

    def test_index_renders_dashboard_shell(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"JobSiphon", response.data)
        self.assertIn(b"Run search", response.data)
        self.assertNotIn(b"control room", response.data)

    def test_overview_reports_configured_llm_provider(self) -> None:
        self.service._llm_status = lambda: {
            "provider": "freellmapi",
            "label": "FreeLLM API",
            "configured": True,
            "online": True,
            "model": "auto",
            "models": ["model-one", "model-two"],
        }

        response = self.client.get("/api/overview")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["model"], "auto")
        self.assertEqual(payload["llm"]["provider"], "freellmapi")

    def test_pipeline_process_updates_progress_and_completes(self) -> None:
        script = self.root / "start.sh"
        script.write_text(
            "#!/usr/bin/env bash\n"
            'echo "args: $*"\n'
            "echo 'Starting job discovery pipeline'\n"
            "echo 'Scoring phase: 2 jobs queued'\n"
            "echo 'Scoring progress: 2/2 jobs scored'\n"
            "echo 'Pipeline complete'\n",
            encoding="utf-8",
        )

        started = self.service.start("score-only", "test-profile")
        deadline = time.monotonic() + 2
        while self.service.pipeline_status()["stage"] != "complete":
            if time.monotonic() > deadline:
                self.fail("pipeline did not complete within the test deadline")
            time.sleep(0.01)

        status = self.service.pipeline_status()
        messages = [entry["message"] for entry in self.service.logs()]
        self.assertTrue(started["running"])
        self.assertEqual(status["exit_code"], 0)
        self.assertEqual(status["profile"], "test-profile")
        self.assertEqual(status["percent"], 100)
        self.assertTrue(any("2/2" in message for message in messages))
        self.assertTrue(
            any("--profile test-profile" in message for message in messages)
        )

    def test_streaming_progress_uses_real_counters_without_fake_percent(self) -> None:
        self.service._append_log(
            "Pipeline progress: searches 4/8 | found 420 | unique 120 | "
            "queued 18 | scored 7 | matches 2"
        )

        status = self.service.pipeline_status()

        self.assertEqual(status["stage"], "streaming")
        self.assertIsNone(status["percent"])
        self.assertEqual(status["counters"]["searches_done"], 4)
        self.assertEqual(status["counters"]["found"], 420)
        self.assertEqual(status["counters"]["scored"], 7)
        self.assertEqual(status["counters"]["matches"], 2)


if __name__ == "__main__":
    unittest.main()
