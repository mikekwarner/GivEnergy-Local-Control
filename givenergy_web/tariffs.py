"""UK Energy Tariff Database, Octopus Energy Live API Client, and Cost Calculation Engine."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Standard Great Britain Grid Supply Point (GSP) / DNO region codes (I and O are skipped)
UK_GSP_REGIONS: dict[str, str] = {
    "A": "Eastern England",
    "B": "East Midlands",
    "C": "London",
    "D": "North Wales, Merseyside & Cheshire",
    "E": "West Midlands",
    "F": "North East England",
    "G": "North West England",
    "H": "Southern England",
    "J": "South East England",
    "K": "South Wales",
    "L": "South West England",
    "M": "Yorkshire",
    "N": "Southern Scotland",
    "P": "Northern Scotland",
}

OCTOPUS_API_BASE = "https://api.octopus.energy/v1"

# Curated UK Tariff Presets (Tier 2) — time windows pre-filled, rates editable by user
UK_TARIFF_PRESETS: dict[str, list[dict[str, Any]]] = {
    "import": [
        {
            "id": "ofgem_svt",
            "supplier": "Ofgem Price Cap (Default)",
            "name": "Standard Variable Tariff (Flat)",
            "default_rate_p": 24.50,
            "standing_charge_p": 60.0,
            "windows": [],
            "notes": "Single flat unit rate based on typical Ofgem cap.",
        },
        {
            "id": "economy_7",
            "supplier": "UK Standard",
            "name": "Economy 7 (Night 00:00–07:00)",
            "default_rate_p": 28.00,
            "standing_charge_p": 60.00,
            "windows": [
                {"start": "00:00", "end": "07:00", "rate_p": 14.00, "label": "Night"},
            ],
            "notes": "Cheaper 7-hour overnight window; adjust times if your meter uses 00:30–07:30.",
        },
        {
            "id": "eon_next_drive",
            "supplier": "E.ON Next",
            "name": "Next Drive (Off-Peak 00:00–07:00)",
            "default_rate_p": 24.50,
            "standing_charge_p": 60.00,
            "windows": [
                {"start": "00:00", "end": "07:00", "rate_p": 6.70, "label": "Off-Peak EV"},
            ],
            "notes": "7-hour overnight EV/battery rate from midnight to 07:00.",
        },
        {
            "id": "edf_go_electric",
            "supplier": "EDF Energy",
            "name": "GoElectric (Off-Peak 00:00–05:00)",
            "default_rate_p": 24.50,
            "standing_charge_p": 60.00,
            "windows": [
                {"start": "00:00", "end": "05:00", "rate_p": 8.50, "label": "Off-Peak"},
            ],
            "notes": "5-hour overnight off-peak window.",
        },
        {
            "id": "bg_electric_driver",
            "supplier": "British Gas",
            "name": "Electric Driver (Off-Peak 00:00–05:00)",
            "default_rate_p": 24.50,
            "standing_charge_p": 60.00,
            "windows": [
                {"start": "00:00", "end": "05:00", "rate_p": 7.90, "label": "Off-Peak"},
            ],
            "notes": "5-hour overnight EV/battery window.",
        },
        {
            "id": "ovo_drive_anytime",
            "supplier": "OVO Energy",
            "name": "Standard + Charge Anytime (Flat Base)",
            "default_rate_p": 24.50,
            "standing_charge_p": 60.00,
            "windows": [],
            "notes": "Standard base rate; edit windows if on a fixed ToU tariff.",
        },
        {
            "id": "octopus_go_preset",
            "supplier": "Octopus Energy (Offline Preset)",
            "name": "Octopus Go (00:30–05:30)",
            "default_rate_p": 24.50,
            "standing_charge_p": 60.00,
            "windows": [
                {"start": "00:30", "end": "05:30", "rate_p": 8.50, "label": "Go Off-Peak"},
            ],
            "notes": "Offline preset; use Octopus Live API mode for exact regional rates.",
        },
        {
            "id": "octopus_igo_preset",
            "supplier": "Octopus Energy (Offline Preset)",
            "name": "Intelligent Octopus Go (23:30–05:30)",
            "default_rate_p": 24.50,
            "standing_charge_p": 60.00,
            "windows": [
                {"start": "23:30", "end": "05:30", "rate_p": 7.00, "label": "Intelligent Off-Peak"},
            ],
            "notes": "6-hour overnight window (23:30–05:30).",
        },
        {
            "id": "octopus_flux_imp_preset",
            "supplier": "Octopus Energy (Offline Preset)",
            "name": "Octopus Flux Import (Peak 16:00–19:00)",
            "default_rate_p": 24.50,
            "standing_charge_p": 60.00,
            "windows": [
                {"start": "02:00", "end": "05:00", "rate_p": 14.70, "label": "Flux Off-Peak"},
                {"start": "16:00", "end": "19:00", "rate_p": 34.30, "label": "Flux Peak"},
            ],
            "notes": "3-hour off-peak (02:00–05:00) and 3-hour peak (16:00–19:00).",
        },
    ],
    "export": [
        {
            "id": "octopus_outgoing_fixed",
            "supplier": "Octopus Energy",
            "name": "Outgoing Octopus Fixed (15.0p)",
            "default_rate_p": 15.00,
            "windows": [],
            "notes": "Flat 15p/kWh export rate.",
        },
        {
            "id": "octopus_flux_exp_preset",
            "supplier": "Octopus Energy (Offline Preset)",
            "name": "Octopus Flux Export (Peak 16:00–19:00)",
            "default_rate_p": 16.00,
            "windows": [
                {"start": "02:00", "end": "05:00", "rate_p": 5.20, "label": "Flux Off-Peak"},
                {"start": "16:00", "end": "19:00", "rate_p": 29.00, "label": "Flux Peak"},
            ],
            "notes": "Higher export payout during 16:00–19:00 peak.",
        },
        {
            "id": "eon_next_export",
            "supplier": "E.ON Next",
            "name": "Next Export Exclusive (16.5p)",
            "default_rate_p": 16.50,
            "windows": [],
            "notes": "Flat export rate for eligible E.ON Next customers.",
        },
        {
            "id": "bg_export",
            "supplier": "British Gas",
            "name": "Export & Earn Plus (15.1p)",
            "default_rate_p": 15.10,
            "windows": [],
            "notes": "Flat export rate.",
        },
        {
            "id": "edf_export",
            "supplier": "EDF Energy",
            "name": "Export Rate (12.0p)",
            "default_rate_p": 12.00,
            "windows": [],
            "notes": "Flat export rate.",
        },
        {
            "id": "seg_standard",
            "supplier": "UK Standard SEG",
            "name": "Standard SEG Export (4.1p)",
            "default_rate_p": 4.10,
            "windows": [],
            "notes": "Baseline Smart Export Guarantee rate.",
        },
    ],
}


@dataclass
class TariffWindow:
    start: str = "00:00"
    end: str = "07:00"
    rate_p: float = 7.0
    label: str = "Off-Peak"


@dataclass
class TariffSideConfig:
    mode: str = "preset"  # "octopus_api" | "preset" | "custom"
    preset_id: str = ""
    label: str = "Standard Variable Tariff (Flat)"
    description: str = ""
    octopus_product_code: str = ""
    octopus_tariff_code: str = ""
    default_rate_p: float = 24.5
    windows: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TariffConfig:
    currency_symbol: str = "£"
    sub_unit_symbol: str = "p"
    region: str = "H"
    postcode: str = ""
    standing_charge_p: float = 60.0
    import_tariff: TariffSideConfig = field(
        default_factory=lambda: TariffSideConfig(
            mode="preset",
            preset_id="ofgem_svt",
            label="Standard Variable Tariff (Flat)",
            default_rate_p=24.50,
            windows=[],
        )
    )
    export_tariff: TariffSideConfig = field(
        default_factory=lambda: TariffSideConfig(
            mode="preset",
            preset_id="octopus_outgoing_fixed",
            label="Outgoing Octopus Fixed (15.0p)",
            default_rate_p=15.00,
            windows=[],
        )
    )
    last_octopus_sync: str = ""
    price_cap_rate_p: float = 24.50
    price_cap_standing_charge_p: float = 60.0
    price_cap_auto_sync: bool = True
    price_cap_source: str = "Ofgem Price Cap (Default)"
    price_cap_updated_at: str = ""


def _side_from_dict(raw: dict[str, Any] | None, fallback: TariffSideConfig) -> TariffSideConfig:
    if not isinstance(raw, dict):
        return fallback
    windows = []
    for w in raw.get("windows", []) or []:
        if isinstance(w, dict):
            windows.append(
                {
                    "start": str(w.get("start", "00:00"))[:5],
                    "end": str(w.get("end", "07:00"))[:5],
                    "rate_p": float(w.get("rate_p", 0.0)),
                    "label": str(w.get("label", "")),
                }
            )
    return TariffSideConfig(
        mode=str(raw.get("mode", fallback.mode)),
        preset_id=str(raw.get("preset_id", fallback.preset_id)),
        label=str(raw.get("label", fallback.label)),
        description=str(raw.get("description", fallback.description)),
        octopus_product_code=str(raw.get("octopus_product_code", "")),
        octopus_tariff_code=str(raw.get("octopus_tariff_code", "")),
        default_rate_p=float(raw.get("default_rate_p", fallback.default_rate_p)),
        windows=windows,
    )


def load_tariff_config(path: Path) -> TariffConfig:
    cfg = TariffConfig()
    if not path.exists():
        return cfg
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return cfg
        region = str(raw.get("region", cfg.region)).upper()
        if region not in UK_GSP_REGIONS:
            region = "H"
        return TariffConfig(
            currency_symbol=str(raw.get("currency_symbol", "£")) or "£",
            sub_unit_symbol=str(raw.get("sub_unit_symbol", "p")) or "p",
            region=region,
            postcode=str(raw.get("postcode", "")),
            standing_charge_p=float(raw.get("standing_charge_p", cfg.standing_charge_p)),
            import_tariff=_side_from_dict(raw.get("import_tariff"), cfg.import_tariff),
            export_tariff=_side_from_dict(raw.get("export_tariff"), cfg.export_tariff),
            last_octopus_sync=str(raw.get("last_octopus_sync", "")),
            price_cap_rate_p=float(raw.get("price_cap_rate_p", cfg.price_cap_rate_p)),
            price_cap_standing_charge_p=float(raw.get("price_cap_standing_charge_p", cfg.price_cap_standing_charge_p)),
            price_cap_auto_sync=bool(raw.get("price_cap_auto_sync", cfg.price_cap_auto_sync)),
            price_cap_source=str(raw.get("price_cap_source", cfg.price_cap_source)),
            price_cap_updated_at=str(raw.get("price_cap_updated_at", "")),
        )
    except Exception:
        return cfg


def save_tariff_config(path: Path, cfg: TariffConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Octopus Energy Public REST API Client & Local SQLite Cache
# ---------------------------------------------------------------------------

_products_cache_lock = threading.Lock()
_products_cache_data: dict[str, Any] | None = None
_products_cache_ts: float = 0.0


def _http_get_json(url: str, timeout: float = 12.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "GivEnergy-Local-Control/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def lookup_gsp_by_postcode(postcode: str) -> str:
    """Resolve a UK postcode to a GSP region letter (A-P) using Octopus Energy's public endpoint."""
    cleaned = postcode.strip().upper().replace(" ", "")
    if not cleaned:
        raise ValueError("Postcode is required")
    url = f"{OCTOPUS_API_BASE}/industry/grid-supply-points/?postcode={urllib.parse.quote(cleaned)}"
    data = _http_get_json(url)
    results = data.get("results") or []
    if not results:
        raise ValueError(f"No GSP region found for postcode '{postcode}'")
    group_id = str(results[0].get("group_id", "")).strip("_").upper()
    if group_id not in UK_GSP_REGIONS:
        raise ValueError(f"Unexpected GSP region '{group_id}'")
    return group_id


