import subprocess
import unittest
from pathlib import Path

import yaml
from evdev import ecodes

from mouse_map import MouseMapper


class TestMouseConfig(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parent.parent
        self.config_path = self.repo_root / "config.yaml"

    def test_config_yaml_valid(self):
        """Test that config.yaml exists and parses valid YAML."""
        self.assertTrue(self.config_path.exists(), "config.yaml not found")
        with open(self.config_path) as f:
            data = yaml.safe_load(f)
        self.assertIn("devices", data)
        self.assertIsInstance(data["devices"], list)
        self.assertGreater(len(data["devices"]), 0)

        for dev in data["devices"]:
            self.assertIn("name", dev)
            self.assertIn("vendor", dev)
            self.assertIn("product", dev)
            self.assertIn("buttons", dev)
            for btn_name, action in dev["buttons"].items():
                self.assertTrue(hasattr(ecodes, btn_name), f"Unknown button code: {btn_name}")
                self.assertIn("type", action)
                if action["type"] == "key_combo":
                    self.assertIn("keys", action)
                    for key in action["keys"]:
                        self.assertTrue(hasattr(ecodes, key), f"Unknown key code: {key}")
                elif action["type"] == "command":
                    self.assertIn("command", action)

    def test_mouse_mapper_load_config(self):
        """Test MouseMapper initialization and config loading."""
        mapper = MouseMapper(str(self.config_path))
        mapped = mapper._collect_mapped_codes()
        self.assertIsInstance(mapped, set)
        self.assertGreater(len(mapped), 0)

        action_codes = mapper._collect_action_codes()
        self.assertIn("INSTANT USB GAMING MOUSE", action_codes)
        self.assertIn(ecodes.BTN_SIDE, action_codes["INSTANT USB GAMING MOUSE"])
        self.assertIn(ecodes.BTN_EXTRA, action_codes["INSTANT USB GAMING MOUSE"])

    def test_invalid_key_raises_error(self):
        """Test that invalid ecodes raise ValueError."""
        mapper = MouseMapper(str(self.config_path))
        with self.assertRaises(ValueError):
            mapper._get_code("NON_EXISTENT_KEY_12345")

    def test_keyboard_caps_generation(self):
        """Test _build_keyboard_caps contains mapped keys and standard base keys."""
        mapper = MouseMapper(str(self.config_path))
        caps = mapper._build_keyboard_caps()
        self.assertIn(ecodes.EV_KEY, caps)
        keys = set(caps[ecodes.EV_KEY])

        # Base keys must be included
        self.assertIn(ecodes.KEY_ESC, keys)
        self.assertIn(ecodes.KEY_ENTER, keys)
        self.assertIn(ecodes.KEY_LEFTCTRL, keys)
        # Mapped keys must be included
        self.assertIn(ecodes.KEY_C, keys)
        self.assertIn(ecodes.KEY_V, keys)

    def test_install_script_syntax(self):
        """Test that install.sh passes bash syntax validation."""
        install_sh = self.repo_root / "install.sh"
        self.assertTrue(install_sh.exists())
        result = subprocess.run(["bash", "-n", str(install_sh)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, f"install.sh syntax error: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
