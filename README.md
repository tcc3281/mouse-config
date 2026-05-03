# Inphic Mouse Config for Ubuntu

Remap extra mouse buttons to keyboard shortcuts (or shell commands) on Ubuntu/Wayland using `uinput`.

Works with Inphic and other "INSTANT USB GAMING MOUSE" devices (vendor `30fa`, product `1701`).

## Install

```bash
git clone <this-repo>
cd mouse-config
sudo ./install.sh   # sets up udev rules, uinput module, and systemd service
```

After install, the service auto-starts on login.

## Usage

### Edit bindings

Open `config.yaml` and change the button mappings:

```yaml
buttons:
  BTN_SIDE:
    type: key_combo
    keys: [KEY_LEFTCTRL, KEY_V]    # Ctrl+V

  BTN_EXTRA:
    type: key_combo
    keys: [KEY_LEFTCTRL, KEY_C]    # Ctrl+C
```

### Restart to apply

```bash
systemctl --user restart mouse-config
```

### Check status / logs

```bash
systemctl --user status mouse-config
journalctl --user -u mouse-config -f
```

### Supported actions

- `key_combo` — simulate a keyboard shortcut (list `KEY_*` names as shown in `config.yaml`)
- `command` — run any shell command

Find available key names:

```bash
python3 -c "from evdev import ecodes; print([k for k in dir(ecodes) if k.startswith(('KEY_','BTN_'))])"
```

## Requirements

- Ubuntu (tested on 22.04+)
- `python3-evdev` (auto-detected, installs via apt)
- `pyyaml` (auto-detected, installs via pip)

## How it works

The daemon reads raw input events from the mouse's `/dev/input/event*` interfaces. When a mapped button is pressed, it creates a virtual keyboard via the kernel's `uinput` module and injects the configured key combination — no X11/Wayland-specific API needed.
