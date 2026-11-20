"""Package dist/GivEnergy-Local-Control-Linux.tar.gz with LF line endings and executable permissions."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist"
OUT_TAR = DIST_DIR / "GivEnergy-Local-Control-Linux.tar.gz"
PKG_PREFIX = "GivEnergy-Local-Control-Linux"


def _add_bytes(tf: tarfile.TarFile, arcname: str, data: bytes, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name=f"{PKG_PREFIX}/{arcname}")
    info.size = len(data)
    info.mode = mode
    tf.addfile(info, io.BytesIO(data))


def build() -> Path:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    with tarfile.open(OUT_TAR, "w:gz") as tf:
        for fname, arc, mode, normalize_lf in [
            ("install_linux.sh", "install_linux.sh", 0o755, True),
            ("install_linux.py", "install_linux.py", 0o755, True),
            ("README_LINUX.md", "README.md", 0o644, True),
            ("requirements.txt", "requirements.txt", 0o644, True),
        ]:
            src = ROOT / fname
            raw = src.read_bytes()
            if normalize_lf:
                raw = raw.replace(b"\r\n", b"\n")
            _add_bytes(tf, arc, raw, mode=mode)

        pkg_dir = ROOT / "givenergy_web"
        for path in sorted(pkg_dir.rglob("*")):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
                continue
            rel = path.relative_to(ROOT).as_posix()
            raw = path.read_bytes()
            if path.suffix in (".py", ".html", ".json", ".js", ".css"):
                raw = raw.replace(b"\r\n", b"\n")
            _add_bytes(tf, rel, raw, mode=0o644)

    print(f"Built Linux package: {OUT_TAR} ({OUT_TAR.stat().st_size // 1024} KB)")
    return OUT_TAR


if __name__ == "__main__":
    build()
