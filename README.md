# GivEnergy Local Control (`v1.0.0`)

Local, cloud-free monitoring, control, and historic telemetry server for **GivEnergy Hybrid Inverters, Batteries, and EV Chargers**, designed to run 24/7 on a **Raspberry Pi** (Pi 400, Pi 4, Pi 5, etc.) with a standalone **Windows Deployment Installer**.

![GivEnergy Local Control Web Dashboard](docs/dashboard-preview.jpg)

---

## Key Features

- **100% Local Modbus TCP Control** — Direct local communication with GivEnergy Hybrid Inverters (`:8899`) and EV Chargers (`:502`) with no cloud dependency.
- **Real-Time Power Flow & Live Dashboard** — Responsive web UI (desktop & mobile) with WebSocket updates, live power flow schematic, KPI cards, and timeline chart.
- **Historic Telemetry (Storage-Safe SQLite WAL)** — Configurable RAM-batched writes for MicroSD wear-protection (`60s` flush + volatile journald logs) or NVMe/USB SSD tuning (`15s` flush + `fstrim`).
- **Battery & BMS + Grid Voltage Views** — Per-cell voltage monitoring, BMS status, and grid voltage history charts.
- **Auto-Discovery & Property Setup** — Automatically scans your local `/24` subnet for GivEnergy Inverters and EV Chargers on first setup and saves configuration to `property_config.json`.
- **Raspberry Pi System & Maintenance Tab** — Displays detected Pi hardware model, CPU temperature, RAM/storage usage, OS software updates with live progress, reboot-required detection, and Restart / Shut Down actions.
- **Optional Tailscale Remote Access** — Guided one-click Tailscale setup for encrypted off-site access from phones and laptops without router port-forwarding.
- **Standalone Windows Installer (`GivEnergy-Local-Control-Installer.exe`)** — No Python installation required on the deployment PC.

---

## Quick Start (Windows — No Python Required)

1. Download **`GivEnergy-Local-Control-Installer.exe`** from the [**v1.0.0 Release**](https://github.com/mikekwarner/GivEnergy-Local-Control/releases/tag/v1.0.0).
2. Double-click `GivEnergy-Local-Control-Installer.exe` and follow the 5-step wizard:
   - **Step 1:** Prepare a Raspberry Pi OS Lite SD/SSD card using Raspberry Pi Imager (auto-detected if installed).
   - **Step 2:** Auto-detect Raspberry Pis on your local network (also shows currently installed version & available upgrades).
   - **Step 3:** Enter SSH credentials, choose Tailscale remote access, select MicroSD vs. NVMe/SSD optimization, and optionally wipe previous data/config.
   - **Step 4:** Automated provisioning, Python environment setup, Tailscale login link, and `systemd` service deployment.
   - **Step 5:** Open the dashboard directly to **⚙ Property Setup** to verify auto-discovered Inverter and EV Charger IPs.

---

## Running or Building from Source

```powershell
# 1. Create virtual environment & install dependencies
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. Launch the Desktop App or Deployment Wizard from source
.\run.bat
# or
.\deploy_wizard.bat

# 3. Build the standalone Windows executable (dist\GivEnergy-Local-Control-Installer.exe)
.\build_wizard_exe.bat
```

---

## Network Ports Used

| Port | Protocol | Purpose |
| :--- | :--- | :--- |
| `80` | HTTP / WebSocket | GivEnergy Local Control Web Dashboard (`givenergy-web.service`) |
| `22` | SSH / SFTP | Initial provisioning and deployment from the Windows Installer |
| `8899` | TCP (Modbus) | GivEnergy Hybrid Inverter local communication |
| `502` | TCP (Modbus) | GivEnergy EV Charger local communication |

---

## Repository Structure

- `wizard.py` — PySide6 Windows Deployment Installer GUI
- `deploy_to_pi.py` & `setup_pi.py` — Automated Raspberry Pi SSH/SFTP provisioning & systemd deployment
- `givenergy_web/` — FastAPI + WebSocket backend, async Modbus poller, SQLite WAL database, and web UI (`static/index.html`)
- `givenergy_app/` & `main.py` — Local Windows PySide6 desktop monitor/control app
- `build_wizard_exe.bat` — PyInstaller build script for `GivEnergy-Local-Control-Installer.exe`
