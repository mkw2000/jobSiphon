from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from job_profiles import JobProfile, get_profile, load_profiles
from tests.profile_helpers import profile_payload, write_profile


class JobProfileTestCase(unittest.TestCase):
    def test_local_profiles_load_with_isolated_storage(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            write_profile(root, profile_payload("alpha-search"))
            write_profile(root, profile_payload("beta-search"))

            profiles = load_profiles(root)
            alpha_paths = profiles["alpha-search"].paths(root)
            beta_paths = profiles["beta-search"].paths(root)

            self.assertEqual(set(profiles), {"alpha-search", "beta-search"})
            self.assertNotEqual(alpha_paths.database, beta_paths.database)
            self.assertIn("alpha-search", str(alpha_paths.apply_csv))

    def test_profile_uses_compact_search_hubs_for_wider_accepted_area(self) -> None:
        payload = profile_payload(
            "metro-search",
            locations=["Austin, TX", "Round Rock, TX", "Georgetown, TX"],
        )
        profile = JobProfile.from_dict(payload, Path("synthetic.json"))

        self.assertEqual(profile.search_locations, ("Austin, TX",))
        self.assertLess(len(profile.search_locations), len(profile.locations))
        self.assertFalse(profile.wellfound.get("enabled", False))

    def test_first_sorted_local_profile_is_the_default(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            write_profile(root, profile_payload("beta-search"))
            write_profile(root, profile_payload("alpha-search"))

            self.assertEqual(get_profile(root).slug, "alpha-search")

    def test_invalid_profile_policy_is_rejected(self) -> None:
        payload = profile_payload("invalid", remote_policy="sometimes")

        with self.assertRaisesRegex(ValueError, "remote_policy"):
            JobProfile.from_dict(payload, Path("invalid.json"))


if __name__ == "__main__":
    unittest.main()
