#!/usr/bin/env python3
"""
geolocate_grok_processor.py — Grok batch geocoding for grok_pending rows.

Runs after geolocate_new (free APIs). Submits xAI Batch API jobs, polls, merges
results back into Geocoding. Resumable via geolocate_grok_state.json.

When grok_pending is fully drained, exports grok:* failures to
{final_dir}/grok_failures_for_patterns.tsv.gz for pattern-rule mining.
"""

from __future__ import annotations

import csv
import gzip
import json
import os
import shutil
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI
from pydantic import ValidationError

from config import global_config
from constants import (
    GEOCODING_GROK_BATCH_SIZE,
    GEOCODING_GROK_EXPORT_ROWS,
    GEOCODING_GROK_POLL_INTERVAL,
    GROK_FAILURES_EXPORT_FILE,
)
from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from geocoding_api_processor import BatchGeocodeOutput, GeocodingAPIProcessor, GeocodingWorkUnit
from logging_utils import log_error, log_info, log_warning
from pending_database_context import PendingDatabaseContext


STATE_FILE = Path(__file__).parent / "geolocate_grok_state.json"


def _xai_client() -> OpenAI:
    key = os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY") or os.getenv("X_API_KEY")
    if not key:
        raise RuntimeError("No XAI_API_KEY / GROK_API_KEY / X_API_KEY in environment")
    return OpenAI(api_key=key, base_url="https://api.x.ai/v1")


def _load_state() -> Dict[str, Any]:
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"active_batch": None, "completed_batches": 0, "rows_applied": 0}


def _save_state(state: Dict[str, Any]) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _chunk(items: List, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _extract_completion_content(item: Dict[str, Any]) -> Optional[str]:
    if "batch_result" in item:
        try:
            br = item["batch_result"]
            resp = br.get("response") or {}
            cgc = resp.get("chat_get_completion") or {}
            choices = cgc.get("choices") or []
            if choices:
                msg = choices[0].get("message") or {}
                content = msg.get("content")
                if isinstance(content, str):
                    return content
        except Exception:
            pass
    if "response" in item:
        resp = item["response"]
        if isinstance(resp, dict):
            body = resp.get("body")
            if isinstance(body, dict):
                choices = body.get("choices") or []
                if choices:
                    msg = choices[0].get("message") or {}
                    content = msg.get("content")
                    if isinstance(content, str):
                        return content
            if isinstance(body, str):
                return body
    if "error" in item and item["error"]:
        return None
    return None


def _load_batch_results(client: OpenAI, batch_id: str, out_path: Path) -> List[Dict[str, Any]]:
    batch = client.batches.retrieve(batch_id)
    if getattr(batch, "output_file_id", None):
        content = client.files.content(batch.output_file_id)
        out_path.write_bytes(content.content)
        results = []
        for line in out_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                results.append(json.loads(line))
        return results

    import requests
    key = os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY") or os.getenv("X_API_KEY")
    url = f"https://api.x.ai/v1/batches/{batch_id}/results?limit=10000"
    r = requests.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=120)
    r.raise_for_status()
    data = r.json()
    out_path.write_text(r.text, encoding="utf-8")
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data if isinstance(data, list) else [data]


