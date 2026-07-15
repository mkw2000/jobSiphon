from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_profiles import JobProfile
from jobs_core import ScrapeBatch
from main import append_cache_batch, load_cached_jobs, run_streaming_discovery
from tests.profile_helpers import profile_payload


class StreamingPipelineTestCase(unittest.TestCase):
    def test_jsonl_cache_survives_incremental_batches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw_jobs.json"
            path.write_text("", encoding="utf-8")
            append_cache_batch(path, [{"url": "one"}])
            append_cache_batch(path, [{"url": "two"}])

            self.assertEqual(
                load_cached_jobs(path), [{"url": "one"}, {"url": "two"}]
            )

    def test_legacy_array_cache_remains_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw_jobs.json"
            payload = [{"url": "legacy"}]
            path.write_text(json.dumps(payload), encoding="utf-8")

            self.assertEqual(load_cached_jobs(path), payload)

    def test_incomplete_jsonl_record_does_not_discard_valid_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw_jobs.json"
            path.write_text(
                '{"url": "one"}\n{"title": "unfinished\n', encoding="utf-8"
            )

            with self.assertLogs("job-pipeline", level="WARNING") as logs:
                jobs = load_cached_jobs(path)

            self.assertEqual(jobs, [{"url": "one"}])
            self.assertIn("recovered 1 valid jobs", "\n".join(logs.output))

    def test_unicode_line_separator_inside_description_is_not_a_record_break(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw_jobs.json"
            payload = {
                "url": "https://example.com/unicode",
                "description": "First paragraph\u2028Second paragraph",
            }
            path.write_text(
                json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
            )

            self.assertEqual(load_cached_jobs(path), [payload])

    @patch("main.evaluated_row")
    @patch("main.stream_scraper_batches")
    def test_match_is_persisted_before_discovery_run_ends(
        self, stream_scrapers, evaluate
    ) -> None:
        raw = {
            "title": "Widget Engineer",
            "company": "Example",
            "url": "https://example.com/job",
            "description": "Build widgets with Python.",
            "location": "Austin, TX",
            "date_posted": "2026-01-01",
            "employment_type": "Full-time",
            "source": "indeed",
        }
        stream_scrapers.return_value = (1, iter([ScrapeBatch("indeed", [raw])]))
        evaluate.return_value = {
            "profile": "engineering-search",
            "score": 80,
            "llm_score": 75,
            "title": raw["title"],
            "company": raw["company"],
            "location": raw["location"],
            "employment_type": raw["employment_type"],
            "source": raw["source"],
            "date_found": "2026-01-01",
            "fit_signals": "target role family",
            "reason": "Relevant experience.",
            "url": raw["url"],
            "description": raw["description"],
        }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = JobProfile.from_dict(
                profile_payload(
                    "engineering-search",
                    search_terms=["Widget Engineer"],
                    role_terms=["widget engineer"],
                    preferred_terms=["python"],
                ),
                Path("engineering.json"),
            )
            paths = profile.paths(root)
            paths.ensure_directories()
            paths.resume.write_text("SKILLS\nLanguages: Python", encoding="utf-8")

            run_streaming_discovery(profile, paths, set())

            self.assertIn(raw["url"], paths.apply_csv.read_text(encoding="utf-8"))
            self.assertIn(raw["url"], paths.master_csv.read_text(encoding="utf-8"))
            self.assertEqual(load_cached_jobs(paths.cache), [raw])


if __name__ == "__main__":
    unittest.main()
