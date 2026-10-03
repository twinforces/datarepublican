#!/usr/bin/env python3
"""FMCSA SMS crash / inspection / BASIC ingest (current monthly window).

Company Census is already in `dot_carriers`. This step loads the Safety
Measurement System public files from data.transportation.gov and does **not**
touch Addresses. Join to physical colocators in generate_safety_gallery.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from database_operations import DatabaseOperations
from download_utils import (
    FMCSA_SMS_BASICS_URL,
    FMCSA_SMS_CRASH_URL,
    FMCSA_SMS_INSPECTION_URL,
    ensure_download,
)
from logging_utils import log_info, log_warning

INGEST_VERSION = 1

DATASETS = (
    {
        "key": "crash",
        "filename": "sms_input_crash.csv",
        "url": FMCSA_SMS_CRASH_URL,
        "table": "fmcsa_sms_crashes",
        "keep": (
            "dot_number",
            "report_number",
            "report_seq_no",
            "report_date",
            "report_state",
            "fatalities",
            "injuries",
            "tow_away",
            "hazmat_released",
            "severity_weight",
            "time_weight",
        ),
    },
    {
        "key": "inspection",
        "filename": "sms_input_inspection.csv",
        "url": FMCSA_SMS_INSPECTION_URL,
        "table": "fmcsa_sms_inspections",
        "keep": (
            "dot_number",
            "unique_id",
            "report_number",
            "report_state",
            "insp_date",
            "insp_level_id",
            "time_weight",
            "driver_oos_total",
            "vehicle_oos_total",
            "oos_total",
            "hazmat_oos_total",
        ),
    },
    {
        "key": "basics",
        "filename": "sms_ab_passproperty.csv",
        "url": FMCSA_SMS_BASICS_URL,
        "table": "fmcsa_sms_basics",
        "keep": (
            "dot_number",
            "insp_total",
            "driver_insp_total",
            "driver_oos_insp_total",
            "vehicle_insp_total",
            "vehicle_oos_insp_total",
            "unsafe_driv_insp_w_viol",
            "unsafe_driv_measure",
            "unsafe_driv_pct",
            "unsafe_driv_ac",
            "unsafe_driv_basic_alert",
            "hos_driv_insp_w_viol",
            "hos_driv_measure",
            "hos_driv_pct",
            "hos_driv_ac",
            "hos_driv_basic_alert",
            "driv_fit_insp_w_viol",
            "driv_fit_measure",
            "driv_fit_pct",
            "driv_fit_ac",
            "driv_fit_basic_alert",
            "contr_subst_insp_w_viol",
            "contr_subst_measure",
            "contr_subst_pct",
            "contr_subst_ac",
            "contr_subst_basic_alert",
            "veh_maint_insp_w_viol",
            "veh_maint_measure",
            "veh_maint_pct",
            "veh_maint_ac",
            "veh_maint_basic_alert",
            "unsafe_load_insp_w_viol",
            "unsafe_load_measure",
            "unsafe_load_pct",
            "unsafe_load_ac",
            "unsafe_load_basic_alert",
            "crash_ind_ac",
            "crash_ind_basic_alert",
        ),
    },
)


def normalize_dot_sql(expr: str) -> str:
    """USDOT numbers as canonical decimal strings (strip leading zeros)."""
    return f"CAST(TRY_CAST(TRIM(CAST({expr} AS VARCHAR)) AS BIGINT) AS VARCHAR)"


def census_phone_norm_sql(expr: str) -> str:
    """10-digit US census phone, or NULL. Strips a leading 1 from 11-digit values."""
    digits = f"regexp_replace(COALESCE(CAST({expr} AS VARCHAR), ''), '[^0-9]', '', 'g')"
    return (
        f"CASE "
        f"WHEN length({digits}) = 11 AND {digits} LIKE '1%' THEN substr({digits}, 2) "
        f"WHEN length({digits}) = 10 THEN {digits} "
        f"ELSE NULL END"
    )


def sms_flag_true_sql(expr: str) -> str:
    """SMS Y/N columns and Socrata true/false booleans both count as set."""
    return (
        f"UPPER(TRIM(COALESCE(CAST({expr} AS VARCHAR), ''))) "
        f"IN ('Y', 'TRUE', 'T', '1')"
    )


TERMS = {
    "extra_quiet": (
        "Paper USDOTs (power units ≤ 5) on the same census phone as a small "
        "harm DOT (power units ≤ 50), with a clean SMS window. The new vinyl. "
        "Counted on mill phones like Signal Hill; ignored next to a 5,000-truck "
        "fleet fatality."
    ),
    "worst_fat": (
        "Highest crash-fatality count on any single USDOT at this colocator."
    ),
    "score": (
        "Sort: Mill×harm = Apple/PU × physical USDOT records. A paperwork mill "
        "is legal; this scores mills that did not vet a bad driver."
    ),
    "mill_score": (
        "Apple/PU of the worst USDOT at this yard, times how many physical "
        "USDOT records sit here. A 1-truck death at a 500-name mill is 500; "
        "FedEx with three names stays tiny. The mill is legal; the product is "
        "the vet-your-drivers stain."
    ),
    "fat_dots": (
        "Distinct USDOTs with crash fatalities and power units ≤ 50 that "
        "share a census phone with another physical USDOT. Vinyl already "
        "in service."
    ),
    "harm_phones": (
        "Census phones used by two or more physical USDOTs here, where at "
        "least one of those numbers has SMS harm (fatality, crash, OOS, or BASIC)."
    ),
    "phone_fatalities": (
        "Crash fatalities on USDOTs that share a census phone with another "
        "physical USDOT at this yard."
    ),
    "oos": (
        "Inspections in the SMS window that recorded at least one "
        "out-of-service violation. Inside Apple/PU and Yard/PU this is "
        "w_oos×OOS, universe-wide sum(fatalities)/sum(OOS) (~0.0069)."
    ),
    "basic": (
        "FMCSA Behavior Analysis and Safety Improvement Categories — seven "
        "on-road scores that are the road to a crash: unsafe driving, hours "
        "of service, driver fitness, drugs/alcohol, vehicle maintenance, "
        "cargo/hazmat, crash indicator. Each category has its own universe "
        "weight sum(fatalities)/count of that flag (rare flags score higher). "
        "Lit flags add. Yard count is USDOTs with at least one flag."
    ),
    "basic_flag": (
        "Which of the seven BASIC categories are over the intervention "
        "threshold on this USDOT. Hover a short label for that flag's "
        "expected-death weight."
    ),
    "basic_w": (
        "Sum of per-flag universe weights on this USDOT. Each lit BASIC "
        "category scores sum(fatalities)/count of that flag."
    ),
    "basic_unsafe": "Unsafe driving BASIC over the intervention threshold.",
    "basic_hos": "Hours-of-service BASIC over the intervention threshold.",
    "basic_fit": "Driver fitness BASIC over the intervention threshold.",
    "basic_drugs": "Controlled substances / alcohol BASIC over the intervention threshold.",
    "basic_maint": "Vehicle maintenance BASIC over the intervention threshold.",
    "basic_cargo": "Cargo / hazmat BASIC over the intervention threshold.",
    "basic_crash": "Crash indicator BASIC over the intervention threshold.",
    "phy": (
        "Distinct physical-address USDOT numbers at this colocator. Mail and "
        "virtual-office addresses are excluded."
    ),
    "fatalities": (
        "Crash fatalities in the current FMCSA SMS window, summed across "
        "USDOTs at this colocator."
    ),
    "crashes": (
        "SMS crash records at this colocator in the current window "
        "(tow-away, injury, or fatality crashes FMCSA attributes to these "
        "USDOTs)."
    ),
    "crash_dots": (
        "Distinct USDOTs with at least one SMS crash on a shared mill phone "
        "at this yard."
    ),
    "sms": (
        "FMCSA Safety Measurement System current public window (crashes, "
        "inspections, BASIC)."
    ),
    "colocator": (
        "Physical yard key (LL:lat:lon) grouping carriers that share the "
        "same operational footprint."
    ),
    "dot": (
        "USDOT number. Search Carriers (SC), SC map, MOTUS account, and "
        "SAFER snapshot."
    ),
    "rank_score": (
        "Row rank. Color tracks Mill×harm (Apple/PU × physical USDOT records) "
        "among listed farms. Sort is Mill×harm, then Fat DOTs, then crashes."
    ),
    "rank_rate": (
        "Row rank on this Harm/PU list. Color tracks Yard/PU. "
        "Yard/PU and Apple/PU are the same number on a one-truck / one-USDOT "
        "yard; they split when a small apple sits next to a larger fleet."
    ),
    "max_pu": (
        "Largest MCS-150 power-unit count on the shared census phone. "
        "Informational; a large quiet DOT does not by itself drop the farm."
    ),
    "rank_fat": (
        "Percentile rank of colocator fatalities among listed yards."
    ),
    "power_units": (
        "Power units on the MCS-150 census (trucks/tractors the carrier "
        "reports). Active PU is status A only."
    ),
    "status": "FMCSA census status: A = active authority, I = inactive.",
    "phone": (
        "Phone on the census record. A number reused across many legal names "
        "is a paper-stack signal."
    ),
    "spoil_rate": (
        "Yard / PU (scale outside the max): max(fatalities, "
        "sum of per-flag weights, w_crash×crashes, w_oos×OOS) / yard power "
        "units. Each BASIC category is sum(fatalities)/count of that flag. "
        "Zero listed power units count as 1."
    ),
    "spoil_in": (
        "Apple / PU (scale inside the max): worst USDOT at this yard of "
        "max(that number's fatalities, sum of its per-flag weights, "
        "w_crash×crashes, w_oos×OOS) / that number's power units. "
        "The seven BASIC categories are the road to a crash and are not "
        "equal — a rare flag (unsafe driving) scores more than a common "
        "one (driver fitness)."
    ),
}

# (key, short label, title, ac column, alert column)
BASIC_CATEGORIES = (
    ("unsafe", "U", "Unsafe driving", "unsafe_driv_ac", "unsafe_driv_basic_alert"),
    ("hos", "HOS", "Hours of service", "hos_driv_ac", "hos_driv_basic_alert"),
    ("fit", "Fit", "Driver fitness", "driv_fit_ac", "driv_fit_basic_alert"),
    ("drugs", "Drug", "Drugs / alcohol", "contr_subst_ac", "contr_subst_basic_alert"),
    ("maint", "Maint", "Vehicle maintenance", "veh_maint_ac", "veh_maint_basic_alert"),
    ("cargo", "Cargo", "Cargo / hazmat", "unsafe_load_ac", "unsafe_load_basic_alert"),
    ("crash", "CrInd", "Crash indicator", "crash_ind_ac", "crash_ind_basic_alert"),
)

# Universe-wide flag counts in the 2026-10-02 SMS window (cargo / crash-ind unused).
SMS_WINDOW_FATALITIES = 7675
SMS_FLAG_COUNTS = {
    "unsafe": 41,
    "hos": 604,
    "fit": 746,
    "drugs": 607,
    "maint": 326,
    "cargo": 0,
    "crash": 0,
}
ACTIVE_FLAG_WEIGHTS: dict[str, float] = {}


def default_flag_weights() -> dict[str, float]:
    """sum(fatalities) / count(this flag). Unused flags (count 0) score 0."""
    n_fat = float(SMS_WINDOW_FATALITIES)
    return {
        key: (n_fat / n if n else 0.0)
        for key, n in SMS_FLAG_COUNTS.items()
    }


def set_flag_weights(weights: dict[str, float] | None = None) -> dict[str, float]:
    """Install per-flag expected-death weights and refresh hover TERMS."""
    w = dict(weights or default_flag_weights())
    ACTIVE_FLAG_WEIGHTS.clear()
    ACTIVE_FLAG_WEIGHTS.update(w)
    for key, _short, title, _ac, _al in BASIC_CATEGORIES:
        ww = float(w.get(key) or 0.0)
        if ww <= 0:
            TERMS[f"basic_{key}"] = (
                f"{title} BASIC over the intervention threshold. "
                "No alerts in the current SMS window, so the universe weight is 0."
            )
        else:
            TERMS[f"basic_{key}"] = (
                f"{title} BASIC over the intervention threshold. "
                f"Universe weight {ww:.1f} expected deaths "
                f"(sum(fatalities)/count of this flag)."
            )
    return w


set_flag_weights()


def sms_category_alert_sql(ac_col: str, alert_col: str) -> str:
    return f"({sms_flag_true_sql(ac_col)} OR {sms_flag_true_sql(alert_col)})"


def sms_basic_alert_sql() -> str:
    return " OR ".join(
        sms_category_alert_sql(ac, al) for _, _, _, ac, al in BASIC_CATEGORIES
    )


def sms_basic_flags_sql() -> str:
    parts = [
        f"(CASE WHEN {sms_category_alert_sql(ac, al)} THEN 1 ELSE 0 END)"
        for _, _, _, ac, al in BASIC_CATEGORIES
    ]
    return " + ".join(parts)


def sms_per_dot_sql() -> str:
    """One row per USDOT: crash / OOS / BASIC flags in the current SMS window."""
    flag_select = ",\n            ".join(
        f"{sms_category_alert_sql(ac, al)} AS basic_{key}"
        for key, _, _, ac, al in BASIC_CATEGORIES
    )
    return f"""
    WITH crash AS (
        SELECT
            dot_norm,
            COUNT(*)::BIGINT AS crashes,
            COALESCE(SUM(TRY_CAST(fatalities AS INTEGER)), 0)::BIGINT AS fatalities,
            COALESCE(SUM(TRY_CAST(injuries AS INTEGER)), 0)::BIGINT AS injuries
        FROM fmcsa_sms_crashes
        WHERE dot_norm IS NOT NULL
        GROUP BY 1
    ),
    insp AS (
        SELECT
            dot_norm,
            COUNT(*)::BIGINT AS inspections,
            SUM(CASE WHEN TRY_CAST(oos_total AS INTEGER) > 0 THEN 1 ELSE 0 END)
                ::BIGINT AS oos_inspections
        FROM fmcsa_sms_inspections
        WHERE dot_norm IS NOT NULL
        GROUP BY 1
    ),
    basic AS (
        SELECT
            dot_norm,
            {flag_select},
            ({sms_basic_flags_sql()})::BIGINT AS basic_flags,
            ({sms_basic_alert_sql()}) AS basic_alert
        FROM fmcsa_sms_basics
        WHERE dot_norm IS NOT NULL
    )
    SELECT
        dot_norm,
        COALESCE(c.crashes, 0) AS crashes,
        COALESCE(c.fatalities, 0) AS fatalities,
        COALESCE(c.injuries, 0) AS injuries,
        COALESCE(i.oos_inspections, 0) AS oos_inspections,
        COALESCE(b.basic_alert, FALSE) AS basic_alert,
        COALESCE(b.basic_flags, 0) AS basic_flags,
        COALESCE(b.basic_unsafe, FALSE) AS basic_unsafe,
        COALESCE(b.basic_hos, FALSE) AS basic_hos,
        COALESCE(b.basic_fit, FALSE) AS basic_fit,
        COALESCE(b.basic_drugs, FALSE) AS basic_drugs,
        COALESCE(b.basic_maint, FALSE) AS basic_maint,
        COALESCE(b.basic_cargo, FALSE) AS basic_cargo,
        COALESCE(b.basic_crash, FALSE) AS basic_crash
    FROM crash c
    FULL OUTER JOIN insp i USING (dot_norm)
    FULL OUTER JOIN basic b USING (dot_norm)
    """


def sms_tables_present(conn) -> bool:
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM information_schema.tables
            WHERE lower(table_name) IN (
                'fmcsa_sms_crashes', 'fmcsa_sms_inspections', 'fmcsa_sms_basics'
            )
            """
        ).fetchone()
        return bool(row and int(row[0]) >= 3)
    except Exception:
        return False


