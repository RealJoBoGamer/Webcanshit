#!/usr/bin/env python3
"""
Webcam Streaming Server
=======================
A Linux service that:
1. Waits for WiFi connectivity at startup
2. Detects connected USB cameras/webcams
3. Streams the webcam feed over HTTP on the public IP
4. Checks for new versions from the GitHub repository on startup
"""

import subprocess
import sys
import time
import os
import re
import signal
import threading
import json
import glob
import socket
import struct
import fcntl
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
VERSION = "1.1.0"
GITHUB_REPO = "RealJoBoGamer/Webcanshit"
STREAM_PORT = int(os.environ.get("STREAM_PORT", 8080))
CAMERA_DEVICE = os.environ.get("CAMERA_DEVICE", "")  # auto-detect if empty
RESOLUTION = os.environ.get("RESOLUTION", "640x480")
FRAMERATE = int(os.environ.get("FRAMERATE", 30))
WIFI_TIMEOUT = int(os.environ.get("WIFI_TIMEOUT", 120))  # seconds
WIFI_CHECK_INTERVAL = 2  # seconds
TUNNEL_MODE = os.environ.get("TUNNEL_MODE", "auto")  # auto, upnp, ssh, none

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
def log(msg):
    print(f"[webcam-stream] {msg}", flush=True)

def log_err(msg):
    print(f"[webcam-stream] ERROR: {msg}", flush=True, file=sys.stderr)

# ---------------------------------------------------------------------------
# 1. WiFi / Network connectivity
# ---------------------------------------------------------------------------
def get_wifi_interfaces():
    """Return list of wireless interface names."""
    wireless_path = "/proc/net/wireless"
    interfaces = []
    try:
        with open(wireless_path) as f:
            for line in f:
                line = line.strip()
                if ":" in line and not line.startswith("|"):
                    iface = line.split(":")[0].strip()
                    if iface:
                        interfaces.append(iface)
    except FileNotFoundError:
        pass
    # Fallback: scan /sys/class/net for wireless devices
    if not interfaces:
        for iface_path in glob.glob("/sys/class/net/*/wireless"):
            iface = iface_path.split("/")[-2]
            interfaces.append(iface)
    return interfaces

def interface_has_ip(iface):
    """Check if interface has an IP address assigned."""
    try:
        result = subprocess.run(
            ["ip", "-4", "addr", "show", iface],
            capture_output=True, text=True, timeout=5
        )
        return "inet " in result.stdout
    except Exception:
        return False

def can_reach_internet():
    """Check basic internet connectivity by connecting to a public DNS."""
    for host in [("8.8.8.8", 53), ("1.1.1.1", 53)]:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            sock.connect(host)
            sock.close()
            return True
        except OSError:
            continue
    return False