def fetch_octopus_products(force_refresh: bool = False) -> dict[str, list[dict[str, Any]]]:
    """Fetch active Octopus Energy electricity products, split into import and export."""
    global _products_cache_data, _products_cache_ts
    now = time.monotonic()
    with _products_cache_lock:
        if not force_refresh and _products_cache_data is not None and (now - _products_cache_ts) < 21600:
            return _products_cache_data

    # Fetch active products (exclude business-only)
    url = f"{OCTOPUS_API_BASE}/products/?is_business=false"
    all_results: list[dict[str, Any]] = []
    next_url: str | None = url
    pages = 0
    while next_url and pages < 6:
        payload = _http_get_json(next_url)
        all_results.extend(payload.get("results") or [])
        next_url = payload.get("next")
        pages += 1

    import_products: list[dict[str, Any]] = []
    export_products: list[dict[str, Any]] = []

    for prod in all_results:
        code = str(prod.get("code", ""))
        direction = str(prod.get("direction", "")).upper()
        # Octopus marks export tariffs with direction == "EXPORT" or OUTGOING/FLUX-EXPORT codes
        is_export = direction == "EXPORT" or "OUTGOING" in code.upper() or "EXPORT" in code.upper()
        item = {
            "code": code,
            "display_name": prod.get("display_name") or prod.get("full_name") or code,
            "full_name": prod.get("full_name") or code,
            "description": prod.get("description") or "",
            "is_variable": bool(prod.get("is_variable", False)),
            "is_tracker": bool(prod.get("is_tracker", False)),
            "is_green": bool(prod.get("is_green", False)),
            "direction": "EXPORT" if is_export else "IMPORT",
        }
        if is_export:
            export_products.append(item)
        else:
            import_products.append(item)

    # Sort popular smart tariffs to the top
    def _rank(item: dict[str, Any]) -> tuple[int, str]:
        c = item["code"].upper()
        for idx, prefix in enumerate(("AGILE", "INTELLI", "GO", "FLUX", "COSY", "TRACKER", "OUTGOING", "VAR")):
            if prefix in c:
                return (idx, item["display_name"])
        return (99, item["display_name"])

    import_products.sort(key=_rank)
    export_products.sort(key=_rank)

    result = {"import_products": import_products, "export_products": export_products}
    with _products_cache_lock:
        _products_cache_data = result
        _products_cache_ts = time.monotonic()
    return result