def normalize_dot_value(value) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    try:
        return str(int(s))
    except ValueError:
        return s.lstrip("0") or "0"


def load_sms_by_dot(conn) -> dict[str, dict] | None:
    """Map dot_norm → SMS totals. None when SMS tables are missing."""
    if not sms_tables_present(conn):
        return None
    rows = conn.execute(sms_per_dot_sql()).fetchall()
    cols = [d[0] for d in conn.description]
    out: dict[str, dict] = {}
    for row in rows:
        rec = dict(zip(cols, row))
        key = str(rec.get("dot_norm") or "")
        if key:
            out[key] = rec
    return out


def attach_sms(carriers: list[dict], sms_by_dot: dict[str, dict] | None) -> None:
    if not sms_by_dot:
        return
    for c in carriers:
        rec = sms_by_dot.get(normalize_dot_value(c.get("dot_number"))) or {}
        c["fatalities"] = int(rec.get("fatalities") or 0)
        c["crashes"] = int(rec.get("crashes") or 0)
        c["oos_inspections"] = int(rec.get("oos_inspections") or 0)
        c["basic_alert"] = bool(rec.get("basic_alert"))
        c["basic_flags"] = int(rec.get("basic_flags") or 0)
        for key, _, _, _, _ in BASIC_CATEGORIES:
            c[f"basic_{key}"] = bool(rec.get(f"basic_{key}"))


