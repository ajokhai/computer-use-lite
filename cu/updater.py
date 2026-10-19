"""Updates from GitHub Releases: check, download, replace the app in place, relaunch.

Each release carries "Computer Use <version>.dmg" and, optionally, "Computer Use Voice Pack <version>.zip"
(packaging/release.sh publishes them). The repo may be private, so requests use a GitHub token when one is
available: GITHUB_TOKEN in the environment or Keychain, or the `gh` CLI's login."""

import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__
from .config import get_key

REPO = os.environ.get("CU_UPDATE_REPO", "ajokhai/computer-use-lite")
API = f"https://api.github.com/repos/{REPO}/releases/latest"


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or get_key("GITHUB_TOKEN")
    if token:
        return token
    for gh in ("/opt/homebrew/bin/gh", "/usr/local/bin/gh", "gh"):
        try:
            r = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            continue
    return ""


def _request(url: str, accept: str = "application/vnd.github+json"):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "computer-use-updater"})
    token = _token()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return urllib.request.urlopen(req, timeout=30)


def _parse(v: str) -> tuple:
    return tuple(int(p) for p in v.lstrip("v").split(".") if p.isdigit())


def check() -> dict | None:
    """The latest release if it's newer than this app, else None."""
    try:
        with _request(API) as r:
            rel = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # no releases yet
        raise
    version = rel.get("tag_name", "").lstrip("v")
    if not version or _parse(version) <= _parse(__version__):
        return None
    assets = rel.get("assets", [])
    # GitHub turns spaces in asset names into dots, so match loosely.
    dmg = next((a for a in assets if a["name"].endswith(".dmg")), None)
    pack = next((a for a in assets if "Voice" in a["name"] and a["name"].endswith(".zip")), None)
    if dmg is None:
        return None
    return {"version": version, "notes": (rel.get("body") or "").strip(), "dmg": dmg, "pack": pack}


def download(asset: dict, progress=lambda fraction: None) -> Path:
    """Download a release asset (works for private repos) to a temp file."""
    dest = Path(tempfile.mkdtemp(prefix="cu-update-")) / asset["name"]
    with _request(asset["url"], accept="application/octet-stream") as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or asset.get("size") or 0)
        done = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if total:
                progress(done / total)
    return dest


def install_and_relaunch(dmg: Path, app: Path):
    """Hand off to a small script that waits for this app to quit, swaps in the new one, and opens it.
    The caller should quit right after this returns."""
    script = Path(tempfile.mkdtemp(prefix="cu-update-")) / "install.sh"
    script.write_text(f'''#!/bin/zsh
while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.3; done
MNT=$(mktemp -d /tmp/cu-update-mnt.XXXX)
# Fixed mount point: hdiutil's output mixes in checksum lines, and the volume name may already be taken.
hdiutil attach -nobrowse -readonly -noautoopen -noverify -mountpoint "$MNT" "{dmg}" >/dev/null
if [ -d "$MNT/Computer Use.app" ]; then
  rm -rf "{app}.new"
  ditto "$MNT/Computer Use.app" "{app}.new" && rm -rf "{app}" && mv "{app}.new" "{app}"
  xattr -dr com.apple.quarantine "{app}" 2>/dev/null
fi
hdiutil detach "$MNT" -quiet || true
open "{app}"
''')
    script.chmod(0o755)
    subprocess.Popen(["/bin/zsh", str(script)], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def running_bundle() -> Path | None:
    from .integration import app_bundle
    return app_bundle()
