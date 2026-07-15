from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from job_profiles import JobProfile
from jobs_core import Job, init_db, update_seen
from main import (
    is_target_location,
    prefilter,
    requires_too_much_experience,
    role_signal_score,
)
from tests.profile_helpers import profile_payload


class ProfilePipelineTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engineering = JobProfile.from_dict(
            profile_payload(
                "engineering-search",
                search_terms=["Widget Engineer"],
                role_terms=["widget engineer"],
                preferred_terms=["python"],
            ),
            Path("engineering.json"),
        )
        cls.operations = JobProfile.from_dict(
            profile_payload(
                "operations-search",
                search_terms=["Warehouse Associate"],
                role_terms=["warehouse", "field service"],
            ),
            Path("operations.json"),
        )

    def job(self, title: str, location: str, description: str) -> Job:
        return Job(
            title=title,
            company="Example Company",
            url=f"https://example.com/{title.lower().replace(' ', '-')}",
            description=description,
            location=location,
            date_posted="2026-01-01",
            employment_type="Full-time",
            source="indeed",
        )

    def test_profiles_apply_their_own_role_signals(self) -> None:
        job = self.job(
            "Warehouse Associate",
            "Austin, TX",
            "Full-time warehouse role with training provided.",
        )

        self.assertEqual(prefilter([job], set(), self.operations), [job])
        self.assertEqual(prefilter([job], set(), self.engineering), [])

    def test_role_signal_score_is_profile_specific(self) -> None:
        job = self.job(
            "Widget Engineer",
            "Austin, TX",
            "Build widgets with Python.",
        )

        self.assertGreaterEqual(role_signal_score(job, self.engineering), 8)
        self.assertEqual(role_signal_score(job, self.operations), 0)

    def test_configured_metro_and_nearby_cities_are_accepted(self) -> None:
        for city in ("Austin, TX", "Round Rock, TX"):
            with self.subTest(city=city):
                job = self.job("Field Service Technician", city, "Field service work.")
                self.assertTrue(is_target_location(job, self.operations))

        wrong_region = self.job(
            "Field Service Technician", "Austin, MN", "Field service work."
        )
        self.assertFalse(is_target_location(wrong_region, self.operations))

    def test_experience_limit_is_profile_configurable(self) -> None:
        description = "Requires 5+ years of professional experience."

        self.assertTrue(requires_too_much_experience(description, 3))
        self.assertFalse(requires_too_much_experience(description, 5))
        self.assertFalse(requires_too_much_experience(description, None))

    def test_us_only_remote_profile_rejects_unspecified_global_listing(self) -> None:
        job = self.job(
            "Widget Engineer",
            "Remote",
            "Join our distributed product engineering team.",
        )
        job.source = "remoteok"

        self.assertFalse(is_target_location(job, self.engineering))

    def test_seen_job_update_refreshes_existing_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "seen.db"
            conn = init_db(database)
            conn.execute(
                "INSERT INTO seen_jobs (url, evaluated_at) VALUES (?, ?)",
                ("https://example.com/job", "2020-01-01"),
            )
            conn.commit()

            update_seen(conn, ["https://example.com/job"])
            refreshed = conn.execute(
                "SELECT evaluated_at FROM seen_jobs WHERE url = ?",
                ("https://example.com/job",),
            ).fetchone()[0]
            conn.close()

        self.assertNotEqual(refreshed, "2020-01-01")


if __name__ == "__main__":
    unittest.main()