def sms_cluster_totals(carriers: list[dict]) -> dict:
    return {
        "sms_fatalities": sum(int(c.get("fatalities") or 0) for c in carriers),
        "sms_crashes": sum(int(c.get("crashes") or 0) for c in carriers),
        "sms_oos_inspections": sum(int(c.get("oos_inspections") or 0) for c in carriers),
        "sms_basic_alert_carriers": sum(1 for c in carriers if c.get("basic_alert")),
    }


class FmcsaSafetyProcessor:
    def __init__(self, db_ops: DatabaseOperations, data_dir: str | Path):
        self.db_ops = db_ops
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def run(self) -> Dict[str, int]:
        log_info("=== Starting DOT safety step (SMS crash / inspection / BASIC) ===")
        stats: Dict[str, int] = {"downloads": 0}
        for spec in DATASETS:
            dest = self.data_dir / spec["filename"]
            marker = self.data_dir / f".{spec['key']}_ingest.json"
            try:
                if ensure_download(spec["url"], dest, timeout=0):
                    stats["downloads"] += 1
            except Exception as exc:
                log_warning(f"SMS {spec['key']} download failed: {exc}")
                meta_path = dest.with_suffix(dest.suffix + ".meta.json")
                if not meta_path.exists():
                    stats[spec["key"]] = 0
                    continue
            if self._ingest_current(dest, marker, spec["table"]):
                stats[spec["key"]] = self._table_count(spec["table"])
                continue
            n = self._promote_csv(dest, spec)
            self._save_marker(marker, dest)
            stats[spec["key"]] = n
            log_info(f"  {spec['table']}: {n:,}")
        log_info(f"=== DOT safety complete: {stats} ===")
        return stats

    def _table_count(self, table: str) -> int:
        try:
            row = self.db_ops.execute_query(f"SELECT COUNT(*) FROM {table}").fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0

    def _ingest_current(self, source: Path, marker: Path, table: str) -> bool:
        if not marker.exists() or not source.exists():
            return False
        try:
            meta = json.loads(marker.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        if meta.get("source") != str(source.resolve()):
            return False
        if meta.get("size") != source.stat().st_size:
            return False
        if meta.get("mtime") != source.stat().st_mtime:
            return False
        if meta.get("ingest_version") != INGEST_VERSION:
            return False
        n = self._table_count(table)
        if n > 0:
            log_info(f"Skipping SMS {table} — source unchanged ({n:,} rows)")
            return True
        return False

    def _save_marker(self, marker: Path, source: Path) -> None:
        st = source.stat()
        marker.write_text(
            json.dumps(
                {
                    "source": str(source.resolve()),
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                    "ingest_version": INGEST_VERSION,
                }
            ),
            encoding="utf-8",
        )

    def _promote_csv(self, csv_path: Path, spec: dict) -> int:
        table = spec["table"]
        path = str(csv_path.resolve())
        log_info(f"Loading {csv_path.name} into {table}")
        with self.db_ops.acquire_write_conn() as conn:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
            peek = conn.execute(
                """
                DESCRIBE SELECT * FROM read_csv(?, header := true, all_varchar := true,
                                                ignore_errors := true, sample_size := 1)
                """,
                [path],
            ).fetchall()
            colmap = {r[0].lower(): r[0] for r in peek}
            selects = []
            for dest in spec["keep"]:
                src = colmap.get(dest)
                if src:
                    selects.append(f'"{src}" AS {dest}')
                else:
                    selects.append(f"NULL AS {dest}")
            dot_src = colmap.get("dot_number")
            dot_norm = (
                normalize_dot_sql(f'"{dot_src}"') if dot_src else "NULL"
            )
            conn.execute(
                f"""
                CREATE TABLE {table} AS
                SELECT
                    {", ".join(selects)},
                    {dot_norm} AS dot_norm
                FROM read_csv(?, header := true, all_varchar := true,
                              ignore_errors := true, parallel := true)
                """,
                [path],
            )
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_dot ON {table}(dot_norm)")
            conn.commit()
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return int(n)
