# GivEnergy Local Control — Local Linux Installer

Install and run **GivEnergy Local Control** directly on an **Ubuntu**, **Debian**, or **Raspberry Pi OS** machine as a `systemd` service—no Windows PC or SSH required.

---

## Prerequisites

- **Operating System:** Ubuntu (22.04 / 24.04+), Debian (11 / 12+), or Raspberry Pi OS (with `systemd` and `apt-get`).
- **Python:** Python **3.10 or newer** (`python3 --version`).
- **Privileges:** `root` or `sudo` access.
- **Network & Ports:**
  - Internet access during installation (to install `python3-venv` / Python packages).
  - Port **80** available (or choose an alternate port such as `8080` during setup if Apache/Nginx is already using port 80).
  - Local network access to your GivEnergy Hybrid Inverter (`TCP :8899`) and/or EV Charger (`TCP :502`).
- **Power Settings (Desktop Linux):** If running Ubuntu Desktop rather than Server, disable automatic Sleep/Suspend so 24/7 Modbus polling is not paused.

---

## Quick Install

1. Extract the archive and enter the folder:

```bash
tar -xzf GivEnergy-Local-Control-Linux.tar.gz
cd GivEnergy-Local-Control-Linux
```

2. Run the installer:

```bash
chmod +x install_linux.sh install_linux.py
./install_linux.sh
```

The interactive installer will:
- Check OS, `systemd`, `apt-get`, Python 3.10+, and port `80` availability.
- Ask for your **Storage Mode** (`1` = SSD / NVMe / Server `15s` DB flush + persistent `journald` logs; `2` = MicroSD `60s` RAM-batched wear-protection).
- Install `curl` and `python3-venv` if needed, copy `givenergy_web/` to `/opt/givenergy-web/`, create `/opt/givenergy-web/.venv`, and install dependencies.
- Register and start `/etc/systemd/system/givenergy-web.service` (with `/etc/sudoers.d/givenergy-web-ops` for dashboard maintenance actions).

### Non-Interactive / Custom Options

```bash
# Example: custom port 8080, SSD mode, and Tailscale Funnel
sudo python3 install_linux.py --port 8080 --storage ssd --tailscale --yes
```

Available flags:
- `--port <PORT>` — HTTP port for the web dashboard (default: `80`)
- `--storage {ssd,sd}` — `ssd` (`15s` flush + `fstrim`) or `sd` (`60s` RAM-batched flush)
- `--user <USER>` — Linux user account to run the service
- `--tailscale` — Install Tailscale and enable Tailscale Funnel
- `--wipe-data` — Wipe existing database and property config
- `--yes` — Accept defaults non-interactively

---

## Managing the Service

```bash
sudo systemctl status givenergy-web.service
sudo systemctl restart givenergy-web.service
journalctl -u givenergy-web.service -f
```

---

## Uninstall

To cleanly remove GivEnergy Local Control (with an optional `.zip` backup of your config and historic database):

```bash
sudo python3 install_linux.py --uninstall
```
