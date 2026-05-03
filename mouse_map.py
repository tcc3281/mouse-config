#!/usr/bin/env python3
"""Inphic Mouse Button Remapper - remaps extra mouse buttons to keyboard shortcuts.

Reads EV_KEY events from configured input devices and simulates keyboard
input via uinput when a mapped button is pressed.
"""

import argparse
import logging
import select
import signal
import sys
import time
from pathlib import Path

import yaml
from evdev import InputDevice, UInput, ecodes, list_devices

LOG = logging.getLogger("mouse-config")

# Reverse lookup for ALL event codes (ecodes.KEY only covers keyboard keys, not BTN_*)
_EVENT_NAMES: dict[int, str] = {}
for _name in dir(ecodes):
    if _name.startswith(("KEY_", "BTN_")):
        _val = getattr(ecodes, _name)
        if isinstance(_val, int):
            _EVENT_NAMES[_val] = _name


def find_uis() -> dict:
    """Find all UInput devices with our name."""
    import fcntl, struct
    result = {}
    for path in Path("/sys/devices/virtual/input").glob("*/name"):
        if path.read_text().strip() == "inphic-virtual-kbd":
            event_dev = list(path.parent.glob("event*"))
            if event_dev:
                result[path.parent.name] = str(Path("/dev/input") / event_dev[0].name)
    return result


class MouseMapper:
    """Monitor input devices and remap buttons to actions."""

    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self.config = {}
        self.devices: dict[int, dict] = {}
        self.ui = None
        self.running = False
        self._load_config()

    def _load_config(self):
        with open(self.config_path) as f:
            self.config = yaml.safe_load(f)

    def _get_key_code(self, name: str) -> int:
        code = getattr(ecodes, name, None)
        if code is None:
            raise ValueError(f"Unknown key code: {name}. Use names from evdev.ecodes (e.g. KEY_A, BTN_SIDE)")
        return code

    def _build_uinput_caps(self) -> dict:
        """Collect all key codes needed across all button mappings."""
        all_keys = set()
        for device_cfg in self.config.get("devices", []):
            for btn_cfg in device_cfg.get("buttons", {}).values():
                if btn_cfg.get("type") == "key_combo":
                    for key_name in btn_cfg.get("keys", []):
                        all_keys.add(self._get_key_code(key_name))
        if not all_keys:
            all_keys.add(ecodes.KEY_ESC)  # required minimum capability
        return {ecodes.EV_KEY: list(all_keys)}

    def _open_uinput(self):
        caps = self._build_uinput_caps()
        try:
            self.ui = UInput(caps, name="inphic-virtual-kbd", version=0x1)
            LOG.info("uinput virtual keyboard created")
        except PermissionError:
            LOG.error("Cannot open /dev/uinput. Run install.sh first to set up permissions.")
            sys.exit(1)

    def _find_matching_devices(self) -> list[InputDevice]:
        """Find all input devices matching configured vendor/product pairs."""
        matched = []
        path_cfgs = []
        for device_cfg in self.config.get("devices", []):
            vendor = int(device_cfg["vendor"], 16)
            product = int(device_cfg["product"], 16)
            path_cfgs.append((vendor, product, device_cfg))

        for path in list_devices():
            try:
                dev = InputDevice(path)
            except PermissionError:
                continue
            for vendor, product, device_cfg in path_cfgs:
                if dev.info.vendor == vendor and dev.info.product == product:
                    matched.append((dev, device_cfg))
                    LOG.info(
                        "Found %s (%s) on %s", device_cfg["name"], dev.name.strip(), path
                    )
        return matched

    def _execute_action(self, button_cfg: dict):
        action = button_cfg.get("type")
        if action == "key_combo":
            codes = [self._get_key_code(k) for k in button_cfg["keys"]]
            for code in codes:
                self.ui.write(ecodes.EV_KEY, code, 1)
            self.ui.syn()
            time.sleep(0.05)
            for code in reversed(codes):
                self.ui.write(ecodes.EV_KEY, code, 0)
            self.ui.syn()
        elif action == "command":
            import subprocess
            subprocess.Popen(
                button_cfg["command"], shell=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            LOG.warning("Unknown action type: %s", action)

    def run(self):
        self.running = True
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        self._open_uinput()

        # Build fd → (device, button_map) lookup
        matched = self._find_matching_devices()
        if not matched:
            LOG.error("No matching devices found. Is the mouse plugged in?")
            sys.exit(1)

        for dev, device_cfg in matched:
            self.devices[dev.fd] = {
                "dev": dev,
                "buttons": device_cfg.get("buttons", {}),
            }

        LOG.info("Monitoring %d device(s) for button events", len(self.devices))

        while self.running:
            try:
                r, _, _ = select.select(list(self.devices.keys()), [], [], 1.0)
            except (OSError, ValueError):
                break
            for fd in r:
                info = self.devices.get(fd)
                if info is None:
                    continue
                try:
                    events = info["dev"].read()
                except OSError:
                    continue
                for event in events:
                    if event.type == ecodes.EV_KEY and event.value == 1:
                        ev_name = _EVENT_NAMES.get(event.code, f"CODE_{event.code}")
                        if ev_name in info["buttons"]:
                            LOG.info("Button %s pressed → executing action", ev_name)
                            self._execute_action(info["buttons"][ev_name])

        self._cleanup()

    def _handle_signal(self, signum, frame):
        LOG.info("Shutting down...")
        self.running = False

    def _cleanup(self):
        for info in self.devices.values():
            try:
                info["dev"].close()
            except OSError:
                pass
        if self.ui:
            self.ui.close()
            self.ui = None


def main():
    parser = argparse.ArgumentParser(description="Inphic Mouse Button Remapper")
    parser.add_argument(
        "-c", "--config",
        default=str(Path(__file__).parent / "config.yaml"),
        help="Path to config file (default: config.yaml next to this script)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    mapper = MouseMapper(args.config)
    mapper.run()


if __name__ == "__main__":
    main()
