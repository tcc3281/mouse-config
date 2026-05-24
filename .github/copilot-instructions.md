# Copilot instructions for mouse-config

## Build / test / lint
- No automated test or lint commands are configured in this repo.
- Dependency setup (mirrors `install.sh`): `uv lock` then `uv sync --python 3.11 --locked` to create/update `.venv`. If `uv` is unavailable, `install.sh` falls back to system `python3`.

## Architecture
- `install.sh` performs system setup: loads `uinput`, writes udev rules for the mouse and `/dev/uinput`, and installs a systemd **user** unit that runs `mouse_map.py -c config.yaml`.
- `mouse_map.py` is the daemon. It loads `config.yaml`, finds devices by vendor/product, grabs the pointer interface (`REL_X`), creates virtual keyboard + mouse via `uinput`, forwards non-remapped events, and executes actions for remapped buttons.
- `config.yaml` defines `devices` with vendor/product **hex strings**, and per-device `buttons` keyed by evdev `BTN_*` names. Actions are `key_combo` (list of `KEY_*`) or `command` (shell string).

## Conventions
- Use evdev code names (`BTN_*`, `KEY_*`) in config and code; unknown names should raise (see `MouseMapper._get_code`).
- Service operations are via `systemctl --user` for `mouse-config.service`; after editing `config.yaml`, restart the user service.
- Logging uses the `mouse-config` logger; action execution is logged as `BTN_* pressed → action` for troubleshooting.
