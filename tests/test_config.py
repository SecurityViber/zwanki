import copy
import tempfile
import unittest
from pathlib import Path

from zwanki.config import DEFAULTS, load_config, parse_config, save_config


class ParseConfigTest(unittest.TestCase):
    def test_defaults_are_valid(self):
        self.assertEqual(parse_config(DEFAULTS), DEFAULTS)

    def test_missing_sections_get_defaults(self):
        self.assertEqual(parse_config({}), DEFAULTS)

    def test_empty_button_table_is_kept(self):
        self.assertEqual(parse_config({"buttons": {}})["buttons"], {})

    def test_rejects_bad_values(self):
        bad = [
            {"buttons": {"a": "explode"}},
            {"buttons": {"nose": "good"}},
            {"big_buttons": {"press_threshold": 20, "release_threshold": 30}},
            {"big_buttons": {"press_threshold": 60.5}},
            {"trainer": {"mode": "warp"}},
            {"trainer": {"enabled": "yes"}},
            {"general": {"colour": "orange"}},
            {"buttons": ["a"]},
        ]
        for cfg in bad:
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                parse_config(cfg)


class FileTest(unittest.TestCase):
    def test_first_run_creates_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            config, first_run, problem = load_config(path)
            self.assertEqual((config, first_run, problem), (DEFAULTS, True, None))
            self.assertTrue(path.exists())

    def test_round_trip(self):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["buttons"]["dpad_down"] = "trainer_easier"
        cfg["trainer"] = {"enabled": True, "mode": "grade"}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            save_config(cfg, path)
            self.assertEqual(load_config(path), (cfg, False, None))

    def test_broken_file_is_set_aside(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text("[buttons\n")
            config, _, problem = load_config(path)
            self.assertEqual(config, DEFAULTS)
            self.assertIn("invalid", problem)
            self.assertTrue(path.with_name("config.toml.broken").exists())


if __name__ == "__main__":
    unittest.main()
