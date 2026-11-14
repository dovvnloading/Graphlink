"""Remote attempts must be durable before dispatch and fail closed on restart."""

import asyncio
import copy
import hashlib

import pytest

from backend import chat_library
from backend.canvas import SceneDocument
from backend.domain.graph_run_external import ExternalCall, dispatch_external_attempt
from backend.domain.graph_run_history import restore_graph_runs
from backend.domain.graph_runner import plan_graph_run
from backend.domain.model import SceneError
from backend.events import SessionBus


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sample_step():
    return {
        "nodeId": "n2", "sourceId": "n1", "kind": "note", "operation": "model",
        "label": "Model reply", "status": "pending", "detail": "Waiting to run.",
        "inputHash": digest("frozen prompt"), "configHash": digest("model"),
        "targetHash": digest("empty destination"),
    }


def sample_call(step):
    return ExternalCall(
        kind="model", provider_name="profile/openai", model_id="example-model",
        input_ref="artifact/version-1", input_hash=step["inputHash"],
    )


def saved_run(step):
    return {
        "runId": "remote-run-01", "previewHash": digest("preview"),
        "selectionHash": digest("selection"), "startedAt": "2026-11-14T00:00:00Z",
        "status": "running", "steps": [copy.deepcopy(step)],
    }


def test_external_attempt_checkpoint_precedes_any_dispatch_and_receipt_is_atomic():
    async def scenario():
        step = sample_step()
        snapshots = []
        called = []

        async def checkpoint():
            snapshots.append(copy.deepcopy(step))
            return True

        async def invoke(request_id):
            assert len(snapshots) == 1
            assert snapshots[0]["externalAttempt"]["phase"] == "dispatched"
            assert snapshots[0]["externalAttempt"]["requestId"] == request_id
            assert snapshots[0]["status"] == "running"
            called.append(request_id)
            return {"answer": "ok", "secret-response": "do not journal"}

        async def apply(response):
            assert response["answer"] == "ok"
            return digest(response["answer"])

        result = await dispatch_external_attempt(
            step, sample_call(step), checkpoint=checkpoint, invoke=invoke, apply_result=apply,
        )
        assert result == digest("ok")
        assert len(called) == 1 and len(snapshots) == 2
        assert snapshots[-1]["status"] == "completed"
        assert snapshots[-1]["externalAttempt"]["phase"] == "completed"
        assert snapshots[-1]["outputHash"] == result
        assert "secret-response" not in str(snapshots)
        with pytest.raises(SceneError, match="already attempted"):
            await dispatch_external_attempt(
                step, sample_call(step), checkpoint=checkpoint, invoke=invoke, apply_result=apply,
            )
        assert len(called) == 1

    asyncio.run(scenario())


def test_persistence_failure_prevents_dispatch_and_does_not_leave_a_false_receipt():
    async def scenario():
        step = sample_step()
        dispatched = []

        async def fail_checkpoint():
            raise OSError("disk full")

        async def invoke(request_id):
            dispatched.append(request_id)

        async def apply(_response):
            return digest("never")

        with pytest.raises(OSError, match="disk full"):
            await dispatch_external_attempt(
                step, sample_call(step), checkpoint=fail_checkpoint,
                invoke=invoke, apply_result=apply,
            )
        assert not dispatched
        assert step["status"] == "pending" and "externalAttempt" not in step

    asyncio.run(scenario())


def test_false_checkpoint_does_not_send_a_request():
    async def scenario():
        step = sample_step()
        called = []

        async def checkpoint():
            return False

        async def invoke(request_id):
            called.append(request_id)

        async def apply(_response):
            return digest("unused")

        with pytest.raises(SceneError, match="not confirmed"):
            await dispatch_external_attempt(
                step, sample_call(step), checkpoint=checkpoint,
                invoke=invoke, apply_result=apply,
            )
        assert called == [] and step["status"] == "pending"
        assert "externalAttempt" not in step

    asyncio.run(scenario())


