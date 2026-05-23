---
name: mouse-config-maintenance
description: "Maintain and debug this repo's Ubuntu/Wayland mouse button remapper (systemd user service + uinput + evdev). Use when: the mouse remap “stops working”, service is inactive/crashing, permissions/udev issues, or you need to pin the daemon to Python 3.11 via uv + .venv."
argument-hint: 'Describe the symptom (inactive service, missing evdev, permission denied, device not found, wrong Python version).'
---

# Mouse Config Maintenance

This skill is for **this repository** (mouse button remapper on Ubuntu/Wayland using `uinput`, `systemd --user`, and Python `evdev`).

## When to Use

- Remap suddenly stops working
- `mouse-config` service is `inactive (dead)` or `activating (auto-restart)`
- Logs show `ModuleNotFoundError: No module named evdev`
- Logs show permission errors opening `/dev/input/event*` or `/dev/uinput`
- The daemon is running with the wrong Python version and you want **Python 3.11**

## Quick Checks (fast triage)

Run these commands and interpret the results:

- Service state:
  - `systemctl --user is-enabled mouse-config && systemctl --user is-active mouse-config`
  - `systemctl --user status mouse-config --no-pager -l`
- Recent logs:
  - `journalctl --user -u mouse-config -n 120 --no-pager`

**Healthy** usually includes log lines like:
- `Grabbed ... on /dev/input/eventX`
- `Virtual keyboard created: ...`
- `Virtual mouse created`

## Procedure

### 1) Start (or restart) the daemon

- Start:
  - `systemctl --user start mouse-config`
- Restart (after config changes):
  - `systemctl --user restart mouse-config`
- Follow logs live while testing buttons:
  - `journalctl --user -u mouse-config -f`

Verification: press mapped buttons (`BTN_SIDE`, `BTN_EXTRA`) and look for `... pressed → action`.

### 2) If the service is enabled but not running

- Check if it’s enabled for the graphical session:
  - `ls -l ~/.config/systemd/user/graphical-session.target.wants/ | grep mouse-config || true`
- Re-enable cleanly:
  - `systemctl --user disable mouse-config || true`
  - `systemctl --user enable --now mouse-config`

If this repo’s installer is available, prefer:
- `sudo ./install.sh`

### 3) If logs show missing Python modules (`evdev`, `yaml`)

Preferred (repo-managed environment):
- Ensure `uv` exists: `uv --version`
- Create/recreate venv (Python 3.11):
  - `uv python install 3.11`
  - `uv venv --python 3.11 --seed --clear .venv`
- If `evdev` build fails with `cc: No such file or directory`:
  - `sudo apt-get update -y && sudo apt-get install -y build-essential`
- Install deps into the venv:
  - `.venv/bin/python -m pip install -U pip`
  - `.venv/bin/python -m pip install evdev pyyaml`

Then restart:
- `systemctl --user restart mouse-config`

### 4) If you need to pin the daemon to Python 3.11

Confirm what systemd is running:
- `SYSTEMD_PAGER=cat systemctl --user show mouse-config -p ExecStart --value`

Target outcome: `ExecStart` begins with something like `.../.venv/bin/python` and `.venv/bin/python --version` shows `Python 3.11.x`.

If the unit is not using `.venv/bin/python`, re-run the installer:
- `sudo ./install.sh`

Or edit the unit and reload:
- `systemctl --user edit --full mouse-config`
- Set `ExecStart=/ABS/PATH/TO/repo/.venv/bin/python /ABS/PATH/TO/repo/mouse_map.py -c /ABS/PATH/TO/repo/config.yaml`
- `systemctl --user daemon-reload && systemctl --user restart mouse-config`

### 5) If you hit permission errors (`/dev/input/event*` or `/dev/uinput`)

- Confirm device node permissions:
  - `ls -l /dev/uinput`
- Confirm you’re in the expected group:
  - `id`

This repo expects udev rules granting `plugdev` access to the mouse and `uinput`.
After changing group membership, you usually need **logout/login**.

Reload rules if needed:
- `sudo udevadm control --reload-rules`
- `sudo udevadm trigger --subsystem-match=input`
- `sudo udevadm trigger --subsystem-match=misc`

### 6) If it says “No matching devices found”

- Make sure `config.yaml` device `vendor` and `product` match the real device.
- Use `evdev` to list devices and IDs (run from the repo venv):
  - `.venv/bin/python -c "from evdev import InputDevice, list_devices;\nfor p in list_devices():\n d=InputDevice(p);\n print(p, hex(d.info.vendor), hex(d.info.product), d.name)"`

Update `config.yaml` accordingly, then restart the service.

## Completion Criteria

- `systemctl --user is-active mouse-config` returns `active`
- Logs show `Grabbed ...` and `Virtual ... created`
- Pressing mapped buttons produces `... pressed → action` in `journalctl -f`
- If required: `SYSTEMD_PAGER=cat systemctl --user show mouse-config -p ExecStart --value` points to `.venv/bin/python` and `.venv/bin/python --version` is `3.11.x`
