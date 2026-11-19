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
import threading
import time
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import Any, Literal, Protocol

from .core import Operation, calculate_result, readable_result

DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_TIMEOUT_SECONDS = 600.0
TIMEOUT_ENV = "GRAPHLINK_SCIENTIFIC_TIMEOUT_SECONDS"

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
                         inputs: dict, sources: dict | None, input_units: dict | None) -> None:
    """Top-level spawn target: no closure or live graph objects cross processes."""
    try:
        try:
            if operation is None:
                # Some built-in packs use module-level lambdas in OPERATIONS;
                # spawn cannot pickle them. Resolve the registered callable
                # inside the worker instead of changing their public pack API.
                from .catalog import operations
                operation = next(op for op in operations(pack_id) if op.id == operation_id)
            answer: _WorkerReply = ("ok", calculate_result(pack_id, operation, inputs, sources, input_units=input_units))
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


class _Receiver(Protocol):
    """The one pipe method the receiver needs. Context.Pipe() returns a
    Connection on POSIX but a PipeConnection on Windows."""

    def recv(self) -> Any: ...


def _receive_in_daemon_thread(reader: _Receiver) -> asyncio.Future[tuple[object | None, Exception | None]]:
    """Receive without letting an uninterruptible pipe read hold up shutdown.

    On Windows, terminating the writer and closing pipe handles need not wake
    a recv() already waiting for a partial frame. asyncio.to_thread uses the
    loop's default executor, which asyncio.run() joins even after cancellation.
    This dedicated daemon reader instead reports completion to the event loop;
    a cancelled/timed-out run can discard it without waiting for that thread.
    """
    loop = asyncio.get_running_loop()
    finished: asyncio.Future[tuple[object | None, Exception | None]] = loop.create_future()

    def deliver(outcome: tuple[object | None, Exception | None]) -> None:
        if not finished.done():
            finished.set_result(outcome)

    def receive() -> None:
        try:
            outcome: tuple[object | None, Exception | None] = (reader.recv(), None)
        except Exception as exc:
            outcome = (None, exc)
        try:
            loop.call_soon_threadsafe(deliver, outcome)
        except RuntimeError:
            # A cancelled run may already have closed its event loop.
            pass

    threading.Thread(target=receive, name="graphlink-science-pipe-recv", daemon=True).start()
    return finished


async def execute_in_process(pack_id: str, operation: Operation, inputs: dict, sources: dict | None,
                             *, timeout_s: float, cancel_event: asyncio.Event,
                             input_units: dict | None = None) -> dict:
    """Run a spawned, killable calculation, including during stalled pipe reads.

    A partial frame can leave Connection.recv() stuck even after cancellation.
    The receiver must never prevent the deadline, worker reap or loop shutdown.
    """
    started = time.monotonic()

    def elapsed_ms() -> int:
        return max(0, int((time.monotonic() - started) * 1000))

    if cancel_event.is_set():
        raise ExecutionFailure("cancelled", "Scientific calculation cancelled (0 ms elapsed).", elapsed_ms())

    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    worker: BaseProcess | None = None
    receive_future: asyncio.Future[tuple[object | None, Exception | None]] | None = None
    cancel_task: asyncio.Task | None = None
    try:
        try:
            pickle.dumps(operation)
            transferable_operation: Operation | None = operation
        except (pickle.PickleError, AttributeError, TypeError):
            # Registered built-in lambdas are resolved by ID in the worker.
            transferable_operation = None
        try:
            worker = context.Process(
                target=_calculate_in_worker,
                args=(writer, pack_id, operation.id, transferable_operation, inputs, sources, input_units),
                daemon=True, name=f"graphlink-science-{pack_id}-{operation.id}",
            )
            worker.start()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise ExecutionFailure("worker_error", "Unable to start the isolated scientific worker.", elapsed_ms()) from exc
        finally:
            writer.close()

        remaining = timeout_s - (time.monotonic() - started)
        if remaining <= 0:
            raise ExecutionFailure("timeout",
                                   f"Scientific calculation exceeded its {timeout_s:g} s budget ({elapsed_ms()} ms elapsed).",
                                   elapsed_ms())
        # recv() runs outside the event loop and, crucially, outside the
        # default executor whose non-daemon threads block asyncio.run() exit.
        try:
            receive_future = _receive_in_daemon_thread(reader)
        except RuntimeError as exc:
            raise ExecutionFailure("worker_error", "Unable to start the scientific pipe receiver.",
                                   elapsed_ms()) from exc
        cancel_task = asyncio.create_task(cancel_event.wait())
        done, _ = await asyncio.wait(
            {receive_future, cancel_task}, timeout=remaining, return_when=asyncio.FIRST_COMPLETED,
        )
        if cancel_event.is_set():
            raise ExecutionFailure("cancelled", f"Scientific calculation cancelled after {elapsed_ms()} ms.", elapsed_ms())
        if receive_future not in done or time.monotonic() - started >= timeout_s:
            raise ExecutionFailure("timeout",
                                   f"Scientific calculation exceeded its {timeout_s:g} s budget ({elapsed_ms()} ms elapsed).",
                                   elapsed_ms())
        answer, receive_error = receive_future.result()
        if receive_error is not None:
            raise ExecutionFailure("worker_error", "The scientific worker exited without a complete result.",
                                   elapsed_ms()) from None
        if not isinstance(answer, tuple) or len(answer) != 2:
            raise ExecutionFailure("worker_error", "The scientific worker returned a malformed reply.", elapsed_ms())
        status, payload = answer
        if status == "ok":
            if (not isinstance(payload, dict) or payload.get("operationId") != operation.id
                    or payload.get("inputs") != inputs
                    or not readable_result({"version": 1, "latest": payload}, pack_id)
                    or payload["provenance"]["sources"] != (sources or {})
                    or (input_units is not None and payload["provenance"].get("inputUnits") != input_units)):
                raise ExecutionFailure("worker_error", "The scientific worker returned an incomplete or invalid result.",
                                       elapsed_ms())
            return payload
        if status in ("invalid_input", "worker_error") and isinstance(payload, str):
            raise ExecutionFailure(status, payload, elapsed_ms())
        raise ExecutionFailure("worker_error", "The scientific worker returned a malformed reply.", elapsed_ms())
    finally:
        if cancel_task is not None:
            cancel_task.cancel()
        if receive_future is not None:
            receive_future.cancel()
        try:
            if worker is not None:
                _stop_worker(worker)
        finally:
            # Terminate/reap the sender and close *both* ends on every path,
            # including process-start failures and external task cancellation.
            # Never await/join a recv thread: Windows may not interrupt it.
            writer.close()
            reader.close()