def resolve_octopus_tariff_code(product_code: str, region: str) -> str:
    """Resolve the regional electricity tariff code (e.g. E-1R-AGILE-24-10-01-H) for a product & region."""
    region_key = f"_{region.upper()}"
    url = f"{OCTOPUS_API_BASE}/products/{urllib.parse.quote(product_code)}/"
    data = _http_get_json(url)
    single_reg = data.get("single_register_electricity_tariffs") or {}
    if region_key not in single_reg:
        raise ValueError(f"Region {region} not found for product {product_code}")
    methods = single_reg[region_key]
    if "direct_debit_monthly" in methods:
        return str(methods["direct_debit_monthly"]["code"])
    if "varying" in methods:
        return str(methods["varying"]["code"])
    # Fallback to first available payment method
    for m in methods.values():
        if isinstance(m, dict) and "code" in m:
            return str(m["code"])
    raise ValueError(f"No tariff code found for {product_code} region {region}")


class TariffRateCache:
    """SQLite cache for half-hourly / time-ranged Octopus tariff unit rates."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_table()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_table(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tariff_rates_cache (
                        tariff_code TEXT NOT NULL,
                        valid_from_epoch INTEGER NOT NULL,
                        valid_to_epoch INTEGER NOT NULL,
                        rate_p REAL NOT NULL,
                        PRIMARY KEY (tariff_code, valid_from_epoch)
                    );
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_trc_lookup ON tariff_rates_cache(tariff_code, valid_from_epoch, valid_to_epoch);"
                )
                conn.commit()
            finally:
                conn.close()

    def upsert_rates(self, tariff_code: str, slots: list[tuple[int, int, float]]) -> None:
        if not slots:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.executemany(
                    """
                    INSERT INTO tariff_rates_cache(tariff_code, valid_from_epoch, valid_to_epoch, rate_p)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(tariff_code, valid_from_epoch) DO UPDATE SET
                        valid_to_epoch = excluded.valid_to_epoch,
                        rate_p = excluded.rate_p
                    """,
                    [(tariff_code, vf, vt, rp) for vf, vt, rp in slots],
                )
                conn.commit()
            finally:
                conn.close()

    def get_slots(self, tariff_code: str, start_epoch: int, end_epoch: int) -> list[tuple[int, int, float]]:
        if not tariff_code:
            return []
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT valid_from_epoch, valid_to_epoch, rate_p
                    FROM tariff_rates_cache
                    WHERE tariff_code = ? AND valid_to_epoch > ? AND valid_from_epoch < ?
                    ORDER BY valid_from_epoch ASC
                    """,
                    (tariff_code, start_epoch, end_epoch),
                ).fetchall()
                return [(int(r["valid_from_epoch"]), int(r["valid_to_epoch"]), float(r["rate_p"])) for r in rows]
            finally:
                conn.close()


