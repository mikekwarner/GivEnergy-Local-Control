#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required. Install it with: sudo apt update && sudo apt install -y python3 python3-venv"
  exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
  exec sudo python3 "$SCRIPT_DIR/install_linux.py" "$@"
else
  exec python3 "$SCRIPT_DIR/install_linux.py" "$@"
fi
