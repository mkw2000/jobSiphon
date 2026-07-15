from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from ollama_config import select_model


class OllamaConfigTestCase(unittest.TestCase):
    def test_first_installed_model_is_used_without_an_override(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                select_model(["gemma4:latest", "another-model:latest"]),
                "gemma4:latest",
            )

    def test_override_without_latest_tag_matches_installed_model(self) -> None:
        with patch.dict(os.environ, {"OLLAMA_MODEL": "gemma4"}, clear=True):
            self.assertEqual(select_model(["gemma4:latest"]), "gemma4:latest")


if __name__ == "__main__":
    unittest.main()
