from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from llm_client import chat_json, provider_status


def configured_value(name: str) -> str | None:
    return {
        "LLM_PROVIDER": "freellmapi",
        "FREELLMAPI_UNIFIED_API_KEY": "test-unified-key",
        "FREELLMAPI_BASE_URL": "http://localhost:3001/v1",
        "FREELLMAPI_MODEL": "auto",
    }.get(name)


class LlmClientTestCase(unittest.TestCase):
    @patch("llm_client.runtime_value", side_effect=configured_value)
    @patch("llm_client.requests.get")
    def test_freellm_status_uses_authenticated_models_endpoint(
        self, get: Mock, _runtime: Mock
    ) -> None:
        get.return_value.json.return_value = {
            "data": [{"id": "model-one"}, {"id": "model-two"}]
        }

        status = provider_status()

        self.assertTrue(status["online"])
        self.assertEqual(status["model"], "auto")
        self.assertEqual(status["models"], ["model-one", "model-two"])
        self.assertEqual(
            get.call_args.kwargs["headers"]["Authorization"],
            "Bearer test-unified-key",
        )

    @patch("llm_client.runtime_value", side_effect=configured_value)
    @patch("llm_client.requests.post")
    def test_freellm_chat_requests_structured_json(
        self, post: Mock, _runtime: Mock
    ) -> None:
        post.return_value.json.return_value = {
            "choices": [
                {"message": {"content": '{"score": 72, "reason": "Good fit"}'}}
            ]
        }
        schema = {
            "type": "object",
            "properties": {
                "score": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["score", "reason"],
        }

        result = chat_json([{"role": "user", "content": "score this"}], schema)

        self.assertEqual(result["score"], 72)
        request = post.call_args.kwargs
        self.assertEqual(request["json"]["model"], "auto")
        self.assertEqual(
            request["json"]["response_format"]["type"], "json_schema"
        )
        self.assertEqual(
            request["headers"]["Authorization"], "Bearer test-unified-key"
        )

    @patch("llm_client.runtime_value", return_value=None)
    def test_missing_freellm_key_is_reported_without_network_call(
        self, _runtime: Mock
    ) -> None:
        status = provider_status()

        self.assertFalse(status["configured"])
        self.assertFalse(status["online"])


if __name__ == "__main__":
    unittest.main()
