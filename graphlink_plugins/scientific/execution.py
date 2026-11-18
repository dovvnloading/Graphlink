"""Killable, bounded scientific calculations (Windows-safe spawn workers).

Only the validated, detached calculation is sent to the child. The graph,
plugin grants, and undo journal stay on the session event loop in the parent.
"""

from __future__ import annotations

import asyncio
import math
import multiprocessing
import os
import pickle
import time
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import Literal

from .core import Operation, calculate_result

DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_TIMEOUT_SECONDS = 600.0
TIMEOUT_ENV = "GRAPHLINK_SCIENTIFIC_TIMEOUT_SECONDS"
_POLL_SECONDS = 0.025

_WorkerReply = tuple[Literal["ok"], dict] | tuple[Literal["invalid_input", "worker_error"], str]


class ExecutionFailure(Exception):
    """Typed, non-artifact error: never stored as a valid scientific result."""

    def __init__(self, error_type: str, message: str, elapsed_ms: int):
        super().__init__(message)
        self.error_type = error_type
        self.elapsed_ms = elapsed_ms

    def as_result(self) -> dict:
        return {"ok": False, "errorType": self.error_type,
                "elapsedMs": self.elapsed_ms, "error": str(self)}


def execution_timeout(operation: Operation) -> float:
    """Pack override wins over the deployment-wide default; neither is unbounded."""
    raw = (operation.timeout_s if operation.timeout_s is not None else
           os.environ.get(TIMEOUT_ENV, str(DEFAULT_TIMEOUT_SECONDS)))
    try:
        seconds = float(raw)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{TIMEOUT_ENV} / Operation.timeout_s must be a finite positive number of seconds.") from None
    if not math.isfinite(seconds) or not 0 < seconds <= MAX_TIMEOUT_SECONDS:
        raise ValueError(f"Scientific timeout must be greater than 0 and at most {MAX_TIMEOUT_SECONDS:g} seconds.")
    return seconds


def _calculate_in_worker(sender: Connection, pack_id: str, operation_id: str, operation: Operation | None,
                         inputs: dict, sources: dict | None, input_units: dict | None,
                         uncertainty_inputs: dict | None = None,
                         uncertainty_method: str = "auto") -> None:
    """Top-level spawn target: no closure or live graph objects cross processes."""
    try:
        try:
            if operation is None:
                # Some built-in packs use module-level lambdas in OPERATIONS;
                # spawn cannot pickle them. Resolve the registered callable
                # inside the worker instead of changing their public pack API.
                from .catalog import operations
                operation = next(op for op in operations(pack_id) if op.id == operation_id)
            answer: _WorkerReply = ("ok", calculate_result(
                pack_id, operation, inputs, sources, input_units=input_units,
                uncertainty_inputs=uncertainty_inputs, uncertainty_method=uncertainty_method))
        except ValueError as exc:
            answer = ("invalid_input", str(exc))
        except Exception:
            # Do not return arbitrary exceptions or their tracebacks over IPC.
            answer = ("worker_error", "The scientific worker failed while calculating this operation.")
        try:
            sender.send(answer)
        except (BrokenPipeError, OSError, EOFError):
            pass  # Parent timed out/cancelled and closed its end.
    finally:
        sender.close()


def _stop_worker(process: BaseProcess) -> None:
    """Reap even on timeout, explicit cancel, or parent task cancellation."""
    if process.pid is None:
        process.close()
        return
    if process.is_alive():
        process.terminate()
    process.join(timeout=0.5)
    if process.is_alive():
        process.kill()
        process.join(timeout=1.0)
    if process.is_alive():
        # Never report a successful cancellation while its worker survives.
        raise RuntimeError("Unable to stop a scientific calculation worker.")
    process.close()


async def execute_in_process(pack_id: str, operation: Operation, inputs: dict, sources: dict | None,
                             *, timeout_s: float, cancel_event: asyncio.Event,
                             input_units: dict | None = None,
                             uncertainty_inputs: dict | None = None,
                             uncertainty_method: str = "auto") -> dict:
    """Run without executor threads; terminate/reap the child on every exit.

    Spawn works on Windows (forking the multithreaded server would be unsafe).
    IPC uses a one-way Pipe rather than multiprocessing.Queue's feeder thread.
    """
    started = time.monotonic()

    def elapsed_ms() -> int:
        return max(0, int((time.monotonic() - started) * 1000))

    if cancel_event.is_set():
        raise ExecutionFailure("cancelled", "Scientific calculation cancelled (0 ms elapsed).", elapsed_ms())
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    try:
        pickle.dumps(operation)
        transferable_operation: Operation | None = operation
    except (pickle.PickleError, AttributeError, TypeError):
        transferable_operation = None  # Resolve built-in operation by ID in child.
    worker = context.Process(
        target=_calculate_in_worker,
        args=(writer, pack_id, operation.id, transferable_operation, inputs, sources,
              input_units, uncertainty_inputs, uncertainty_method),
        daemon=True, name=f"graphlink-science-{pack_id}-{operation.id}",
    )
    try:
        try:
            worker.start()
        except (OSError, RuntimeError, TypeError) as exc:
            raise ExecutionFailure("worker_error", "Unable to start the isolated scientific worker.", elapsed_ms()) from exc
        finally:
            writer.close()
        while True:
            if cancel_event.is_set():
                raise ExecutionFailure("cancelled", f"Scientific calculation cancelled after {elapsed_ms()} ms.", elapsed_ms())
            remaining = timeout_s - (time.monotonic() - started)
            if remaining <= 0:
                raise ExecutionFailure("timeout", f"Scientific calculation exceeded its {timeout_s:g} s budget ({elapsed_ms()} ms elapsed).", elapsed_ms())
            if reader.poll():
                try:
                    status, payload = reader.recv()
                except (EOFError, OSError):
                    break
                if cancel_event.is_set():
                    raise ExecutionFailure("cancelled", f"Scientific calculation cancelled after {elapsed_ms()} ms.", elapsed_ms())
                if status == "ok":
                    return payload
                raise ExecutionFailure(status, str(payload), elapsed_ms())
            if not worker.is_alive():
                break
            # Cancellation is immediately wakeable; the short poll bound
            # accommodates Windows where asyncio.add_reader is unavailable.
            try:
                await asyncio.wait_for(cancel_event.wait(), timeout=min(_POLL_SECONDS, remaining))
            except asyncio.TimeoutError:
                pass
        raise ExecutionFailure("worker_error", "The scientific worker exited without a complete result.", elapsed_ms())
    finally:
        reader.close()
        writer.close()
        _stop_worker(worker)