def get_public_ip(iface=None):
    """Get the IP address of the given interface, or the default route IP."""
    if iface:
        try:
            result = subprocess.run(
                ["ip", "-4", "addr", "show", iface],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith("inet "):
                    return line.split()[1].split("/")[0]
        except Exception:
            pass
    # Fallback: get default IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "0.0.0.0"

def wait_for_wifi():
    """Block until a WiFi interface is connected with an IP and internet access."""
    log("Waiting for WiFi connection...")
    start = time.time()
    while time.time() - start < WIFI_TIMEOUT:
        wifi_ifaces = get_wifi_interfaces()
        if not wifi_ifaces:
            # No wireless interfaces found — check if any network is available
            if can_reach_internet():
                log("No WiFi interfaces found, but internet is reachable (wired/other).")
                return get_public_ip()
        for iface in wifi_ifaces:
            if interface_has_ip(iface) and can_reach_internet():
                ip = get_public_ip(iface)
                log(f"WiFi connected on {iface} with IP {ip}")
                return ip
        elapsed = int(time.time() - start)
        if elapsed % 10 == 0 and elapsed > 0:
            log(f"  Still waiting for WiFi... ({elapsed}s / {WIFI_TIMEOUT}s)")
        time.sleep(WIFI_CHECK_INTERVAL)
    log_err(f"WiFi connection timed out after {WIFI_TIMEOUT}s")
    sys.exit(1)

# ---------------------------------------------------------------------------
# 2. USB Camera detection
# ---------------------------------------------------------------------------
def find_video_devices():
    """Return list of /dev/videoN devices that support video capture."""
    devices = sorted(glob.glob("/dev/video*"))
    capture_devices = []
    for dev in devices:
        try:
            result = subprocess.run(
                ["v4l2-ctl", "--device", dev, "--all"],
                capture_output=True, text=True, timeout=5
            )
            # Check that it's a capture device (not metadata or output)
            if "Video Capture" in result.stdout:
                capture_devices.append(dev)
        except FileNotFoundError:
            # v4l2-ctl not installed, accept all video devices
            capture_devices.append(dev)
        except Exception:
            continue
    return capture_devices

def detect_camera():
    """Detect and return the camera device path."""
    global CAMERA_DEVICE
    if CAMERA_DEVICE:
        if os.path.exists(CAMERA_DEVICE):
            log(f"Using configured camera: {CAMERA_DEVICE}")
            return CAMERA_DEVICE
        else:
            log_err(f"Configured camera {CAMERA_DEVICE} not found")
            sys.exit(1)

    log("Scanning for USB cameras...")
    devices = find_video_devices()
    if not devices:
        log_err("No USB camera/webcam detected. Please connect a camera and try again.")
        sys.exit(1)

    CAMERA_DEVICE = devices[0]
    log(f"Found {len(devices)} camera(s): {', '.join(devices)}")
    log(f"Using: {CAMERA_DEVICE}")
    return CAMERA_DEVICE

# ---------------------------------------------------------------------------
# 3. Version check
# ---------------------------------------------------------------------------
def check_for_updates():
    """Check GitHub for newer releases/tags compared to current version."""
    log(f"Current version: {VERSION}")
    log("Checking for updates...")

    # Try git-based check first (if we're in a repo clone)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    git_dir = os.path.join(script_dir, ".git")
    if os.path.isdir(git_dir):
        try:
            subprocess.run(
                ["git", "-C", script_dir, "fetch", "--tags", "origin"],
                capture_output=True, text=True, timeout=15
            )
            result = subprocess.run(
                ["git", "-C", script_dir, "log", "HEAD..origin/main", "--oneline"],
                capture_output=True, text=True, timeout=10
            )
            commits_behind = len([l for l in result.stdout.strip().splitlines() if l])
            if commits_behind > 0:
                log(f"UPDATE AVAILABLE: You are {commits_behind} commit(s) behind origin/main.")
                log(f"  Run: cd {script_dir} && git pull")
            else:
                log("You are up to date.")
            return
        except Exception as e:
            log(f"Git check failed ({e}), trying HTTP...")

    # Fallback: check GitHub API via curl
    try:
        result = subprocess.run(
            ["curl", "-sf", "--connect-timeout", "5", "--max-time", "10",
             f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            latest = data.get("tag_name", "").lstrip("v")
            if latest and latest != VERSION:
                log(f"UPDATE AVAILABLE: v{latest} (you have v{VERSION})")
                log(f"  See: https://github.com/{GITHUB_REPO}/releases/latest")
            else:
                log("You are up to date.")
        else:
            log("No releases found on GitHub — skipping update check.")
    except Exception as e:
        log(f"Update check failed: {e} (continuing anyway)")

# ---------------------------------------------------------------------------
# 4. Public Internet Access (Tunnel / UPnP)
# ---------------------------------------------------------------------------

# Global: holds the public URL once a tunnel is established
public_url = None
tunnel_process = None

def get_external_ip():
    """Query external IP from public services."""
    for url in ["https://ifconfig.me", "https://api.ipify.org", "https://icanhazip.com"]:
        try:
            result = subprocess.run(
                ["curl", "-sf", "--connect-timeout", "5", "--max-time", "8", url],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                ip = result.stdout.strip()
                if re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
                    return ip
        except Exception:
            continue
    return None

def try_upnp_forward(port):
    """Try to set up UPnP port forwarding on the router. Returns external IP or None."""
    try:
        # Check if upnpc (miniupnpc) is available
        subprocess.run(["upnpc", "-h"], capture_output=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        log("  UPnP: 'upnpc' not installed (install miniupnpc to enable)")
        return None

    try:
        local_ip = get_public_ip()
        # Add port mapping: external port -> local ip:port (TCP, lease 0 = permanent until removed)
        result = subprocess.run(
            ["upnpc", "-a", local_ip, str(port), str(port), "TCP", "86400"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0 or "is redirected to" in result.stdout:
            ext_ip = get_external_ip()
            if ext_ip:
                log(f"  UPnP: Port {port} forwarded successfully")
                return ext_ip
            else:
                log("  UPnP: Port forwarded but could not determine external IP")
                return None
        else:
            log(f"  UPnP: Port forwarding failed — {result.stderr.strip() or result.stdout.strip()}")
            return None
    except Exception as e:
        log(f"  UPnP: Failed ({e})")
        return None

def remove_upnp_forward(port):
    """Remove UPnP port mapping on shutdown."""
    try:
        subprocess.run(
            ["upnpc", "-d", str(port), "TCP"],
            capture_output=True, timeout=10
        )
        log("UPnP port mapping removed.")
    except Exception:
        pass

def try_ssh_tunnel(port):
    """Start a reverse SSH tunnel via localhost.run (free, no signup).
    Falls back to serveo.net. Returns public URL or None."""
    global tunnel_process

    services = [
        {
            "name": "localhost.run",
            "cmd": [
                "ssh", "-o", "StrictHostKeyChecking=no",
                "-o", "ServerAliveInterval=30",
                "-o", "ServerAliveCountMax=3",
                "-R", f"80:localhost:{port}",
                "nokey@localhost.run"
            ],
            "pattern": r'(https?://[a-z0-9]+\.lhr\.life[^\s]*|https?://[a-z0-9]+\.localhost\.run[^\s]*)',
        },
        {
            "name": "serveo.net",
            "cmd": [
                "ssh", "-o", "StrictHostKeyChecking=no",
                "-o", "ServerAliveInterval=30",
                "-o", "ServerAliveCountMax=3",
                "-R", f"80:localhost:{port}",
                "serveo.net"
            ],
            "pattern": r'(https?://[a-z0-9]+\.serveo\.net[^\s]*)',
        },
    ]

    for svc in services:
        log(f"  Tunnel: Trying {svc['name']}...")
        try:
            proc = subprocess.Popen(
                svc["cmd"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            # Wait up to 30 seconds for the public URL to appear
            deadline = time.time() + 30
            while time.time() < deadline:
                line = ""
                # Non-blocking-ish read with a short timeout
                import select
                ready, _, _ = select.select([proc.stdout], [], [], 2.0)
                if ready:
                    line = proc.stdout.readline()
                if proc.poll() is not None:
                    break
                if line:
                    match = re.search(svc["pattern"], line)
                    if match:
                        url = match.group(1)
                        tunnel_process = proc
                        log(f"  Tunnel: Connected via {svc['name']}")
                        return url

            # Didn't get a URL, kill and try next
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            log(f"  Tunnel: {svc['name']} did not return a URL")
        except FileNotFoundError:
            log("  Tunnel: 'ssh' not found — cannot create tunnel")
            return None
        except Exception as e:
            log(f"  Tunnel: {svc['name']} failed ({e})")
            continue

    return None

def setup_public_access(port):
    """Try to make the stream publicly accessible from the internet.
    Tries UPnP first, then SSH tunnel. Returns (url, method) or (None, None)."""
    global public_url

    if TUNNEL_MODE == "none":
        log("Public access disabled (TUNNEL_MODE=none)")
        return None, None

    log("Setting up public internet access...")

    # Method 1: UPnP port forwarding (direct, best performance)
    if TUNNEL_MODE in ("auto", "upnp"):
        ext_ip = try_upnp_forward(port)
        if ext_ip:
            url = f"http://{ext_ip}:{port}"
            public_url = url
            return url, "upnp"
        if TUNNEL_MODE == "upnp":
            log_err("UPnP was requested but failed.")
            return None, None

    # Method 2: SSH reverse tunnel (works behind any NAT/firewall)
    if TUNNEL_MODE in ("auto", "ssh"):
        url = try_ssh_tunnel(port)
        if url:
            public_url = url
            return url, "ssh-tunnel"
        if TUNNEL_MODE == "ssh":
            log_err("SSH tunnel was requested but failed.")
            return None, None

    log("  Could not establish public access (stream is still available on LAN)")
    return None, None

def stop_tunnel():
    """Clean up tunnel/UPnP on shutdown."""
    global tunnel_process
    if tunnel_process:
        tunnel_process.terminate()
        try:
            tunnel_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            tunnel_process.kill()
        tunnel_process = None
    remove_upnp_forward(STREAM_PORT)

# ---------------------------------------------------------------------------
# 5. MJPEG Streaming Server
# ---------------------------------------------------------------------------
class MJPEGCaptureThread(threading.Thread):
    """Captures frames from the webcam using ffmpeg and stores the latest JPEG."""

    def __init__(self, device, resolution, framerate):
        super().__init__(daemon=True)
        self.device = device
        self.resolution = resolution
        self.framerate = framerate
        self.frame = None
        self.lock = threading.Lock()
        self.event = threading.Event()
        self.running = True
        self.process = None

    def run(self):
        width, height = self.resolution.split("x")
        cmd = [
            "ffmpeg",
            "-f", "v4l2",
            "-framerate", str(self.framerate),
            "-video_size", self.resolution,
            "-i", self.device,
            "-f", "mjpeg",
            "-q:v", "5",
            "-r", str(self.framerate),
            "pipe:1"
        ]
        log(f"Starting capture: {' '.join(cmd)}")
        try:
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
        except FileNotFoundError:
            log_err("ffmpeg not found. Install it: sudo apt install ffmpeg")
            os._exit(1)

        buf = b""
        while self.running:
            chunk = self.process.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            # JPEG frames are delimited by SOI (FFD8) and EOI (FFD9)
            while True:
                start = buf.find(b'\xff\xd8')
                end = buf.find(b'\xff\xd9', start + 2) if start != -1 else -1
                if start == -1 or end == -1:
                    break
                frame = buf[start:end + 2]
                buf = buf[end + 2:]
                with self.lock:
                    self.frame = frame
                self.event.set()

        if self.process:
            self.process.terminate()

    def get_frame(self):
        self.event.wait(timeout=5)
        with self.lock:
            return self.frame

    def stop(self):
        self.running = False
        if self.process:
            self.process.terminate()


# Global capture thread reference
capture_thread = None

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Webcam Stream</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: #1a1a2e; color: #eee;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            display: flex; flex-direction: column; align-items: center;
            min-height: 100vh; padding: 20px;
        }
        h1 { margin: 20px 0 10px; font-size: 1.5em; color: #e94560; }
        .info { color: #888; margin-bottom: 20px; font-size: 0.9em; }
        img {
            max-width: 100%; border: 2px solid #333; border-radius: 8px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.5);
        }
        .status { margin-top: 15px; color: #0f3460; background: #e94560;
            padding: 5px 15px; border-radius: 20px; font-size: 0.8em; }
    </style>
</head>
<body>
    <h1>Webcam Live Stream</h1>
    <p class="info">DEVICE</p>
    <img src="/stream" alt="Webcam Stream">
    <p class="status">LIVE</p>
</body>
</html>"""


class StreamHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress default access logs to reduce noise
        pass

    def do_GET(self):
        if self.path == "/":
            content = INDEX_HTML.replace("DEVICE", capture_thread.device)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content.encode())

        elif self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type",
                             "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            try:
                while True:
                    frame = capture_thread.get_frame()
                    if frame is None:
                        time.sleep(0.1)
                        continue
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
                    time.sleep(1.0 / FRAMERATE)
            except (BrokenPipeError, ConnectionResetError):
                pass

        elif self.path == "/snapshot":
            frame = capture_thread.get_frame()
            if frame:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(frame)))
                self.end_headers()
                self.wfile.write(frame)
            else:
                self.send_response(503)
                self.end_headers()

        elif self.path == "/status":
            status = json.dumps({
                "version": VERSION,
                "camera": capture_thread.device,
                "resolution": RESOLUTION,
                "framerate": FRAMERATE,
                "streaming": capture_thread.is_alive(),
                "public_url": public_url,
            })
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(status)))
            self.end_headers()
            self.wfile.write(status.encode())

        else:
            self.send_response(404)
            self.end_headers()


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


# ---------------------------------------------------------------------------
# 6. Main
# ---------------------------------------------------------------------------
def main():
    global capture_thread

    log("=" * 50)
    log("  Webcam Streaming Server")
    log(f"  Version {VERSION}")
    log("=" * 50)

    # Step 1: Check for updates
    check_for_updates()

    # Step 2: Wait for WiFi / network
    ip = wait_for_wifi()

    # Step 3: Detect camera
    device = detect_camera()

    # Step 4: Start capture thread
    capture_thread = MJPEGCaptureThread(device, RESOLUTION, FRAMERATE)
    capture_thread.start()

    # Give ffmpeg a moment to start up
    time.sleep(2)
    if not capture_thread.is_alive():
        log_err("Camera capture failed to start.")
        sys.exit(1)

    # Step 5: Start HTTP server
    server = ThreadedHTTPServer(("0.0.0.0", STREAM_PORT), StreamHandler)

    def shutdown(signum, frame):
        log("Shutting down...")
        capture_thread.stop()
        stop_tunnel()
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Step 6: Set up public internet access
    pub_url, method = setup_public_access(STREAM_PORT)

    log("")
    log("=" * 50)
    log("  Stream is LIVE")
    log("=" * 50)
    log(f"  Local:    http://127.0.0.1:{STREAM_PORT}")
    log(f"  LAN:      http://{ip}:{STREAM_PORT}")
    if pub_url:
        log(f"  PUBLIC:   {pub_url}  ({method})")
        log(f"")
        log(f"  Share this link with anyone — no same-WiFi needed!")
    else:
        log(f"  PUBLIC:   Not available (see logs above)")
        log(f"")
        log(f"  Tip: Install 'miniupnpc' for UPnP, or 'openssh-client' for SSH tunnel")
    log(f"")
    log(f"  Snapshot: http://127.0.0.1:{STREAM_PORT}/snapshot")
    log(f"  Status:   http://127.0.0.1:{STREAM_PORT}/status")
    log("")
    log("Press Ctrl+C to stop.")

    server.serve_forever()


if __name__ == "__main__":
    main()