def test_cancelled_pre_dispatch_save_retains_the_fence_and_never_calls_provider():
    async def scenario():
        step = sample_step()
        entered = asyncio.Event()
        release = asyncio.Event()
        snapshots = []
        dispatched = []

        async def checkpoint():
            entered.set()
            await release.wait()
            snapshots.append(copy.deepcopy(step))
            return True

        async def invoke(request_id):
            dispatched.append(request_id)

        async def apply(_response):
            return digest("unused")

        task = asyncio.create_task(
            dispatch_external_attempt(
                step, sample_call(step), checkpoint=checkpoint,
                invoke=invoke, apply_result=apply,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert dispatched == []
        assert len(snapshots) >= 1
        assert snapshots[0]["externalAttempt"]["phase"] == "dispatched"
        assert step["status"] == "reconciliation-required"
        assert step["externalAttempt"]["phase"] == "reconciliation-required"
        restored, = restore_graph_runs([saved_run(snapshots[0])], {"n1": "n1", "n2": "n2"})
        assert restored["status"] == "reconciliation-required"
        assert restored["steps"][0]["externalAttempt"]["requestId"] == step["externalAttempt"]["requestId"]

    asyncio.run(scenario())


def test_timeout_is_not_a_retryable_failure_and_keeps_the_request_identity():
    async def scenario():
        step = sample_step()
        snapshots = []
        sent = []

        async def checkpoint():
            snapshots.append(copy.deepcopy(step))
            return True

        async def invoke(request_id):
            sent.append(request_id)
            raise TimeoutError("remote might have completed")

        async def apply(_response):
            return digest("unused")

        with pytest.raises(TimeoutError):
            await dispatch_external_attempt(
                step, sample_call(step), checkpoint=checkpoint,
                invoke=invoke, apply_result=apply,
            )
        assert len(sent) == 1 and len(snapshots) == 2
        assert snapshots[-1]["status"] == "reconciliation-required"
        assert snapshots[-1]["externalAttempt"]["requestId"] == sent[0]
        for snapshot in snapshots:
            restored, = restore_graph_runs([saved_run(snapshot)], {"n1": "n3", "n2": "n4"})
            assert restored["status"] == "reconciliation-required"
            assert restored["steps"][0]["status"] == "reconciliation-required"
            assert restored["steps"][0]["externalAttempt"]["requestId"] == sent[0]
            assert restored["steps"][0]["nodeId"] == "n4"
        with pytest.raises(SceneError, match="already attempted"):
            await dispatch_external_attempt(
                step, sample_call(step), checkpoint=checkpoint, invoke=invoke, apply_result=apply,
            )
        assert len(sent) == 1

    asyncio.run(scenario())


def test_cancellation_during_remote_call_marks_request_uncertain():
    async def scenario():
        step = sample_step()
        in_call = asyncio.Event()
        snapshots = []

        async def checkpoint():
            snapshots.append(copy.deepcopy(step))
            return True

        async def invoke(_request_id):
            in_call.set()
            await asyncio.Event().wait()

        async def apply(_response):
            return digest("unused")

        task = asyncio.create_task(
            dispatch_external_attempt(
                step, sample_call(step), checkpoint=checkpoint,
                invoke=invoke, apply_result=apply,
            )
        )
        await asyncio.wait_for(in_call.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(snapshots) == 2
        assert snapshots[-1]["status"] == "reconciliation-required"

    asyncio.run(scenario())


def test_failed_final_receipt_checkpoint_never_replays_the_remote_side_effect():
    async def scenario():
        step = sample_step()
        checkpoint_count = 0
        durable = []

        async def checkpoint():
            nonlocal checkpoint_count
            checkpoint_count += 1
            if checkpoint_count == 2:
                raise OSError("output + receipt did not commit")
            durable.append(copy.deepcopy(step))
            return True

        async def invoke(_request_id):
            return "remote result"

        async def apply(response):
            return digest(response)

        with pytest.raises(OSError, match="did not commit"):
            await dispatch_external_attempt(
                step, sample_call(step), checkpoint=checkpoint,
                invoke=invoke, apply_result=apply,
            )
        assert step["status"] == "reconciliation-required"
        assert durable[0]["externalAttempt"]["phase"] == "dispatched"
        restored, = restore_graph_runs([saved_run(durable[0])], {"n1": "n3", "n2": "n4"})
        assert restored["status"] == "reconciliation-required"

    asyncio.run(scenario())


def test_invalid_or_missing_external_identity_fails_closed_without_importing_secret_fields():
    step = sample_step()
    step["status"] = "running"
    attempt = sample_call(step).journal_record()
    raw = saved_run({**step, "externalAttempt": {**attempt, "requestId": "malformed", "apiKey": "secret"}})
    restored, = restore_graph_runs([raw], {"n1": "n1", "n2": "n2"})
    assert restored["status"] == "reconciliation-required"
    assert restored["steps"][0]["status"] == "reconciliation-required"
    assert "externalAttempt" not in restored["steps"][0]
    assert "secret" not in str(restored)
    mismatched = saved_run({**step, "externalAttempt": {**attempt, "inputHash": digest("other")}})
    restored, = restore_graph_runs([mismatched], {"n1": "n1", "n2": "n2"})
    assert restored["steps"][0]["status"] == "reconciliation-required"
    assert restored["steps"][0]["externalAttempt"]["requestId"] == attempt["requestId"]
    missing = saved_run(step)
    restored, = restore_graph_runs([missing], {"n1": "n1", "n2": "n2"})
    assert restored["status"] == "reconciliation-required"
    with pytest.raises(SceneError, match="explicit model"):
        ExternalCall(kind="model", provider_name="profile/openai", model_id="", input_ref="artifact/v1", input_hash=digest("x"))
    with pytest.raises(SceneError, match="non-secret"):
        ExternalCall(
            kind="tool", provider_name="key with spaces", tool="file.write",
            input_ref="artifact/v1", input_hash=digest("x"),
        )


def test_dispatched_external_request_survives_real_graph_database_roundtrip(tmp_path):
    async def scenario():
        path = tmp_path / "graphs.db"
        doc = SceneDocument()
        bus = SessionBus("external")
        bus.register_topic("scene", doc.scene_payload)
        chat_library.register_chat_library(bus, path, doc, autosave_interval_seconds=None)

        source, target = doc.add_note(0, 0), doc.add_note(200, 0)
        source.content = "Prompt for a model"
        doc.bind_note_text_input(source.id, target.id)
        step, = plan_graph_run(doc, [target.id])
        step["status"] = "running"
        call = ExternalCall(
            kind="model", provider_name="profile/openai", model_id="example-model",
            input_ref="artifact/immutable-version", input_hash=step["inputHash"],
        )
        step["externalAttempt"] = call.journal_record()
        doc.graph_runs.append(saved_run(step))
        await bus.dispatch_intent("app-chat-library", "saveChat", [])
        chat_id = doc.current_chat_id
        assert chat_id is not None
        row = chat_library.load_chat_row(path, chat_id)
        assert row["data"]["graph_runs"][0]["steps"][0]["externalAttempt"]["requestId"] == (
            step["externalAttempt"]["requestId"]
        )

        reopened = SceneDocument()
        bus2 = SessionBus("external-reopen")
        bus2.register_topic("scene", reopened.scene_payload)
        chat_library.register_chat_library(bus2, path, reopened, autosave_interval_seconds=None)
        await bus2.dispatch_intent("app-chat-library", "loadChat", [chat_id])
        run = await bus2.dispatch_intent("scene", "getGraphRun", [])
        assert run["status"] == "reconciliation-required"
        assert run["steps"][0]["status"] == "reconciliation-required"
        assert run["steps"][0]["externalAttempt"]["requestId"] == step["externalAttempt"]["requestId"]
        assert run["steps"][0]["nodeId"] in reopened.nodes
        retry = await bus2.dispatch_intent("scene", "resumeGraphRun", [run["runId"]])
        assert retry["status"] == "reconciliation-required"
        assert not reopened.command_log

    asyncio.run(scenario())



def test_uncertain_remote_step_does_not_block_an_independent_local_step(tmp_path):
    async def scenario():
        path = tmp_path / "graphs.db"
        doc = SceneDocument()
        bus = SessionBus("unrelated")
        bus.register_topic("scene", doc.scene_payload)
        chat_library.register_chat_library(bus, path, doc, autosave_interval_seconds=None)
        src1, dst1, src2, dst2 = [doc.add_note(i * 100, 0) for i in range(4)]
        src1.content = "Sent to provider"
        src2.content = "Independent local output"
        doc.bind_note_text_input(src1.id, dst1.id)
        doc.bind_note_text_input(src2.id, dst2.id)
        steps = plan_graph_run(doc, [])
        remote, local = (next(step for step in steps if step["nodeId"] == target)
                         for target in (dst1.id, dst2.id))
        remote["status"] = "reconciliation-required"
        remote["externalAttempt"] = ExternalCall(
            kind="model", provider_name="profile/openai", model_id="example-model",
            input_ref="artifact/version-1", input_hash=remote["inputHash"],
        ).journal_record()
        local["status"] = "pending"
        doc.graph_runs.append({
            **saved_run(remote), "steps": [remote, local],
            "status": "reconciliation-required",
        })
        await bus.dispatch_intent("app-chat-library", "saveChat", [])
        graph_id = doc.current_chat_id
        assert graph_id is not None

        fresh = SceneDocument()
        bus2 = SessionBus("unrelated-reopen")
        bus2.register_topic("scene", fresh.scene_payload)
        chat_library.register_chat_library(bus2, path, fresh, autosave_interval_seconds=None)
        await bus2.dispatch_intent("app-chat-library", "loadChat", [graph_id])
        before = await bus2.dispatch_intent("scene", "getGraphRun", [])
        assert before["status"] == "reconciliation-required"
        assert [step["status"] for step in before["steps"]] == ["reconciliation-required", "pending"]
        finished = await bus2.dispatch_intent("scene", "resumeGraphRun", ["remote-run-01"])
        assert finished["status"] == "reconciliation-required"
        assert [step["status"] for step in finished["steps"]] == ["reconciliation-required", "completed"]
        assert fresh.nodes[finished["steps"][1]["nodeId"]].content == src2.content
        assert fresh.nodes[finished["steps"][0]["nodeId"]].content == "Add note..."
        assert len(fresh.command_log) == 1
        # A second retry makes no more writes or remote calls.
        assert (await bus2.dispatch_intent("scene", "resumeGraphRun", ["remote-run-01"]))["status"] == (
            "reconciliation-required"
        )
        assert len(fresh.command_log) == 1

    asyncio.run(scenario())
