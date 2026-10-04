#!/usr/bin/env python3
"""Inphic Mouse Button Remapper - remaps extra mouse buttons to keyboard shortcuts.

Grabs the physical mouse device so remapped buttons never reach the compositor,
then forwards all other mouse events (movement, scroll, regular clicks) through
a virtual mouse device via uinput. Remapped buttons trigger keyboard shortcuts
through a separate virtual keyboard.
"""

import argparse
import fcntl
import logging
import os
import select
import signal
import time
from pathlib import Path

import yaml
from evdev import InputDevice, UInput, ecodes, list_devices

LOG = logging.getLogger("mouse-config")

# Reverse lookup for ALL event codes (ecodes.KEY only covers keyboard keys, not BTN_*)
_CODE_NAMES: dict[int, str] = {}
for _name in dir(ecodes):
    if _name.startswith(("KEY_", "BTN_")):
        _val = getattr(ecodes, _name)
        if isinstance(_val, int):
            _CODE_NAMES[_val] = _name


class MouseMapper:
    """Grab input device, filter remapped buttons, forward the rest."""

    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self.config = {}
        self._load_config()

        self.vkbd: UInput | None = None  # virtual keyboard for key combos
        self.vmouse: UInput | None = None  # virtual mouse for forwarding events
        self.grabbed: dict[int, InputDevice] = {}  # fd -> grabbed device
        self.listen: dict[int, dict] = {}  # fd -> {dev, buttons}
        self.running = False
        self._reset_done = False

    # ── usb reset ───────────────────────────────────────────────────────

    def _reset_usb_device(self, vendor: int, product: int) -> bool:
        """Perform a software USB port reset for the device matching vendor/product ID.
        This forces the mouse to reset and exit BIOS Boot Protocol into full Report Protocol."""
        USBDEVFS_RESET = (ord("U") << 8) | 20  # 21780

        for uevent_path in Path("/sys/bus/usb/devices").glob("*/uevent"):
            try:
                with open(uevent_path) as f:
                    data = dict(line.strip().split("=", 1) for line in f if "=" in line)
                prod = data.get("PRODUCT", "")
                if prod:
                    parts = prod.split("/")
                    if len(parts) >= 2:
                        v, p = int(parts[0], 16), int(parts[1], 16)
                        if v == vendor and p == product:
                            devname = data.get("DEVNAME")
                            if devname:
                                dev_path = Path("/dev") / devname
                                if dev_path.exists():
                                    LOG.info("Resetting USB device at %s to exit Boot Protocol...", dev_path)
                                    try:
                                        fd = os.open(dev_path, os.O_WRONLY)
                                        try:
                                            fcntl.ioctl(fd, USBDEVFS_RESET, 0)
                                            LOG.info("USB reset successful for %s", dev_path)
                                            return True
                                        finally:
                                            os.close(fd)
                                    except PermissionError:
                                        LOG.warning(
                                            "Permission denied resetting %s. "
                                            "Make sure udev rules include SUBSYSTEM=='usb'.",
                                            dev_path,
                                        )
                                    except Exception as e:
                                        LOG.warning("Failed to reset USB device %s: %s", dev_path, e)
            except Exception:
                continue
        return False

    # ── config ──────────────────────────────────────────────────────────

    def _load_config(self):
        with open(self.config_path) as f:
            self.config = yaml.safe_load(f)

    def _get_code(self, name: str) -> int:
        code = getattr(ecodes, name, None)
        if code is None:
            raise ValueError(f"Unknown key code: {name}. Use names from evdev.ecodes (e.g. KEY_A, BTN_SIDE)")
        return code

    def _collect_mapped_codes(self) -> set[int]:
        """All event codes that are remapped (should not be forwarded)."""
        codes: set[int] = set()
        for dev_cfg in self.config.get("devices", []):
            for btn_cfg in dev_cfg.get("buttons", {}).values():
                if btn_cfg.get("type") == "key_combo":
                    for key_name in btn_cfg.get("keys", []):
                        codes.add(self._get_code(key_name))
        return codes

    def _collect_action_codes(self) -> dict[str, set[int]]:
        """Device name -> set of codes that trigger actions."""
        result: dict[str, set[int]] = {}
        for dev_cfg in self.config.get("devices", []):
            name = dev_cfg["name"]
            result[name] = set()
            for btn_name in dev_cfg.get("buttons", {}):
                result[name].add(self._get_code(btn_name))
        return result

    # ── uinput devices ──────────────────────────────────────────────────

    def _build_keyboard_caps(self) -> dict:
        mapped = self._collect_mapped_codes()
        # Include standard keys so udev classifies this device as a full keyboard (ID_INPUT_KEYBOARD=1)
        base_keys = {
            ecodes.KEY_ESC,
            ecodes.KEY_ENTER,
            ecodes.KEY_SPACE,
            ecodes.KEY_LEFTCTRL,
            ecodes.KEY_RIGHTCTRL,
            ecodes.KEY_LEFTSHIFT,
            ecodes.KEY_RIGHTSHIFT,
            ecodes.KEY_LEFTALT,
            ecodes.KEY_RIGHTALT,
            ecodes.KEY_LEFTMETA,
            ecodes.KEY_RIGHTMETA,
            ecodes.KEY_A,
            ecodes.KEY_C,
            ecodes.KEY_V,
            ecodes.KEY_Z,
        }
        all_keys = sorted(mapped | base_keys)
        return {ecodes.EV_KEY: all_keys}

    def _build_mouse_caps(self, src_dev: InputDevice, exclude_codes: set[int]) -> dict:
        """Copy src_dev capabilities minus the excluded key codes."""
        caps: dict[int, list] = {}
        for ev_type, codes in src_dev.capabilities(absinfo=False).items():
            if ev_type == ecodes.EV_KEY:
                filtered = [c for c in codes if c not in exclude_codes]
                if not filtered:
                    filtered = [ecodes.BTN_LEFT]  # dummy, won't be used
                caps[ev_type] = filtered
            elif ev_type != ecodes.EV_SYN:
                caps[ev_type] = codes
        return caps

    def _open_uinput_devices(self, grabbed_dev: InputDevice, exclude_codes: set[int]):
        # Virtual keyboard (only create once)
        if not self.vkbd:
            kbd_caps = self._build_keyboard_caps()
            self.vkbd = UInput(kbd_caps, name="inphic-virtual-kbd", version=0x1)
            LOG.info(
                "Virtual keyboard created: %s",
                [ecodes.KEY.get(c, _CODE_NAMES.get(c, str(c))) for c in kbd_caps.get(ecodes.EV_KEY, [])],
            )

        # Virtual mouse (only create once)
        if not self.vmouse:
            mouse_caps = self._build_mouse_caps(grabbed_dev, exclude_codes)
            self.vmouse = UInput(mouse_caps, name="inphic-virtual-mouse", version=0x1)
            LOG.info("Virtual mouse created")

    # ── device discovery ────────────────────────────────────────────────

    def _find_devices(self, action_codes: dict[str, set[int]]):
        """Find matching devices. The one with REL_X (actual pointer) gets
        grabbed and forwarded. Keyboard interfaces are listen-only."""
        vp_pairs = []
        for dev_cfg in self.config.get("devices", []):
            vp_pairs.append(
                (
                    int(dev_cfg["vendor"], 16),
                    int(dev_cfg["product"], 16),
                    dev_cfg,
                )
            )

        existing_paths = {d.path for d in self.grabbed.values()} | {info["dev"].path for info in self.listen.values()}

        for path in list_devices():
            if path in existing_paths:
                continue
            try:
                dev = InputDevice(path)
            except (PermissionError, OSError):
                continue
            for vendor, product, dev_cfg in vp_pairs:
                if dev.info.vendor == vendor and dev.info.product == product:
                    name = dev_cfg["name"]
                    caps = dev.capabilities()
                    # Only grab the actual pointer (REL_X), not scroll-only (REL_WHEEL)
                    if ecodes.REL_X in caps.get(ecodes.EV_REL, []):
                        try:
                            dev.grab()
                            self.grabbed[dev.fd] = dev
                            LOG.info("Grabbed %s (%s) on %s", name, dev.name.strip(), path)
                        except OSError as e:
                            LOG.warning("Could not grab %s (%s): %s", name, path, e)
                            try:
                                dev.close()
                            except OSError:
                                pass
                    else:
                        # Keyboard or scroll interface — listen only
                        buttons = dev_cfg.get("buttons", {})
                        self.listen[dev.fd] = {"dev": dev, "buttons": buttons}
                        LOG.info("Listening %s (%s) on %s", name, dev.name.strip(), path)

    # ── event handling ──────────────────────────────────────────────────

    def _execute_action(self, button_cfg: dict):
        action = button_cfg.get("type")
        if action == "key_combo":
            codes = [self._get_code(k) for k in button_cfg["keys"]]
            for code in codes:
                self.vkbd.write(ecodes.EV_KEY, code, 1)
            self.vkbd.syn()
            time.sleep(0.05)
            for code in reversed(codes):
                self.vkbd.write(ecodes.EV_KEY, code, 0)
            self.vkbd.syn()
        elif action == "command":
            import subprocess

            subprocess.Popen(
                button_cfg["command"],
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            LOG.warning("Unknown action type: %s", action)

    # ── main loop ───────────────────────────────────────────────────────

    def run(self):
        self.running = True
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        # 1. Reset USB device on startup to pull it out of BIOS Boot Protocol into Report Protocol
        if not self._reset_done:
            reset_performed = False
            for dev_cfg in self.config.get("devices", []):
                try:
                    v = int(dev_cfg["vendor"], 16)
                    p = int(dev_cfg["product"], 16)
                    if self._reset_usb_device(v, p):
                        reset_performed = True
                except Exception as e:
                    LOG.debug("USB reset attempt error: %s", e)
            if reset_performed:
                # Give kernel and udev time to re-enumerate the device
                time.sleep(1.0)
            self._reset_done = True

        action_codes = self._collect_action_codes()
        exclude_codes: set[int] = set()
        for codes in action_codes.values():
            exclude_codes.update(codes)

        device_buttons: dict[int, dict] = {}
        all_fds: set[int] = set()

        def refresh_devices():
            nonlocal all_fds
            self._find_devices(action_codes)
            if self.grabbed and not self.vmouse:
                grabbed_dev = next(iter(self.grabbed.values()))
                self._open_uinput_devices(grabbed_dev, exclude_codes)

            device_buttons.clear()
            for fd, dev in self.grabbed.items():
                device_buttons[fd] = {}
                for cfg in self.config.get("devices", []):
                    device_buttons[fd].update(cfg.get("buttons", {}))
            for fd, info in self.listen.items():
                device_buttons[fd] = info["buttons"]

            all_fds = set(self.grabbed.keys()) | set(self.listen.keys())
            if all_fds:
                LOG.info(
                    "Active: %d grabbed device(s), %d listen-only device(s)",
                    len(self.grabbed),
                    len(self.listen),
                )

        refresh_devices()

        if not all_fds:
            LOG.warning("No matching devices found. Waiting for mouse to be connected...")

        while self.running:
            if not all_fds:
                time.sleep(1.0)
                refresh_devices()
                continue

            try:
                r, _, _ = select.select(list(all_fds), [], [], 1.0)
            except (OSError, ValueError):
                break

            for fd in r:
                if fd in self.grabbed:
                    dev = self.grabbed[fd]
                    try:
                        events = tuple(dev.read())
                    except OSError:
                        LOG.warning("Grabbed device disconnected or error (fd %d)", fd)
                        self.grabbed.pop(fd, None)
                        all_fds.discard(fd)
                        try:
                            dev.close()
                        except Exception:
                            pass
                        continue

                    for event in events:
                        if event.type == ecodes.EV_KEY and event.code in exclude_codes:
                            # Remapped button
                            btn_name = _CODE_NAMES.get(event.code, f"CODE_{event.code}")
                            buttons = device_buttons.get(fd, {})
                            if event.value == 1 and btn_name in buttons:
                                LOG.info("%s pressed → action", btn_name)
                                self._execute_action(buttons[btn_name])
                            # suppress (don't forward to virtual mouse)
                        elif self.vmouse:
                            # Forward everything else to virtual mouse
                            self.vmouse.write(event.type, event.code, event.value)

                elif fd in self.listen:
                    info = self.listen[fd]
                    try:
                        events = tuple(info["dev"].read())
                    except OSError:
                        LOG.warning("Listen device disconnected or error (fd %d)", fd)
                        self.listen.pop(fd, None)
                        all_fds.discard(fd)
                        try:
                            info["dev"].close()
                        except Exception:
                            pass
                        continue

                    for event in events:
                        if event.type == ecodes.EV_KEY and event.value == 1:
                            btn_name = _CODE_NAMES.get(event.code, f"CODE_{event.code}")
                            buttons = device_buttons.get(fd, {})
                            if btn_name in buttons:
                                LOG.info("%s pressed → action", btn_name)
                                self._execute_action(buttons[btn_name])

        self._cleanup()

    def _handle_signal(self, signum, frame):
        LOG.info("Shutting down...")
        self.running = False

    def _cleanup(self):
        for dev in self.grabbed.values():
            try:
                dev.ungrab()
            except OSError:
                pass
            try:
                dev.close()
            except OSError:
                pass
        for info in self.listen.values():
            try:
                info["dev"].close()
            except OSError:
                pass
        if self.vkbd:
            self.vkbd.close()
            self.vkbd = None
        if self.vmouse:
            self.vmouse.close()
            self.vmouse = None


def main():
    parser = argparse.ArgumentParser(description="Inphic Mouse Button Remapper")
    parser.add_argument(
        "-c",
        "--config",
        default=str(Path(__file__).parent / "config.yaml"),
        help="Path to config file",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    MouseMapper(args.config).run()


if __name__ == "__main__":
    main()
