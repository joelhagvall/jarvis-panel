"""Silent calendar topics come from server.env, not the code: unset hides nothing, set hides whole words."""
import importlib
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "server/agents")]
import silent_topics  # noqa: E402


class SilentTopicsTest(unittest.TestCase):
    def test_unset_hides_nothing_and_adds_no_prompt_rule(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JARVIS_SILENT_TOPICS", None)
            self.assertFalse(silent_topics.silent("Dammsuga hallen"))
            self.assertEqual(silent_topics.instructions(), "")
            import prompts
            prompts = importlib.reload(prompts)
            self.assertNotIn("<<SILENT_TOPICS>>", prompts.INSTRUCTIONS)
            self.assertNotIn("inte heller deras kalenderhändelser", prompts.INSTRUCTIONS)

    def test_set_hides_whole_words_and_names_them_in_the_prompt(self):
        env = {"JARVIS_SILENT_TOPICS": r"dammsug\w*|vacuum\w*", "JARVIS_SILENT_TOPICS_NAME": "dammsugningen"}
        with patch.dict(os.environ, env):
            for title in ("Dammsuga hallen", "DAMMSUGAREN fram", "Vacuum the hall", "Dammsug-påminnelse"):
                self.assertTrue(silent_topics.silent(title), title)
            self.assertFalse(silent_topics.silent("Handla mat"))
            import prompts
            prompts = importlib.reload(prompts)
            self.assertIn("Nämn aldrig dammsugningen, inte heller deras kalenderhändelser", prompts.INSTRUCTIONS)
        importlib.reload(prompts)   # back to the environment without them


if __name__ == "__main__":
    unittest.main()
