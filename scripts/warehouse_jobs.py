"""Background warehouse fetches with real partition-by-partition progress.

Ported from the `redshift-api` proxy's /query/jobs endpoints so that going
direct to Snowflake (see `warehouse.py`) did not cost the React client its
progress bar. The job id / status contract is unchanged, so
`web_api`'s /api/redshift/table/{start,status} routes and the client polling
them did not need to change.

Each job runs in its own OS process, not a thread. That is deliberate and
load-bearing: a big fetch (millions of rows, all-TEXT columns) spends most
of its time in CPU-bound JSON decoding of each partition's response, and
CPython's C json accelerator holds the GIL for that entire decode. Doing
that work in a background *thread* was measured stalling every other
request on the old service - including a trivial, unrelated "SELECT 1" -
for 20s+ while a 9M-row fetch decoded, because the GIL never came back. A
separate process has its own GIL and cannot starve the API that way.

Job state is exchanged through small JSON files in a temp directory rather
than shared memory: progress is a few integers, cheap to write on each
partition and read on each poll, while the result (potentially large) is
written once by the child and read once by the status endpoint. This is an
internal analyst tool, so a temp dir with a TTL sweep is enough - nothing
needs to survive a restart.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import tempfile
import time
import uuid
from typing import Any

import warehouse

_JOBS_DIR = os.path.join(tempfile.gettempdir(), "sc_warehouse_jobs")
os.makedirs(_JOBS_DIR, exist_ok=True)
_JOB_TTL_SECONDS = 1800


def _meta_path(job_id: str) -> str:
    return os.path.join(_JOBS_DIR, f"{job_id}.meta.json")


def _result_path(job_id: str) -> str:
    return os.path.join(_JOBS_DIR, f"{job_id}.result.json")


def _write_json_atomic(path: str, payload: dict) -> None:
    # Write-then-rename so a poll never reads a half-written file.
    tmp_path = f"{path}.tmp-{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp_path, path)


def _prune() -> None:
    cutoff = time.time() - _JOB_TTL_SECONDS
    for name in os.listdir(_JOBS_DIR):
        path = os.path.join(_JOBS_DIR, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


def _run_job(job_id: str, sql: str, limit: int) -> None:
    """Entry point for the job's child process. Must stay a module-level
    function: multiprocessing on Windows re-imports this module in the child
    and resolves the target by name, so a closure or a bound method would
    not survive the trip."""
    meta_path = _meta_path(job_id)
    last = {"total": 1}

    def on_progress(done: int, total: int) -> None:
        last["total"] = total
        _write_json_atomic(
            meta_path,
            {"status": "running", "partitionsDone": done, "partitionsTotal": total},
        )

    try:
        columns, rows = warehouse.fetch_all(sql, on_progress=on_progress)
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller via status()
        _write_json_atomic(meta_path, {"status": "error", "error": str(exc)})
        return

    # The result file is written before the status flips to "done", so a
    # poll can never observe "done" with the result not yet on disk.
    _write_json_atomic(_result_path(job_id), {"columns": columns, "rows": rows})
    _write_json_atomic(
        meta_path,
        {
            "status": "done",
            "partitionsDone": last["total"],
            "partitionsTotal": last["total"],
            "limit": limit,
        },
    )


def start(sql: str, limit: int | None = None) -> str:
    """Validate `sql`, start it as a background job, and return its job id."""
    wrapped, effective_limit = warehouse.validate_query(sql, limit)
    _prune()
    job_id = uuid.uuid4().hex
    _write_json_atomic(
        _meta_path(job_id),
        {"status": "running", "partitionsDone": 0, "partitionsTotal": 1, "limit": effective_limit},
    )
    mp.Process(target=_run_job, args=(job_id, wrapped, effective_limit), daemon=True).start()
    return job_id


def status(job_id: str) -> dict[str, Any]:
    """Poll one job, in the same {status, partitionsDone, partitionsTotal}
    shape the old proxy returned - plus columns/rows once it is done."""
    try:
        with open(_meta_path(job_id), encoding="utf-8") as fh:
            job = json.load(fh)
    except (OSError, ValueError) as exc:
        raise warehouse.WarehouseError("Unknown or expired job") from exc

    result: dict[str, Any] = {
        "status": job["status"],
        "partitionsDone": job.get("partitionsDone", 0),
        "partitionsTotal": job.get("partitionsTotal", 1),
    }
    if job["status"] == "error":
        result["error"] = job.get("error") or "The query failed."
    elif job["status"] == "done":
        try:
            with open(_result_path(job_id), encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError) as exc:
            raise warehouse.WarehouseError("The finished job's result has expired") from exc
        result["columns"] = payload["columns"]
        result["rows"] = payload["rows"]
        result["row_count"] = len(payload["rows"])
        result["truncated"] = len(payload["rows"]) >= job.get("limit", len(payload["rows"]))
    return result
