from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from jobs_core import scrape_pdxpipeline, scrape_wellfound, stream_scraper_batches


PDX_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
 xmlns:content="http://purl.org/rss/1.0/modules/content/"
 xmlns:job_listing="https://www.pdxpipeline.com">
  <channel>
    <item>
      <title>Production Assistant - Portland, OR</title>
      <link>https://www.pdxpipeline.com/jobs/production-assistant/</link>
      <pubDate>Tue, 14 Jul 2026 18:34:22 +0000</pubDate>
      <description>Assist with daily production.</description>
      <content:encoded><![CDATA[<p>Assist with daily production and inventory.</p>]]></content:encoded>
      <job_listing:location>Portland, OR</job_listing:location>
      <job_listing:job_type>Full Time</job_listing:job_type>
      <job_listing:company>Example Company</job_listing:company>
    </item>
  </channel>
</rss>
"""


class LocalSourceTestCase(unittest.TestCase):
    @patch("jobs_core.safe_get")
    def test_pdx_pipeline_feed_is_normalized(self, safe_get: Mock) -> None:
        safe_get.return_value = Mock(status_code=200, text=PDX_FEED)

        jobs = scrape_pdxpipeline()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["source"], "pdxpipeline")
        self.assertEqual(jobs[0]["company"], "Example Company")
        self.assertEqual(jobs[0]["location"], "Portland, OR")
        self.assertEqual(jobs[0]["employment_type"], "Full Time")
        self.assertIn("daily production and inventory", jobs[0]["description"])

    @patch("jobs_core.runtime_value")
    @patch("jobs_core.requests.post")
    def test_wellfound_actor_results_are_normalized(
        self, post: Mock, runtime_value: Mock
    ) -> None:
        runtime_value.side_effect = lambda name: {
            "APIFY_TOKEN": "secret-token",
            "WELLFOUND_APIFY_ACTOR": "blackfalcondata/wellfound-scraper",
            "WELLFOUND_MAX_CHARGE_USD": "1.00",
        }.get(name)
        post.return_value.json.return_value = [
            {
                "title": "Implementation Engineer",
                "companyName": "Startup Example",
                "portalUrl": "https://wellfound.com/jobs/123-implementation-engineer",
                "description": "Integrate APIs for customer deployments.",
                "locationNames": ["Portland, OR"],
                "remote": True,
                "jobType": "Full-time",
                "postedAt": "2026-07-15T12:00:00Z",
                "compensation": "$100k-$130k",
            }
        ]

        jobs = scrape_wellfound({"enabled": True, "maxResults": 25})

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["source"], "wellfound")
        self.assertEqual(jobs[0]["company"], "Startup Example")
        self.assertIn("Portland, OR", jobs[0]["location"])
        self.assertIn("Remote", jobs[0]["location"])
        self.assertIn("$100k-$130k", jobs[0]["description"])
        request = post.call_args
        self.assertNotIn("token", request.kwargs["params"])
        self.assertEqual(request.kwargs["params"]["maxItems"], 25)
        self.assertEqual(
            request.kwargs["headers"]["Authorization"], "Bearer secret-token"
        )

    @patch("jobs_core.runtime_value", return_value=None)
    @patch("jobs_core.requests.post")
    def test_wellfound_is_skipped_without_an_apify_token(
        self, post: Mock, _runtime_value: Mock
    ) -> None:
        self.assertEqual(scrape_wellfound({"enabled": True}), [])
        post.assert_not_called()

    @patch("jobs_core.scrape_jobspy_combo")
    @patch("jobs_core.scrape_pdxpipeline")
    def test_stream_emits_each_search_as_it_finishes(
        self, pdx: Mock, jobspy_combo: Mock
    ) -> None:
        pdx.return_value = [{"title": "Local", "url": "https://example.com/local"}]
        jobspy_combo.return_value = [
            {"title": "Engineer", "url": "https://example.com/engineer"}
        ]

        total, batches = stream_scraper_batches(
            ["Engineer"],
            ["Austin, TX"],
            include_remote=False,
            enabled_sources=["pdxpipeline"],
        )
        emitted = list(batches)

        self.assertEqual(total, 2)
        self.assertEqual(len(emitted), 2)
        self.assertEqual(sum(len(batch.jobs) for batch in emitted), 2)


if __name__ == "__main__":
    unittest.main()