def _parse_iso_epoch(ts: str | None, default_epoch: int) -> int:
    if not ts:
        return default_epoch
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except Exception:
        return default_epoch


def sync_octopus_tariff(
    cache: TariffRateCache,
    product_code: str,
    region: str,
    days_back: int = 14,
) -> dict[str, Any]:
    """Fetch unit rates and standing charge from Octopus API and store in local SQLite cache."""
    tariff_code = resolve_octopus_tariff_code(product_code, region)
    now_utc = datetime.now(timezone.utc)
    period_from = (now_utc - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00Z")
    period_to = (now_utc + timedelta(days=2)).strftime("%Y-%m-%dT00:00:00Z")

    rates_url = (
        f"{OCTOPUS_API_BASE}/products/{urllib.parse.quote(product_code)}"
        f"/electricity-tariffs/{urllib.parse.quote(tariff_code)}/standard-unit-rates/"
        f"?period_from={period_from}&period_to={period_to}&page_size=1500"
    )
    rates_data = _http_get_json(rates_url)
    results = rates_data.get("results") or []

    slots: list[tuple[int, int, float]] = []
    latest_rate_p = 0.0
    far_future = int((now_utc + timedelta(days=3650)).timestamp())
    for r in results:
        val_p = float(r.get("value_inc_vat", r.get("value_exc_vat", 0.0)))
        vf = _parse_iso_epoch(r.get("valid_from"), 0)
        vt = _parse_iso_epoch(r.get("valid_to"), far_future)
        slots.append((vf, vt, round(val_p, 4)))

    if slots:
        # Sort chronologically and pick current/latest rate as default fallback
        slots.sort(key=lambda x: x[0])
        now_ep = int(time.time())
        active = [s[2] for s in slots if s[0] <= now_ep < s[1]]
        latest_rate_p = active[-1] if active else slots[-1][2]
        cache.upsert_rates(tariff_code, slots)

    standing_charge_p: float | None = None
    try:
        sc_url = (
            f"{OCTOPUS_API_BASE}/products/{urllib.parse.quote(product_code)}"
            f"/electricity-tariffs/{urllib.parse.quote(tariff_code)}/standing-charges/"
            f"?page_size=5"
        )
        sc_data = _http_get_json(sc_url)
        sc_results = sc_data.get("results") or []
        if sc_results:
            standing_charge_p = round(float(sc_results[0].get("value_inc_vat", 0.0)), 2)
    except Exception:
        standing_charge_p = None

    return {
        "product_code": product_code,
        "tariff_code": tariff_code,
        "region": region,
        "slots_synced": len(slots),
        "current_rate_p": round(latest_rate_p, 2),
        "standing_charge_p": standing_charge_p,
    }


def fetch_octopus_product_summary(product_code: str, region: str) -> dict[str, Any]:
    """Fetch the current unit rate and standing charge for an Octopus product & region without full history sync."""
    tariff_code = resolve_octopus_tariff_code(product_code, region)
    now_utc = datetime.now(timezone.utc)
    period_from = (now_utc - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    period_to = (now_utc + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")

    rates_url = (
        f"{OCTOPUS_API_BASE}/products/{urllib.parse.quote(product_code)}"
        f"/electricity-tariffs/{urllib.parse.quote(tariff_code)}/standard-unit-rates/"
        f"?period_from={period_from}&period_to={period_to}&page_size=100"
    )
    rates_data = _http_get_json(rates_url)
    results = rates_data.get("results") or []
    now_ep = int(time.time())
    far_future = int((now_utc + timedelta(days=3650)).timestamp())
    current_rate_p: float | None = None
    fallback_rate_p: float | None = None

    for r in results:
        val_p = float(r.get("value_inc_vat", r.get("value_exc_vat", 0.0)))
        vf = _parse_iso_epoch(r.get("valid_from"), 0)
        vt = _parse_iso_epoch(r.get("valid_to"), far_future)
        if fallback_rate_p is None:
            fallback_rate_p = val_p
        if vf <= now_ep < vt:
            current_rate_p = val_p
            break

    if current_rate_p is None:
        current_rate_p = fallback_rate_p

    standing_charge_p: float | None = None
    try:
        sc_url = (
            f"{OCTOPUS_API_BASE}/products/{urllib.parse.quote(product_code)}"
            f"/electricity-tariffs/{urllib.parse.quote(tariff_code)}/standing-charges/"
            f"?page_size=5"
        )
        sc_data = _http_get_json(sc_url)
        sc_results = sc_data.get("results") or []
        if sc_results:
            standing_charge_p = round(float(sc_results[0].get("value_inc_vat", 0.0)), 2)
    except Exception:
        standing_charge_p = None

    return {
        "product_code": product_code,
        "tariff_code": tariff_code,
        "region": region,
        "current_rate_p": round(current_rate_p, 2) if current_rate_p is not None else None,
        "standing_charge_p": standing_charge_p,
    }


# ---------------------------------------------------------------------------
# Cost Calculation Engine
# ---------------------------------------------------------------------------

def _hhmm_to_minutes(hhmm: str) -> int:
    try:
        parts = hhmm.strip().split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return 0


def _window_rate_for_minute(side: TariffSideConfig, minute_of_day: int) -> float:
    for w in side.windows:
        s = _hhmm_to_minutes(w.get("start", "00:00"))
        e = _hhmm_to_minutes(w.get("end", "00:00"))
        rate = float(w.get("rate_p", side.default_rate_p))
        if s == e:
            continue
        if s < e:
            if s <= minute_of_day < e:
                return rate
        else:
            # Overnight wrap-around (e.g., 23:30 to 05:30)
            if minute_of_day >= s or minute_of_day < e:
                return rate
    return float(side.default_rate_p)


def _slot_rate_for_epoch(
    side: TariffSideConfig,
    epoch: int,
    slots: list[tuple[int, int, float]],
    slot_idx_hint: int = 0,
) -> tuple[float, int]:
    """Return (rate_p, next_idx_hint) for an epoch timestamp."""
    if side.mode == "octopus_api" and slots:
        n = len(slots)
        i = slot_idx_hint
        while i < n and slots[i][1] <= epoch:
            i += 1
        if i < n and slots[i][0] <= epoch < slots[i][1]:
            return slots[i][2], i
        # Binary-style fallback if out of order
        for j, (vf, vt, rp) in enumerate(slots):
            if vf <= epoch < vt:
                return rp, j
    dt = datetime.fromtimestamp(epoch)
    return _window_rate_for_minute(side, dt.hour * 60 + dt.minute), slot_idx_hint


def get_current_rates(cfg: TariffConfig, cache: TariffRateCache) -> dict[str, Any]:
    """Return the active import & export rates (p/kWh) right now."""
    now_ep = int(time.time())
    imp_slots = (
        cache.get_slots(cfg.import_tariff.octopus_tariff_code, now_ep - 3600, now_ep + 3600)
        if cfg.import_tariff.mode == "octopus_api"
        else []
    )
    exp_slots = (
        cache.get_slots(cfg.export_tariff.octopus_tariff_code, now_ep - 3600, now_ep + 3600)
        if cfg.export_tariff.mode == "octopus_api"
        else []
    )
    imp_rate, _ = _slot_rate_for_epoch(cfg.import_tariff, now_ep, imp_slots)
    exp_rate, _ = _slot_rate_for_epoch(cfg.export_tariff, now_ep, exp_slots)
    return {
        "import_rate_p": round(imp_rate, 2),
        "export_rate_p": round(exp_rate, 2),
        "standing_charge_p": round(cfg.standing_charge_p, 2),
        "import_label": cfg.import_tariff.label,
        "import_description": cfg.import_tariff.description,
        "export_label": cfg.export_tariff.label,
        "export_description": cfg.export_tariff.description,
        "currency_symbol": cfg.currency_symbol,
        "sub_unit_symbol": cfg.sub_unit_symbol,
    }


def _avg_window_rate(side: TariffSideConfig) -> float:
    if not side.windows:
        return float(side.default_rate_p)
    total = sum(_window_rate_for_minute(side, m) for m in range(0, 1440, 30))
    return total / 48.0


def calculate_intraday_costs(
    db: Any,
    cfg: TariffConfig,
    cache: TariffRateCache,
    date_str: str | None = None,
) -> dict[str, Any]:
    """Compute half-hourly / hourly cost breakdown for a given date (defaults to today)."""
    target_date = date_str or datetime.now().strftime("%Y-%m-%d")
    series = db.get_intraday_series(target_date)

    dt_start = datetime.strptime(target_date, "%Y-%m-%d")
    start_ep = int(dt_start.timestamp())
    end_ep = int((dt_start + timedelta(days=1)).timestamp())

    imp_slots = (
        cache.get_slots(cfg.import_tariff.octopus_tariff_code, start_ep, end_ep)
        if cfg.import_tariff.mode == "octopus_api"
        else []
    )
    exp_slots = (
        cache.get_slots(cfg.export_tariff.octopus_tariff_code, start_ep, end_ep)
        if cfg.export_tariff.mode == "octopus_api"
        else []
    )

    # 48 half-hour buckets for clean charting
    buckets: list[dict[str, Any]] = []
    for slot_i in range(48):
        hh = (slot_i * 30) // 60
        mm = (slot_i * 30) % 60
        slot_ep = start_ep + slot_i * 1800
        imp_r, _ = _slot_rate_for_epoch(cfg.import_tariff, slot_ep, imp_slots)
        exp_r, _ = _slot_rate_for_epoch(cfg.export_tariff, slot_ep, exp_slots)
        buckets.append(
            {
                "time": f"{hh:02d}:{mm:02d}",
                "import_kwh": 0.0,
                "export_kwh": 0.0,
                "import_cost_p": 0.0,
                "export_income_p": 0.0,
                "import_rate_p": round(imp_r, 2),
                "export_rate_p": round(exp_r, 2),
            }
        )

    # Estimate sample duration (1m = 60s, or 5s live ring fallback)
    if len(series) >= 2:
        diffs = [
            max(1, int(series[i]["epoch"]) - int(series[i - 1]["epoch"]))
            for i in range(1, min(len(series), 20))
        ]
        dt_hours = (sum(diffs) / len(diffs)) / 3600.0
        dt_hours = min(dt_hours, 1.0 / 12.0)  # cap at 5m
    else:
        dt_hours = 1.0 / 60.0

    imp_idx = 0
    exp_idx = 0
    total_imp_p = 0.0
    total_exp_p = 0.0
    total_imp_kwh = 0.0
    total_exp_kwh = 0.0

    for row in series:
        ep = int(row.get("epoch", 0))
        if ep < start_ep or ep >= end_ep:
            continue
        slot_i = min(47, max(0, (ep - start_ep) // 1800))
        grid_w = float(row.get("grid_w", row.get("grid_w_avg", 0)) or 0)
        # GivEnergy convention: p_grid_out > 0 is export, < 0 is import
        imp_w = max(0.0, -grid_w)
        exp_w = max(0.0, grid_w)
        imp_kwh = (imp_w * dt_hours) / 1000.0
        exp_kwh = (exp_w * dt_hours) / 1000.0

        imp_r, imp_idx = _slot_rate_for_epoch(cfg.import_tariff, ep, imp_slots, imp_idx)
        exp_r, exp_idx = _slot_rate_for_epoch(cfg.export_tariff, ep, exp_slots, exp_idx)

        imp_cost_p = imp_kwh * imp_r
        exp_inc_p = exp_kwh * exp_r

        b = buckets[slot_i]
        b["import_kwh"] += imp_kwh
        b["export_kwh"] += exp_kwh
        b["import_cost_p"] += imp_cost_p
        b["export_income_p"] += exp_inc_p

        total_imp_kwh += imp_kwh
        total_exp_kwh += exp_kwh
        total_imp_p += imp_cost_p
        total_exp_p += exp_inc_p

    for b in buckets:
        b["import_kwh"] = round(b["import_kwh"], 3)
        b["export_kwh"] = round(b["export_kwh"], 3)
        b["import_cost_p"] = round(b["import_cost_p"], 2)
        b["export_income_p"] = round(b["export_income_p"], 2)

    return {
        "date": target_date,
        "buckets": buckets,
        "totals": {
            "import_kwh": round(total_imp_kwh, 2),
            "export_kwh": round(total_exp_kwh, 2),
            "import_cost_gbp": round(total_imp_p / 100.0, 2),
            "export_income_gbp": round(total_exp_p / 100.0, 2),
            "standing_charge_gbp": round(cfg.standing_charge_p / 100.0, 2),
            "net_cost_gbp": round((total_imp_p + cfg.standing_charge_p - total_exp_p) / 100.0, 2),
        },
    }


def calculate_daily_costs(
    db: Any,
    cfg: TariffConfig,
    cache: TariffRateCache,
    days: int = 30,
) -> dict[str, Any]:
    """Compute daily import cost, export income, standing charge, net cost, and savings."""
    query_limit = 10000 if days <= 0 else days
    summaries = db.get_daily_summaries(limit=query_limit)
    if not summaries:
        return {
            "days": [],
            "totals": {
                "days_count": 0,
                "import_kwh": 0.0,
                "export_kwh": 0.0,
                "self_consumption_kwh": 0.0,
                "import_cost_gbp": 0.0,
                "export_income_gbp": 0.0,
                "standing_charge_gbp": 0.0,
                "net_cost_gbp": 0.0,
                "avg_daily_net_gbp": 0.0,
                "savings_gbp": 0.0,
                "price_cap_cost_gbp": 0.0,
                "savings_vs_price_cap_gbp": 0.0,
            },
        }

    oldest_date = summaries[-1]["date_str"]
    newest_date = summaries[0]["date_str"]
    start_ep = int(datetime.strptime(oldest_date, "%Y-%m-%d").timestamp())
    end_ep = int((datetime.strptime(newest_date, "%Y-%m-%d") + timedelta(days=1)).timestamp())

    imp_slots = (
        cache.get_slots(cfg.import_tariff.octopus_tariff_code, start_ep, end_ep)
        if cfg.import_tariff.mode == "octopus_api"
        else []
    )
    exp_slots = (
        cache.get_slots(cfg.export_tariff.octopus_tariff_code, start_ep, end_ep)
        if cfg.export_tariff.mode == "octopus_api"
        else []
    )

    # Pull 5m telemetry across recent range (capped at 90d for fast all-time aggregation)
    series_days = 90 if days <= 0 else min(max(days + 1, 2), 90)
    series = db.get_range_series(days=series_days)
    by_date: dict[str, list[dict[str, Any]]] = {}
    for row in series:
        d = row.get("date_str")
        if d:
            by_date.setdefault(d, []).append(row)

    avg_imp_fallback = _avg_window_rate(cfg.import_tariff)
    avg_exp_fallback = _avg_window_rate(cfg.export_tariff)

    daily_rows: list[dict[str, Any]] = []
    tot_imp_kwh = 0.0
    tot_exp_kwh = 0.0
    tot_self_kwh = 0.0
    tot_cons_kwh = 0.0
    tot_imp_gbp = 0.0
    tot_exp_gbp = 0.0
    tot_sc_gbp = 0.0
    tot_sav_gbp = 0.0

    for s in summaries:
        d_str = s["date_str"]
        ledger_imp_kwh = float(s.get("grid_import_kwh", 0.0) or 0.0)
        ledger_exp_kwh = float(s.get("grid_export_kwh", 0.0) or 0.0)
        self_kwh = float(s.get("self_consumption_kwh", 0.0) or 0.0)
        cons_kwh = float(s.get("consumption_kwh", 0.0) or 0.0)
        if cons_kwh <= 0.0:
            cons_kwh = ledger_imp_kwh + self_kwh

        rows = by_date.get(d_str, [])
        if rows:
            # Determine step duration (1m if days <= 2, else 5m)
            if len(rows) >= 2:
                step_s = max(30, int(rows[1]["epoch"]) - int(rows[0]["epoch"]))
                dt_h = min(step_s, 300) / 3600.0
            else:
                dt_h = 5.0 / 60.0

            raw_imp_kwh = 0.0
            raw_exp_kwh = 0.0
            raw_imp_p = 0.0
            raw_exp_p = 0.0
            imp_idx = 0
            exp_idx = 0

            for r in rows:
                ep = int(r["epoch"])
                gw = float(r.get("grid_w", r.get("grid_w_avg", 0)) or 0)
                ik = (max(0.0, -gw) * dt_h) / 1000.0
                ek = (max(0.0, gw) * dt_h) / 1000.0
                ir, imp_idx = _slot_rate_for_epoch(cfg.import_tariff, ep, imp_slots, imp_idx)
                er, exp_idx = _slot_rate_for_epoch(cfg.export_tariff, ep, exp_slots, exp_idx)
                raw_imp_kwh += ik
                raw_exp_kwh += ek
                raw_imp_p += ik * ir
                raw_exp_p += ek * er

            eff_imp_rate = (raw_imp_p / raw_imp_kwh) if raw_imp_kwh > 0.02 else avg_imp_fallback
            eff_exp_rate = (raw_exp_p / raw_exp_kwh) if raw_exp_kwh > 0.02 else avg_exp_fallback
        else:
            eff_imp_rate = avg_imp_fallback
            eff_exp_rate = avg_exp_fallback

        imp_cost_gbp = (ledger_imp_kwh * eff_imp_rate) / 100.0
        exp_inc_gbp = (ledger_exp_kwh * eff_exp_rate) / 100.0
        sc_gbp = cfg.standing_charge_p / 100.0
        net_gbp = imp_cost_gbp + sc_gbp - exp_inc_gbp
        savings_gbp = (self_kwh * eff_imp_rate) / 100.0

        daily_rows.append(
            {
                "date_str": d_str,
                "import_kwh": round(ledger_imp_kwh, 2),
                "export_kwh": round(ledger_exp_kwh, 2),
                "self_consumption_kwh": round(self_kwh, 2),
                "avg_import_rate_p": round(eff_imp_rate, 2),
                "avg_export_rate_p": round(eff_exp_rate, 2),
                "import_cost_gbp": round(imp_cost_gbp, 2),
                "export_income_gbp": round(exp_inc_gbp, 2),
                "standing_charge_gbp": round(sc_gbp, 2),
                "net_cost_gbp": round(net_gbp, 2),
                "savings_gbp": round(savings_gbp, 2),
            }
        )

        tot_imp_kwh += ledger_imp_kwh
        tot_exp_kwh += ledger_exp_kwh
        tot_self_kwh += self_kwh
        tot_cons_kwh += cons_kwh
        tot_imp_gbp += imp_cost_gbp
        tot_exp_gbp += exp_inc_gbp
        tot_sc_gbp += sc_gbp
        tot_sav_gbp += savings_gbp

    n_days = len(daily_rows)
    net_tot = tot_imp_gbp + tot_sc_gbp - tot_exp_gbp
    cap_rate_p = float(cfg.price_cap_rate_p if cfg.price_cap_rate_p > 0 else 24.50)
    cap_sc_p = float(cfg.price_cap_standing_charge_p if cfg.price_cap_standing_charge_p >= 0 else 60.0)
    price_cap_bill = ((tot_cons_kwh * cap_rate_p) + (n_days * cap_sc_p)) / 100.0
    savings_vs_cap = price_cap_bill - net_tot
    return {
        "days": daily_rows,
        "totals": {
            "days_count": n_days,
            "import_kwh": round(tot_imp_kwh, 2),
            "export_kwh": round(tot_exp_kwh, 2),
            "self_consumption_kwh": round(tot_self_kwh, 2),
            "consumption_kwh": round(tot_cons_kwh, 2),
            "import_cost_gbp": round(tot_imp_gbp, 2),
            "export_income_gbp": round(tot_exp_gbp, 2),
            "standing_charge_gbp": round(tot_sc_gbp, 2),
            "net_cost_gbp": round(net_tot, 2),
            "avg_daily_net_gbp": round(net_tot / n_days, 2) if n_days else 0.0,
            "savings_gbp": round(tot_sav_gbp, 2),
            "price_cap_cost_gbp": round(price_cap_bill, 2),
            "price_cap_rate_p": round(cap_rate_p, 2),
            "price_cap_sc_p": round(cap_sc_p, 2),
            "price_cap_source": cfg.price_cap_source,
            "price_cap_updated_at": cfg.price_cap_updated_at,
            "savings_vs_price_cap_gbp": round(savings_vs_cap, 2),
        },
    }


def sync_price_cap(cfg: TariffConfig) -> dict[str, Any]:
    """Fetch live UK Standard Variable (Ofgem Price Cap tracker) rates for cfg.region."""
    product_code = "VAR-22-11-01"
    try:
        prods = fetch_octopus_products().get("import_products", [])
        flex = next(
            (
                p for p in prods
                if str(p.get("display_name", "")).strip().lower() == "flexible octopus"
                or (str(p.get("code", "")).startswith("VAR-") and "prepay" not in str(p.get("code", "")).lower())
            ),
            None,
        )
        if flex and flex.get("code"):
            product_code = str(flex["code"])
    except Exception:
        pass

    summary = fetch_octopus_product_summary(product_code, cfg.region)
    rate_p = summary.get("current_rate_p")
    sc_p = summary.get("standing_charge_p")
    if rate_p is None or float(rate_p) <= 0:
        raise RuntimeError(f"No live price cap unit rate returned for {product_code} ({cfg.region})")

    cfg.price_cap_rate_p = round(float(rate_p), 2)
    if sc_p is not None and float(sc_p) >= 0:
        cfg.price_cap_standing_charge_p = round(float(sc_p), 2)
    cfg.price_cap_source = f"Flexible Octopus SVT ({product_code}, Region {cfg.region})"
    cfg.price_cap_updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return {
        "price_cap_rate_p": cfg.price_cap_rate_p,
        "price_cap_standing_charge_p": cfg.price_cap_standing_charge_p,
        "price_cap_source": cfg.price_cap_source,
        "price_cap_updated_at": cfg.price_cap_updated_at,
    }


def aggregate_cost_periods(
    daily_rows: list[dict[str, Any]],
    period: str = "days",
) -> list[dict[str, Any]]:
    """Aggregate daily cost rows into days, weeks, months, years, or all-time buckets (newest first)."""
    if not daily_rows:
        return []

    if period == "days":
        return [
            {
                "period_key": r["date_str"],
                "label": r["date_str"],
                "days_count": 1,
                "import_kwh": r["import_kwh"],
                "export_kwh": r["export_kwh"],
                "self_consumption_kwh": r["self_consumption_kwh"],
                "import_cost_gbp": r["import_cost_gbp"],
                "export_income_gbp": r["export_income_gbp"],
                "standing_charge_gbp": r["standing_charge_gbp"],
                "net_cost_gbp": r["net_cost_gbp"],
                "avg_daily_net_gbp": r["net_cost_gbp"],
                "savings_gbp": r["savings_gbp"],
            }
            for r in daily_rows
        ]

    buckets: dict[str, dict[str, Any]] = {}
    for r in daily_rows:
        d_str = r["date_str"]
        dt = datetime.strptime(d_str, "%Y-%m-%d")
        if period == "weeks":
            iso = dt.isocalendar()
            key = f"{iso.year}-W{iso.week:02d}"
            mon = dt - timedelta(days=dt.weekday())
            sun = mon + timedelta(days=6)
            label = f"{key} ({mon.strftime('%d %b')}–{sun.strftime('%d %b')})"
        elif period == "months":
            key = dt.strftime("%Y-%m")
            label = dt.strftime("%b %Y")
        elif period == "years":
            key = dt.strftime("%Y")
            label = key
        else:  # "all"
            key = "all"
            label = f"All Time ({daily_rows[-1]['date_str']} → {daily_rows[0]['date_str']})"

        b = buckets.get(key)
        if b is None:
            b = {
                "period_key": key,
                "label": label,
                "days_count": 0,
                "import_kwh": 0.0,
                "export_kwh": 0.0,
                "self_consumption_kwh": 0.0,
                "import_cost_gbp": 0.0,
                "export_income_gbp": 0.0,
                "standing_charge_gbp": 0.0,
                "net_cost_gbp": 0.0,
                "savings_gbp": 0.0,
            }
            buckets[key] = b

        b["days_count"] += 1
        b["import_kwh"] += float(r.get("import_kwh", 0.0) or 0.0)
        b["export_kwh"] += float(r.get("export_kwh", 0.0) or 0.0)
        b["self_consumption_kwh"] += float(r.get("self_consumption_kwh", 0.0) or 0.0)
        b["import_cost_gbp"] += float(r.get("import_cost_gbp", 0.0) or 0.0)
        b["export_income_gbp"] += float(r.get("export_income_gbp", 0.0) or 0.0)
        b["standing_charge_gbp"] += float(r.get("standing_charge_gbp", 0.0) or 0.0)
        b["net_cost_gbp"] += float(r.get("net_cost_gbp", 0.0) or 0.0)
        b["savings_gbp"] += float(r.get("savings_gbp", 0.0) or 0.0)

    out: list[dict[str, Any]] = []
    for key in sorted(buckets.keys(), reverse=True):
        b = buckets[key]
        dc = max(1, b["days_count"])
        out.append(
            {
                "period_key": b["period_key"],
                "label": b["label"],
                "days_count": b["days_count"],
                "import_kwh": round(b["import_kwh"], 2),
                "export_kwh": round(b["export_kwh"], 2),
                "self_consumption_kwh": round(b["self_consumption_kwh"], 2),
                "import_cost_gbp": round(b["import_cost_gbp"], 2),
                "export_income_gbp": round(b["export_income_gbp"], 2),
                "standing_charge_gbp": round(b["standing_charge_gbp"], 2),
                "net_cost_gbp": round(b["net_cost_gbp"], 2),
                "avg_daily_net_gbp": round(b["net_cost_gbp"] / dc, 2),
                "savings_gbp": round(b["savings_gbp"], 2),
            }
        )
    return out
