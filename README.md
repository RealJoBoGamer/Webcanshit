# Webcam Streaming Server

A Linux program that automatically streams a USB webcam feed over HTTP.

## What it does

1. **Waits for WiFi** — blocks at startup until a wireless (or wired) network connection is established
2. **Detects USB cameras** — scans `/dev/video*` for connected webcams
3. **Checks for updates** — compares against this GitHub repository for new versions
4. **Streams via HTTP** — starts an MJPEG streaming server accessible on the network
5. **Public internet access** — automatically exposes the stream to the internet via UPnP or SSH tunnel so anyone can view it without being on the same WiFi

## How Public Access Works

The server tries these methods in order to make your stream accessible from anywhere:

1. **UPnP port forwarding** — asks your router to forward the port directly (fastest, requires `miniupnpc`)
2. **SSH tunnel via localhost.run** — creates a free reverse tunnel, gives you a public URL (no signup needed)
3. **SSH tunnel via serveo.net** — fallback tunnel service

You get a shareable public URL in the console output. Set `TUNNEL_MODE=none` to disable.

## Requirements

- Linux with Python 3.6+
- `ffmpeg` — video capture and encoding
- `v4l-utils` — camera detection (optional but recommended)
- `openssh-client` — for SSH tunnel (recommended)
- `miniupnpc` — for UPnP port forwarding (optional)
- A USB webcam

## Quick Start

```bash
# Clone
git clone https://github.com/RealJoBoGamer/Webcanshit.git
cd Webcanshit

# Run directly
sudo apt install ffmpeg v4l-utils
python3 webcam-stream.py

# Or install as a systemd service (auto-start on boot)
sudo ./install.sh
sudo systemctl start webcam-stream
```

## Endpoints

| Path        | Description                    |
|-------------|--------------------------------|
| `/`         | Web page with live stream      |
| `/stream`   | Raw MJPEG stream               |
| `/snapshot`  | Single JPEG frame              |
| `/status`   | JSON status info               |

## Configuration

Set via environment variables:

| Variable        | Default     | Description                    |
|-----------------|-------------|--------------------------------|
| `STREAM_PORT`   | `8080`      | HTTP server port               |
| `CAMERA_DEVICE` | auto-detect | Camera path (e.g. `/dev/video0`) |
| `RESOLUTION`    | `640x480`   | Capture resolution             |
| `FRAMERATE`     | `30`        | Frames per second              |
| `WIFI_TIMEOUT`  | `120`       | Seconds to wait for WiFi       |
| `TUNNEL_MODE`   | `auto`      | `auto`, `upnp`, `ssh`, or `none` |

Example:
```bash
STREAM_PORT=9090 RESOLUTION=1280x720 python3 webcam-stream.py
```

## Service Management

After running `install.sh`:
```bash
sudo systemctl start webcam-stream    # Start
sudo systemctl stop webcam-stream     # Stop
sudo systemctl status webcam-stream   # Check status
sudo journalctl -u webcam-stream -f   # View logs
```
