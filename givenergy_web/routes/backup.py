"""Portable .zip backup export and validated restore routes."""

from __future__ import annotations

import asyncio
import io
import json
import re
import secrets
import shutil
import socket
import sqlite3
import tempfile
import time
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from givenergy_web.config import PropertyConfig, save_config
from givenergy_web.context import get_ctx
from givenergy_web.solar_forecast import load_solar_forecast_config
from givenergy_web.tariffs import load_tariff_config

router = APIRouter()


@router.get("/api/backup/export")
async def api_backup_export(
    request: Request,
    include_config: bool = Query(True),
    include_history: bool = Query(True),
):
    ctx = get_ctx(request)
    if not include_config and not include_history:
        return JSONResponse({"ok": False, "error": "Select configuration and/or historic data to export."}, status_code=400)

    def _build_backup_zip() -> tuple[bytes, str]:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            zip_buf = io.BytesIO()
            stats = ctx.db.get_db_stats() if include_history else {}
            manifest = {
                "format_version": 1,
                "app_version": ctx.app_version,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "hostname": socket.gethostname(),
                "property_name": ctx.poller.config.property_name,
                "includes": {
                    "config": bool(include_config),
                    "history": bool(include_history),
                },
                "stats": stats,
            }

            with zipfile.ZipFile(zip_buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("manifest.json", json.dumps(manifest, indent=2))
                if include_config:
                    zf.writestr("property_config.json", json.dumps(asdict(ctx.poller.config), indent=2))
                    zf.writestr("tariff_config.json", json.dumps(asdict(ctx.tariff_config), indent=2))
                    zf.writestr("solar_forecast_config.json", json.dumps(asdict(ctx.solar_forecast_config), indent=2))
                if include_history:
                    tmp_db = tmp_root / "givenergy_history.db"
                    ctx.db.backup_to_file(tmp_db)
                    zf.write(tmp_db, arcname="givenergy_history.db")

            slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", (ctx.poller.config.property_name or "home").strip()).strip("-") or "home"
            stamp = time.strftime("%Y%m%d-%H%M%S")
            return zip_buf.getvalue(), f"givenergy-backup-{slug}-{stamp}.zip"

    payload_bytes, filename = await asyncio.to_thread(_build_backup_zip)
    return Response(
        content=payload_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/api/backup/import")
async def api_backup_import(
    request: Request,
    restore_config: bool = Query(True),
    restore_history: bool = Query(True),
    preserve_api_token: bool = Query(False),
):
    ctx = get_ctx(request)
    if not restore_config and not restore_history:
        return JSONResponse({"ok": False, "error": "Select configuration and/or historic data to restore."}, status_code=400)

    body = await request.body()
    max_bytes = 512 * 1024 * 1024
    if not body:
        return JSONResponse({"ok": False, "error": "Empty backup file uploaded."}, status_code=400)
    if len(body) > max_bytes:
        return JSONResponse({"ok": False, "error": "Backup file exceeds 512 MB limit."}, status_code=413)

    try:
        zf = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile:
        return JSONResponse({"ok": False, "error": "Uploaded file is not a valid .zip backup archive."}, status_code=400)

    with zf, tempfile.TemporaryDirectory() as tmpdir:
        names = set(zf.namelist())
        manifest: dict[str, Any] = {}
        if "manifest.json" in names:
            try:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                if not isinstance(manifest, dict):
                    return JSONResponse({"ok": False, "error": "Invalid manifest.json in backup."}, status_code=400)
            except Exception as exc:
                return JSONResponse({"ok": False, "error": f"Corrupt manifest.json: {exc}"}, status_code=400)

        prop_raw: dict[str, Any] | None = None
        tariff_raw: dict[str, Any] | None = None
        solar_fc_raw: dict[str, Any] | None = None
        if restore_config:
            if (
                "property_config.json" not in names
                and "tariff_config.json" not in names
                and "solar_forecast_config.json" not in names
            ):
                return JSONResponse(
                    {"ok": False, "error": "Backup archive does not contain configuration files."},
                    status_code=400,
                )
            if "property_config.json" in names:
                try:
                    parsed = json.loads(zf.read("property_config.json").decode("utf-8"))
                    if not isinstance(parsed, dict):
                        raise ValueError("property_config.json must be a JSON object")
                    prop_raw = parsed
                except Exception as exc:
                    return JSONResponse({"ok": False, "error": f"Invalid property_config.json: {exc}"}, status_code=400)
            if "tariff_config.json" in names:
                try:
                    parsed = json.loads(zf.read("tariff_config.json").decode("utf-8"))
                    if not isinstance(parsed, dict):
                        raise ValueError("tariff_config.json must be a JSON object")
                    tariff_raw = parsed
                except Exception as exc:
                    return JSONResponse({"ok": False, "error": f"Invalid tariff_config.json: {exc}"}, status_code=400)
            if "solar_forecast_config.json" in names:
                try:
                    parsed = json.loads(zf.read("solar_forecast_config.json").decode("utf-8"))
                    if not isinstance(parsed, dict):
                        raise ValueError("solar_forecast_config.json must be a JSON object")
                    solar_fc_raw = parsed
                except Exception as exc:
                    return JSONResponse({"ok": False, "error": f"Invalid solar_forecast_config.json: {exc}"}, status_code=400)

        tmp_db_path: Path | None = None
        if restore_history:
            if "givenergy_history.db" not in names:
                return JSONResponse(
                    {"ok": False, "error": "Backup archive does not contain historic database (givenergy_history.db)."},
                    status_code=400,
                )
            info = zf.getinfo("givenergy_history.db")
            if info.file_size > max_bytes:
                return JSONResponse({"ok": False, "error": "Database inside backup exceeds 512 MB limit."}, status_code=413)
            tmp_db_path = Path(tmpdir) / "givenergy_history.db"
            with zf.open(info) as src_f, tmp_db_path.open("wb") as dst_f:
                shutil.copyfileobj(src_f, dst_f)

            def _validate_sqlite(path: Path) -> str | None:
                conn = sqlite3.connect(str(path), timeout=10.0)
                try:
                    chk = conn.execute("PRAGMA integrity_check;").fetchone()
                    if not chk or str(chk[0]).lower() != "ok":
                        return f"SQLite integrity check failed: {chk[0] if chk else 'unknown'}"
                    tables = {
                        row[0]
                        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                    }
                    required = {"daily_summary", "telemetry_1m", "telemetry_5m"}
                    missing = required - tables
                    if missing:
                        return f"Backup database missing required tables: {', '.join(sorted(missing))}"
                    return None
                except Exception as exc:
                    return f"Invalid SQLite database: {exc}"
                finally:
                    conn.close()

            err = await asyncio.to_thread(_validate_sqlite, tmp_db_path)
            if err:
                return JSONResponse({"ok": False, "error": err}, status_code=400)

        if restore_history and tmp_db_path is not None:
            await asyncio.to_thread(ctx.db.replace_from_file, tmp_db_path)
            await asyncio.to_thread(ctx.tariff_cache._init_table)
            await asyncio.to_thread(ctx.solar_forecast_store._init_schema)

        restored_cfg = False
        restored_tariff = False
        restored_solar_forecast = False
        if restore_config:
            if prop_raw is not None:
                valid_fields = set(PropertyConfig.__dataclass_fields__.keys())
                filtered = {k: v for k, v in prop_raw.items() if k in valid_fields}
                new_cfg = PropertyConfig(**filtered)
                if preserve_api_token and ctx.poller.config.api_token:
                    new_cfg.api_token = ctx.poller.config.api_token
                elif not new_cfg.api_token:
                    new_cfg.api_token = ctx.poller.config.api_token or secrets.token_urlsafe(32)
                save_config(ctx.paths.config_path, new_cfg)
                ctx.property_config = new_cfg
                await ctx.poller.apply_config(new_cfg)
                restored_cfg = True

            if tariff_raw is not None:
                ctx.paths.tariff_config_path.parent.mkdir(parents=True, exist_ok=True)
                ctx.paths.tariff_config_path.write_text(json.dumps(tariff_raw, indent=2), encoding="utf-8")
                ctx.tariff_config = load_tariff_config(ctx.paths.tariff_config_path)
                restored_tariff = True

            if solar_fc_raw is not None:
                ctx.paths.solar_forecast_config_path.parent.mkdir(parents=True, exist_ok=True)
                ctx.paths.solar_forecast_config_path.write_text(json.dumps(solar_fc_raw, indent=2), encoding="utf-8")
                ctx.solar_forecast_config = load_solar_forecast_config(ctx.paths.solar_forecast_config_path)
                restored_solar_forecast = True

    stats = ctx.db.get_db_stats()
    return JSONResponse(
        {
            "ok": True,
            "restored_config": restored_cfg,
            "restored_tariff": restored_tariff,
            "restored_solar_forecast": restored_solar_forecast,
            "restored_history": bool(restore_history),
            "stats": stats,
            "manifest": manifest,
        }
    )