class GeolocateGrokProcessor:
    """xAI Batch API processor for grok_pending Geocoding rows."""

    def __init__(self, db_ops: DatabaseOperations):
        self.db_ops = db_ops
        self.export_rows = GEOCODING_GROK_EXPORT_ROWS
        self.prompt_batch_size = GEOCODING_GROK_BATCH_SIZE

    def _fetch_grok_pending_rows(self, limit: int) -> List[Tuple]:
        query = """
            SELECT geocoding_id, normalized_address, attempt_count, canonical_address, address_count, geocoding_status
            FROM Geocoding
            WHERE geocoding_status = 'grok_pending'
            ORDER BY address_count DESC NULLS LAST, geocoding_id
            LIMIT ?
        """
        return self.db_ops.execute_query(query, (limit,)).fetchall()

    def _rows_to_units(self, rows: List[Tuple]) -> List[GeocodingWorkUnit]:
        units = []
        for row in rows:
            data = {
                "geocoding_id": row[0],
                "normalized_address": row[1],
                "attempt_count": row[2],
                "canonical_address": row[3],
                "address_count": row[4] or 0,
                "geocoding_status": row[5],
            }
            units.append(GeocodingWorkUnit.work_item("feed", data))
        return units

    def _build_jsonl(
        self, units: List[GeocodingWorkUnit], jsonl_path: Path,
    ) -> Dict[str, List[GeocodingWorkUnit]]:
        """Write batch JSONL; return custom_id -> units map."""
        id_map: Dict[str, List[GeocodingWorkUnit]] = {}
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for idx, chunk in enumerate(_chunk(units, self.prompt_batch_size)):
                custom_id = f"geocode_{idx:06d}"
                id_map[custom_id] = chunk
                rec = {
                    "custom_id": custom_id,
                    "method": "POST",
                    "url": "/v1/chat/completions",
                    "body": GeocodingAPIProcessor.build_grok_geocode_request_body(chunk),
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return id_map

    def _queue_grok_update(
        self,
        ctx: PendingDatabaseContext,
        unit: GeocodingWorkUnit,
        fields: Dict[str, Any],
    ) -> None:
        ctx.addOperationToDatabase(DatabaseOperation(
            operation_type=DatabaseOperationType.GENERIC_UPDATE,
            data={
                "table": "Geocoding",
                "updates": [fields],
                "id_column": "geocoding_id",
            },
        ))
        ctx.addOperationToDatabase(DatabaseOperation(
            operation_type=DatabaseOperationType.PROGRESS_UPDATE,
            data={"count": unit.address_count},
        ))

    def _apply_batch_results(
        self,
        results: List[Dict[str, Any]],
        id_map: Dict[str, List[GeocodingWorkUnit]],
    ) -> Tuple[int, int]:
        matched = 0
        classified = 0
        now = datetime.now().isoformat()
        ctx = PendingDatabaseContext()
        unit_by_id = {
            u.geocoding_id: u
            for units in id_map.values()
            for u in units
        }
        seen_ids: set[str] = set()

        def apply_unit(unit: GeocodingWorkUnit, res_item=None) -> None:
            nonlocal matched, classified
            if unit.geocoding_id in seen_ids:
                return
            seen_ids.add(unit.geocoding_id)
            fields = GeocodingAPIProcessor.grok_result_update_fields(unit, res_item, now=now)
            if fields["geocoding_status"] == "Match:Grok-4":
                matched += 1
            else:
                classified += 1
            self._queue_grok_update(ctx, unit, fields)

        for item in results:
            custom_id = item.get("custom_id") or item.get("batch_request_id") or ""
            chunk = id_map.get(custom_id, [])
            content = _extract_completion_content(item)
            if not content:
                for unit in chunk:
                    apply_unit(unit, None)
                continue
            try:
                parsed = BatchGeocodeOutput.model_validate_json(content)
            except ValidationError as e:
                log_warning(f"Grok batch parse fail custom_id={custom_id}: {e}")
                for unit in chunk:
                    apply_unit(unit, None)
                continue

            parsed_dict = {res.id: res for res in parsed.results}
            for unit in chunk:
                apply_unit(unit, parsed_dict.get(unit.geocoding_id))

        for gid, unit in unit_by_id.items():
            if gid not in seen_ids:
                apply_unit(unit, None)

        if ctx.getTotalObjectCount() > 0:
            ctx.save_to_database(self.db_ops, checkpoint=True)
        return matched, classified

    def _submit_jsonl(self, client: OpenAI, jsonl_path: Path, name: str) -> str:
        with open(jsonl_path, "rb") as f:
            file_obj = client.files.create(file=f, purpose="batch")
        batch = client.batches.create(
            input_file_id=file_obj.id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
            extra_body={"name": name},
        )
        log_info(f"Submitted xAI batch {batch.id} ({name}) from {jsonl_path.name}")
        print(f"[geolocate_grok] submitted batch_id={batch.id} name={name} requests={jsonl_path.name}", flush=True)
        return batch.id

    def _poll_batch(self, client: OpenAI, batch_id: str) -> str:
        while True:
            batch = client.batches.retrieve(batch_id)
            status = batch.status
            counts = getattr(batch, "request_counts", None)
            print(f"[geolocate_grok] polling {batch_id} status={status} counts={counts}", flush=True)
            if status in ("completed", "failed", "expired", "cancelled"):
                return status
            time.sleep(GEOCODING_GROK_POLL_INTERVAL)

    def _process_active_batch(self, client: OpenAI, state: Dict[str, Any]) -> bool:
        active = state.get("active_batch")
        if not active:
            return False
        batch_id = active["batch_id"]
        status = self._poll_batch(client, batch_id)
        if status != "completed":
            log_error(f"Batch {batch_id} ended with status={status}")
            state["active_batch"] = None
            _save_state(state)
            return False
        results_path = Path(active["results_path"])
        id_map = {
            custom_id: [GeocodingWorkUnit.work_item("feed", d) for d in serialized]
            for custom_id, serialized in active["id_map"].items()
        }
        results = _load_batch_results(client, batch_id, results_path)
        matched, classified = self._apply_batch_results(results, id_map)
        state["active_batch"] = None
        state["completed_batches"] = state.get("completed_batches", 0) + 1
        state["rows_applied"] = state.get("rows_applied", 0) + matched + classified
        _save_state(state)
        print(
            f"[geolocate_grok] batch {batch_id} applied matched={matched} classified={classified}",
            flush=True,
        )
        return True

    def export_grok_failures(self, output_file: str = GROK_FAILURES_EXPORT_FILE) -> int:
        """Export grok:* classified failures grouped by failure_code for pattern-rule mining."""
        output_path = os.path.join(global_config.final_dir, output_file)
        rows = self.db_ops.execute_query("""
            SELECT
                split_part(geocoding_status, ':', 2) AS failure_code,
                canonical_address,
                matched_address,
                normalized_address,
                address_count,
                geocoding_id
            FROM Geocoding
            WHERE geocoding_status LIKE 'grok:%'
            ORDER BY
                failure_code,
                address_count DESC NULLS LAST,
                canonical_address
        """).fetchall()

        os.makedirs(global_config.final_dir or ".", exist_ok=True)
        tmp_path = output_path + ".tmp"
        try:
            with gzip.open(tmp_path, "wt", encoding="utf-8", newline="") as f:
                writer = csv.writer(f, delimiter="\t", lineterminator="\n")
                writer.writerow([
                    "failure_code",
                    "canonical_address",
                    "reason",
                    "address_count",
                    "normalized_address",
                    "geocoding_id",
                ])
                for code, canon, reason, norm, count, gid in rows:
                    writer.writerow([
                        code or "UNKN",
                        canon or "",
                        reason or "",
                        count or 0,
                        norm or "",
                        gid or "",
                    ])
            shutil.move(tmp_path, output_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        by_code = Counter((r[0] or "UNKN") for r in rows)
        summary = ", ".join(f"{k}={v:,}" for k, v in sorted(by_code.items()))
        log_info(f"Exported {len(rows):,} grok failures → {output_path} ({summary})")
        print(
            f"[geolocate_grok] exported {len(rows):,} failures → {output_path}"
            + (f" ({summary})" if summary else ""),
            flush=True,
        )
        return len(rows)

    def run(self, max_batches: Optional[int] = None) -> int:
        """Submit/poll/apply batches until grok_pending is empty or max_batches hit."""
        state = _load_state()
        client = _xai_client()
        batches_done = 0
        total_applied = 0

        if state.get("active_batch"):
            print("[geolocate_grok] resuming active batch from state", flush=True)
            if self._process_active_batch(client, state):
                batches_done += 1

        while True:
            if max_batches is not None and batches_done >= max_batches:
                break
            pending = self.db_ops.execute_query(
                "SELECT COUNT(*) FROM Geocoding WHERE geocoding_status = 'grok_pending'"
            ).fetchone()[0]
            if pending == 0:
                print("[geolocate_grok] no grok_pending rows remaining", flush=True)
                break

            rows = self._fetch_grok_pending_rows(self.export_rows)
            if not rows:
                break
            units = self._rows_to_units(rows)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            jsonl_path = Path(__file__).parent / f"geolocate_grok_batch_{ts}.jsonl"
            id_map = self._build_jsonl(units, jsonl_path)
            serialized_map = {
                cid: [u.data for u in chunk]
                for cid, chunk in id_map.items()
            }
            name = f"geolocate_grok_{ts}"
            batch_id = self._submit_jsonl(client, jsonl_path, name)
            state["active_batch"] = {
                "batch_id": batch_id,
                "name": name,
                "jsonl_path": str(jsonl_path),
                "results_path": str(jsonl_path.with_suffix(".results.jsonl")),
                "id_map": serialized_map,
                "row_count": len(units),
            }
            _save_state(state)

            status = self._poll_batch(client, batch_id)
            if status != "completed":
                log_error(f"Batch {batch_id} ended with status={status}")
                break

            results_path = Path(state["active_batch"]["results_path"])
            id_map_units = {
                cid: [GeocodingWorkUnit.work_item("feed", d) for d in chunk]
                for cid, chunk in serialized_map.items()
            }
            results = _load_batch_results(client, batch_id, results_path)
            matched, classified = self._apply_batch_results(results, id_map_units)
            total_applied += matched + classified
            state["active_batch"] = None
            state["completed_batches"] = state.get("completed_batches", 0) + 1
            state["rows_applied"] = state.get("rows_applied", 0) + matched + classified
            _save_state(state)
            batches_done += 1
            print(
                f"[geolocate_grok] batch {batches_done} done matched={matched} classified={classified} "
                f"total_applied={total_applied}",
                flush=True,
            )

        print(
            f"[geolocate_grok] SUMMARY batches={batches_done} rows_applied={total_applied}",
            flush=True,
        )

        pending = self.db_ops.execute_query(
            "SELECT COUNT(*) FROM Geocoding WHERE geocoding_status = 'grok_pending'"
        ).fetchone()[0]
        if pending == 0 and not state.get("active_batch"):
            self.export_grok_failures()
        elif pending > 0:
            print(
                f"[geolocate_grok] skipping failure export — {pending:,} grok_pending still remain",
                flush=True,
            )

        return total_applied