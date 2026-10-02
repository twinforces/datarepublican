#!/usr/bin/env python3
"""Defer XML Address rows to a TSV; load them at the address step.

XML ingest used to INSERT into production Addresses (~105M rows, seven ART
indexes). executemany OOM'd ~3k rows; TSV COPY then spun 100% CPU for 10h+
without finishing. Charities/Grants/Officers still go to DuckDB. Addresses
append here, then `deduplicate_addresses` heap-loads with indexes down.
"""

from __future__ import annotations

import csv
import fcntl
import os
import time
from typing import List, Sequence

from config import global_config
from database_operations import insert_tsv_via_copy, tsv_cell
from logging_utils import log_info, log_warning
from models.address import Address

ADDRESS_INDEX_DDLS = [
    "CREATE INDEX IF NOT EXISTS idx_addresses_canonical ON Addresses(canonical_address)",
    "CREATE INDEX IF NOT EXISTS idx_addresses_colocator ON Addresses(colocator)",
    "CREATE INDEX IF NOT EXISTS idx_addresses_ein ON Addresses(ein)",
    "CREATE INDEX IF NOT EXISTS idx_addresses_geocoding ON Addresses(geocoding_id)",
    "CREATE INDEX IF NOT EXISTS idx_addresses_master_id ON Addresses(master_id)",
    "CREATE INDEX IF NOT EXISTS idx_addresses_type ON Addresses(address_type)",
    "CREATE INDEX IF NOT EXISTS idx_addresses_zip_code ON Addresses(zip_code)",
    "CREATE INDEX IF NOT EXISTS idx_dedup_canon_groups ON Addresses(canonical_address, address_id)",
]

ADDRESS_INDEX_NAMES = [
    "idx_addresses_canonical",
    "idx_addresses_colocator",
    "idx_addresses_ein",
    "idx_addresses_geocoding",
    "idx_addresses_master_id",
    "idx_addresses_type",
    "idx_addresses_zip_code",
    "idx_dedup_canon_groups",
]


def defer_xml_addresses() -> bool:
    return os.environ.get("XML_DEFER_ADDRESSES", "0") in ("1", "true", "TRUE")


def xml_address_tsv_path() -> str:
    override = os.environ.get("XML_ADDRESS_TSV")
    if override:
        return override
    final_dir = getattr(global_config, "final_dir", None) or "/Volumes/Data/final"
    return os.path.join(final_dir, "xml_addresses.tsv")


def address_tsv_columns() -> List[str]:
    return list(Address.get_db_field_names())


def append_xml_addresses(objects: Sequence) -> int:
    """Prep + append Address rows. Safe for a single PDC writer; flock anyway."""
    if not objects:
        return 0
    cols = address_tsv_columns()
    for obj in objects:
        if hasattr(obj, "prep_for_insert"):
            obj.prep_for_insert()
        if hasattr(obj, "set_id_if_needed"):
            obj.set_id_if_needed()
    path = xml_address_tsv_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    lock_path = path + ".lock"
    with open(lock_path, "a", encoding="utf-8") as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        new_file = (not os.path.exists(path)) or os.path.getsize(path) == 0
        with open(path, "a", encoding="utf-8", newline="") as handle:
            writer = csv.writer(
                handle,
                delimiter="\t",
                lineterminator="\n",
                quoting=csv.QUOTE_MINIMAL,
            )
            if new_file:
                writer.writerow(cols)
            for obj in objects:
                writer.writerow([tsv_cell(getattr(obj, col, None)) for col in cols])
            handle.flush()
            os.fsync(handle.fileno())
    log_info(f"Deferred {len(objects)} Address rows to {path}")
    return len(objects)


def load_deferred_xml_addresses(db_ops) -> int:
    """Heap-load the TSV into Addresses with secondary indexes down. Returns rows inserted."""
    path = xml_address_tsv_path()
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        log_info(f"No deferred XML address TSV at {path}")
        return 0
    cols = address_tsv_columns()
    log_info(f"Loading deferred XML addresses from {path} (indexes down)")
    with db_ops.acquire_write_conn() as conn:
        for name in ADDRESS_INDEX_NAMES:
            conn.execute(f"DROP INDEX IF EXISTS {name}")
        conn.commit()
        describe_rows = conn.execute("DESCRIBE Addresses").fetchall()
        type_by_name = {r[0]: r[1] for r in describe_rows}
        duck_types = [str(type_by_name.get(col, "VARCHAR")) for col in cols]
        before = conn.execute("SELECT COUNT(*) FROM Addresses").fetchone()[0]
        insert_tsv_via_copy(conn, "Addresses", cols, duck_types, path)
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM Addresses").fetchone()[0]
        inserted = int(after) - int(before)
        for ddl in ADDRESS_INDEX_DDLS:
            conn.execute(ddl)
        conn.commit()
    loaded = f"{path}.loaded.{time.strftime('%Y%m%d_%H%M%S')}"
    os.rename(path, loaded)
    log_info(f"Loaded {inserted} Address rows from TSV; moved to {loaded}")
    return inserted
