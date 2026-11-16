"""GitHub self-updater for the GivEnergy Local Control web package on Raspberry Pi."""

from __future__ import annotations

import compileall
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GITHUB_OWNER = "mikekwarner"
GITHUB_REPO = "GivEnergy-Local-Control"
GITHUB_BRANCH = "main"
GITHUB_REPO_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
RAW_INIT_URL = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/givenergy_web/__init__.py"
COMMIT_API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/commits/{GITHUB_BRANCH}"
ARCHIVE_ZIP_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/archive/refs/heads/{GITHUB_BRANCH}.zip"

_USER_AGENT = "GivEnergy-Local-Control-Updater/1.0"
_VERSION_RE = re.compile(r'__version__\s*=\s*["\']([^"\']+)["\']')

_app_update_lock = threading.Lock()
_app_update_state: dict[str, Any] = {
    "running": False,
    "phase": "idle",
    "status_text": "Idle",
    "percent": 0,
    "ok": None,
    "current_version": "",
    "remote_version": None,
    "restarting": False,
    "output": "",
}


def _parse_version(version: str) -> tuple[int, ...]:
    cleaned = version.strip().lstrip("vV")
    parts: list[int] = []
    for token in cleaned.split("."):
        match = re.match(r"^(\d+)", token)
        if not match:
            break
        parts.append(int(match.group(1)))
    return tuple(parts)


def _is_newer_version(remote_version: str, current_version: str) -> bool:
    r_tuple = _parse_version(remote_version)
    c_tuple = _parse_version(current_version)
    if r_tuple and c_tuple:
        return r_tuple > c_tuple
    return bool(remote_version and remote_version.strip() != current_version.strip())


def _http_get_bytes(url: str, timeout: float = 12.0, accept: str | None = None) -> bytes:
    headers = {
        "User-Agent": _USER_AGENT,
        "Cache-Control": "no-cache",
    }
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def check_github_update(current_version: str) -> dict[str, Any]:
    """Check GitHub main branch for the latest package version and commit metadata."""
    result: dict[str, Any] = {
        "ok": False,
        "current_version": current_version,
        "remote_version": None,
        "update_available": False,
        "repo_url": GITHUB_REPO_URL,
        "branch": GITHUB_BRANCH,
        "latest_commit_sha": None,
        "latest_commit_date": None,
        "latest_commit_message": None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "error": None,
    }

    try:
        raw_init = _http_get_bytes(RAW_INIT_URL, timeout=10.0).decode("utf-8", errors="replace")
        match = _VERSION_RE.search(raw_init)
        if not match:
            result["error"] = "Could not parse __version__ from GitHub repository."
            return result
        remote_version = match.group(1).strip()
        result["remote_version"] = remote_version
        result["update_available"] = _is_newer_version(remote_version, current_version)
        result["ok"] = True
    except Exception as exc:
        result["error"] = f"Failed to reach GitHub: {exc}"
        return result

    # Commit metadata is best-effort (GitHub API has unauthenticated rate limits)
    try:
        commit_raw = _http_get_bytes(COMMIT_API_URL, timeout=8.0, accept="application/vnd.github+json")
        commit_data = json.loads(commit_raw.decode("utf-8", errors="replace"))
        sha = str(commit_data.get("sha") or "")
        commit_info = commit_data.get("commit") or {}
        author_info = commit_info.get("committer") or commit_info.get("author") or {}
        message = str(commit_info.get("message") or "").splitlines()[0] if commit_info.get("message") else ""
        result["latest_commit_sha"] = sha[:7] if sha else None
        result["latest_commit_date"] = author_info.get("date")
        result["latest_commit_message"] = message[:120] if message else None
    except Exception:
        pass

    return result


def _append_log(line: str, percent: int | None = None, phase: str | None = None, status_text: str | None = None) -> None:
    with _app_update_lock:
        if line:
            existing = _app_update_state["output"]
            _app_update_state["output"] = (existing + "\n" + line).lstrip("\n")[-8000:]
        if percent is not None:
            _app_update_state["percent"] = percent
        if phase is not None:
            _app_update_state["phase"] = phase
        if status_text is not None:
            _app_update_state["status_text"] = status_text


