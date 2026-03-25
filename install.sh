#!/usr/bin/env bash
# install.sh — Install webcam-stream as a systemd service
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="webcam-stream"

echo "=== Webcam Stream Installer ==="

# Check for root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo ./install.sh"
    exit 1
fi

# Install dependencies
echo "[1/4] Installing dependencies..."
apt-get update -qq
apt-get install -y -qq ffmpeg v4l-utils python3 curl git > /dev/null

# Make main script executable
chmod +x "$SCRIPT_DIR/webcam-stream.py"

# Create systemd service
echo "[2/4] Creating systemd service..."
cat > /etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=Webcam Streaming Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 ${SCRIPT_DIR}/webcam-stream.py
WorkingDirectory=${SCRIPT_DIR}
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

# Optional env overrides — uncomment and edit as needed:
# Environment=STREAM_PORT=8080
# Environment=CAMERA_DEVICE=/dev/video0
# Environment=RESOLUTION=1280x720
# Environment=FRAMERATE=30
# Environment=WIFI_TIMEOUT=120

[Install]
WantedBy=multi-user.target
EOF

# Reload and enable
echo "[3/4] Enabling service..."
systemctl daemon-reload
systemctl enable ${SERVICE_NAME}.service

echo "[4/4] Done!"
echo ""
echo "Commands:"
echo "  Start now:    sudo systemctl start ${SERVICE_NAME}"
echo "  View logs:    sudo journalctl -u ${SERVICE_NAME} -f"
echo "  Stop:         sudo systemctl stop ${SERVICE_NAME}"
echo "  Disable:      sudo systemctl disable ${SERVICE_NAME}"
echo ""
echo "The service will start automatically on boot."
