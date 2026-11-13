"""Entry point for the GivEnergy Local Control & Telemetry Desktop Application."""

from __future__ import annotations

import argparse
import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from givenergy_app.main_window import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser(description="GivEnergy Local Control & Telemetry Desktop Application")
    parser.add_argument("--inverter-ip", default="192.168.0.10", help="IP address of GivEnergy Hybrid Gen 3 Inverter")
    parser.add_argument("--evc-ip", default="192.168.0.11", help="IP address of GivEnergy EV Charger")
    parser.add_argument("--screenshot", default="", help="Optional path to save a verification screenshot and exit")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    app.setApplicationName("GivEnergy Local Control")
    app.setFont(QFont("Segoe UI", 10))

    win = MainWindow(inverter_ip=args.inverter_ip, evc_ip=args.evc_ip)
    win.show()

    if args.screenshot:
        from PySide6.QtCore import QTimer

        captured = {"done": False}

        def _do_capture() -> None:
            if captured["done"]:
                return
            captured["done"] = True
            win.repaint()
            pix = win.grab()
            pix.save(args.screenshot)
            if args.screenshot.endswith(".png"):
                win.tabs.setCurrentIndex(1)
                win.repaint()
                pix2 = win.grab()
                pix2.save(args.screenshot.replace(".png", "_bms.png"))
                win.tabs.setCurrentIndex(2)
                win.repaint()
                pix3 = win.grab()
                pix3.save(args.screenshot.replace(".png", "_inv.png"))
            win.worker.stop()
            win.worker.wait(2500)
            app.exit(0)

        def _on_snap_check(snap) -> None:
            if snap.inverter.connected and snap.ev_charger.connected and not captured["done"]:
                QTimer.singleShot(500, _do_capture)

        win.worker.snapshot_updated.connect(_on_snap_check)
        QTimer.singleShot(12000, _do_capture)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