def _trigger_service_restart() -> None:
    """Restart the systemd service cleanly after a short delay so HTTP responses finish."""
    time.sleep(1.5)
    try:
        check = subprocess.run(
            ["sudo", "-n", "-l", "systemctl", "restart", "givenergy-web.service"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if check.returncode == 0:
            subprocess.Popen(
                ["sudo", "-n", "systemctl", "restart", "givenergy-web.service"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return
    except Exception:
        pass

    # Fallback: systemd unit has Restart=always, so SIGTERM lets uvicorn flush SQLite and restart cleanly
    os.kill(os.getpid(), signal.SIGTERM)


def _find_extracted_package(extract_root: Path) -> Path | None:
    direct = extract_root / "givenergy_web"
    if direct.is_dir():
        return direct
    for child in extract_root.iterdir():
        candidate = child / "givenergy_web"
        if child.is_dir() and candidate.is_dir():
            return candidate
    return None


def _run_app_update_bg(app_dir: Path, current_version: str) -> None:
    try:
        _append_log(
            f"=== Checking GitHub ({GITHUB_OWNER}/{GITHUB_REPO}@{GITHUB_BRANCH}) ===",
            percent=8,
            phase="checking",
            status_text="Checking GitHub repository for latest version...",
        )
        info = check_github_update(current_version)
        if not info.get("ok"):
            raise RuntimeError(info.get("error") or "GitHub version check failed.")

        remote_version = str(info.get("remote_version") or "")
        commit_note = f" ({info['latest_commit_sha']})" if info.get("latest_commit_sha") else ""
        with _app_update_lock:
            _app_update_state["remote_version"] = remote_version
        _append_log(
            f"Current version: v{current_version} | GitHub version: v{remote_version}{commit_note}",
            percent=15,
        )

        with tempfile.TemporaryDirectory(prefix="givenergy_update_") as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            zip_path = tmp_dir / "repo.zip"
            extract_dir = tmp_dir / "extracted"

            _append_log(
                f"Downloading {ARCHIVE_ZIP_URL} ...",
                percent=20,
                phase="downloading",
                status_text=f"Downloading v{remote_version} from GitHub...",
            )
            req = urllib.request.Request(
                ARCHIVE_ZIP_URL,
                headers={"User-Agent": _USER_AGENT, "Cache-Control": "no-cache"},
            )
            with urllib.request.urlopen(req, timeout=45.0) as resp, zip_path.open("wb") as out_f:
                total = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out_f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = 20 + int((downloaded / total) * 35)
                        with _app_update_lock:
                            _app_update_state["percent"] = min(55, pct)

            _append_log(f"Downloaded {zip_path.stat().st_size // 1024} KB. Extracting archive...", percent=60, phase="verifying", status_text="Extracting & verifying package...")
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)

            src_pkg = _find_extracted_package(extract_dir)
            if src_pkg is None:
                raise RuntimeError("Downloaded archive does not contain a givenergy_web/ directory.")

            for required in ("__init__.py", "server.py", "static/index.html"):
                if not (src_pkg / required).is_file():
                    raise RuntimeError(f"Downloaded package is missing required file: givenergy_web/{required}")

            _append_log("Compiling Python modules to verify syntax...", percent=72)
            if not compileall.compile_dir(str(src_pkg), quiet=1, force=True):
                raise RuntimeError("Syntax verification failed on downloaded Python files.")

            # Clean __pycache__ generated during compileall before swapping into place
            for pycache in src_pkg.rglob("__pycache__"):
                shutil.rmtree(pycache, ignore_errors=True)

            parent_dir = app_dir.parent
            staging_dir = parent_dir / "givenergy_web.new"
            backup_dir = parent_dir / "givenergy_web.bak"

            if staging_dir.exists():
                shutil.rmtree(staging_dir)
            if backup_dir.exists():
                shutil.rmtree(backup_dir)

            _append_log(f"Installing updated package into {app_dir} (data/ preserved)...", percent=82, phase="installing", status_text="Installing updated package...")
            shutil.copytree(src_pkg, staging_dir)

            try:
                app_dir.rename(backup_dir)
                staging_dir.rename(app_dir)
            except Exception:
                if not app_dir.exists() and backup_dir.exists():
                    backup_dir.rename(app_dir)
                raise
            finally:
                if staging_dir.exists():
                    shutil.rmtree(staging_dir, ignore_errors=True)

            if backup_dir.exists():
                shutil.rmtree(backup_dir, ignore_errors=True)

        _append_log(f"=== Package updated to v{remote_version}. Scheduling service restart... ===")
        with _app_update_lock:
            _app_update_state.update({
                "running": False,
                "phase": "restarting",
                "status_text": f"✅ Updated to v{remote_version}! Restarting web service...",
                "percent": 100,
                "ok": True,
                "restarting": True,
            })

        threading.Thread(target=_trigger_service_restart, daemon=True).start()
    except Exception as exc:
        with _app_update_lock:
            _app_update_state.update({
                "running": False,
                "phase": "error",
                "status_text": f"❌ App update failed: {exc}",
                "percent": 100,
                "ok": False,
                "restarting": False,
                "output": (_app_update_state["output"] + f"\n[ERROR] {exc}")[-8000:],
            })


def start_app_update(app_dir: Path, current_version: str) -> dict[str, Any]:
    """Start the GitHub app update in a background thread if not already running."""
    with _app_update_lock:
        if _app_update_state["running"]:
            return {"ok": True, "started": False, **_app_update_state}
        _app_update_state.update({
            "running": True,
            "phase": "checking",
            "status_text": "Starting GitHub update...",
            "percent": 5,
            "ok": None,
            "current_version": current_version,
            "remote_version": None,
            "restarting": False,
            "output": "",
        })
        t = threading.Thread(target=_run_app_update_bg, args=(app_dir, current_version), daemon=True)
        t.start()
        return {"ok": True, "started": True}


def get_app_update_status() -> dict[str, Any]:
    """Return a thread-safe snapshot of the current GitHub app update state."""
    with _app_update_lock:
        return dict(_app_update_state)
