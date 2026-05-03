#!/bin/bash
# Install Inphic Mouse Button Remapper
# Run once to set up permissions and auto-start.
#
# Usage:
#   ./install.sh          (run as yourself, will ask for sudo when needed)
#   sudo ./install.sh     (run as root, will auto-detect your real user)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
UDEV_RULES="/etc/udev/rules.d/99-inphic-mouse.rules"

# Detect real user (works whether run with sudo or directly)
if [ -n "$SUDO_USER" ]; then
    REAL_USER="$SUDO_USER"
    REAL_UID=$(id -u "$REAL_USER")
    REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)
    ELEVATE=""           # already root
    AS_USER="sudo -u $REAL_USER env XDG_RUNTIME_DIR=/run/user/$REAL_UID DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$REAL_UID/bus"
else
    REAL_USER="$USER"
    REAL_HOME="$HOME"
    ELEVATE="sudo"       # need to escalate for system ops
    AS_USER=""           # already the right user
fi

SERVICE_DIR="$REAL_HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/mouse-config.service"

echo "=== Inphic Mouse Config Installer ==="
echo "Real user: $REAL_USER"
echo ""

# ── 1. Load uinput module ────────────────────────────────────────────
echo "[1/5] Loading uinput kernel module..."
if ! lsmod | grep -q uinput; then
    $ELEVATE modprobe uinput
    echo "      uinput loaded."
else
    echo "      uinput already loaded."
fi

# Ensure uinput loads at boot
if [ ! -f /etc/modules-load.d/uinput.conf ]; then
    echo uinput | $ELEVATE tee /etc/modules-load.d/uinput.conf > /dev/null
    echo "      Added uinput to modules-load.d for boot persistence."
fi

# ── 2. Create udev rules ─────────────────────────────────────────────
echo "[2/5] Creating udev rules..."
$ELEVATE tee "$UDEV_RULES" > /dev/null <<'UDEV'
# Inphic USB Gaming Mouse - grant read access to plugdev group
SUBSYSTEM=="input", ATTRS{idVendor}=="30fa", ATTRS{idProduct}=="1701", MODE="0660", GROUP="plugdev"

# uinput - grant read/write access to plugdev group
KERNEL=="uinput", MODE="0660", GROUP="plugdev"
UDEV
echo "      Wrote $UDEV_RULES"

# ── 3. Reload udev rules ─────────────────────────────────────────────
echo "[3/5] Reloading udev rules..."
$ELEVATE udevadm control --reload-rules
$ELEVATE udevadm trigger --subsystem-match=input
$ELEVATE udevadm trigger --subsystem-match=misc  # triggers uinput if loaded
echo "      udev rules reloaded."

# ── 4. Install systemd user service ───────────────────────────────────
echo "[4/5] Installing systemd user service..."
$AS_USER mkdir -p "$SERVICE_DIR"
$AS_USER tee "$SERVICE_FILE" > /dev/null <<SYSTEMD
[Unit]
Description=Inphic Mouse Button Remapper
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=$SCRIPT_DIR/mouse_map.py -c $SCRIPT_DIR/config.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
SYSTEMD
echo "      Wrote $SERVICE_FILE"

$AS_USER systemctl --user daemon-reload
echo "      Reloaded systemd user daemon."

# ── 5. Enable and start ──────────────────────────────────────────────
echo "[5/5] Enabling and starting service..."
$AS_USER systemctl --user enable --now mouse-config.service
echo "      Service enabled and started."

echo ""
echo "=== Installation complete ==="
echo ""
echo "Check status:  systemctl --user status mouse-config"
echo "View logs:     journalctl --user -u mouse-config -f"
echo "Edit bindings: $SCRIPT_DIR/config.yaml"
echo "Restart after edit: systemctl --user restart mouse-config"
