# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Python daemon that remaps extra mouse buttons (Inphic and similar "INSTANT USB GAMING MOUSE" devices, vendor `30fa`, product `1701`) to keyboard shortcuts on Ubuntu/Wayland using `evdev` and `uinput`.

The daemon grabs the physical mouse device to intercept button events before they reach the compositor, forwards non-remapped events (movement, scroll, standard clicks) through a virtual mouse device, and injects keyboard shortcuts via a virtual keyboard.

## Commands

```bash
# Run the daemon directly (needs root for /dev/input access)
sudo python3 mouse_map.py

# Run with verbose logging
sudo python3 mouse_map.py -v

# Install (sets up udev rules, uinput module, systemd service)
sudo ./install.sh

# Start/stop/restart as systemd user service
systemctl --user restart mouse-config
systemctl --user status mouse-config

# View logs
journalctl --user -u mouse-config -f
```

No tests, linters, or formatters are configured in this project.

## Architecture

### Source files

- **`mouse_map.py`** — Single-module daemon. Contains `MouseMapper` class with all logic: config loading, device discovery, event loop, uinput device creation, and cleanup.
- **`main.py`** — Placeholder stub, not used. The real entry point is `mouse_map.py`.
- **`config.yaml`** — YAML config specifying device vendor/product IDs and button-to-action mappings.

### How it works

1. **Device discovery**: Scans `/dev/input/event*` devices, matches by vendor/product ID from config.
2. **Two interface types**: The pointer interface (has `REL_X` capability) is **grabbed** — events are intercepted and selectively forwarded. Keyboard/scroll-only interfaces are **listen-only** — button events are read but original events still reach the compositor (they don't carry pointer movement).
3. **Event loop**: Uses `select.select()` to wait on all device FDs. Remapped button presses trigger either key combos (via virtual keyboard) or shell commands. All other events are forwarded through a virtual mouse device so normal mouse functionality is preserved.
4. **uinput devices**: Two virtual devices are created — a keyboard (`inphic-virtual-kbd`) for key combos and a mouse (`inphic-virtual-mouse`) for forwarding events.
5. **Cleanup**: On SIGINT/SIGTERM, devices are ungrabbed and closed properly.

### Configuration format

```yaml
devices:
  - name: "INSTANT USB GAMING MOUSE"
    vendor: "30fa"
    product: "1701"
    buttons:
      BTN_SIDE:
        type: key_combo
        keys: [KEY_LEFTCTRL, KEY_V]
      BTN_EXTRA:
        type: command
        command: "notify-send 'hello'"
```

Supported action types: `key_combo` (list of `KEY_*` ecodes) and `command` (shell command).

### Key dependencies

- **evdev** — Read raw input events, create uinput virtual devices
- **pyyaml** — Parse config
- **uinput kernel module** — Required for creating virtual input devices
- **udev rules** — Grant `plugdev` group access to the mouse and uinput device
