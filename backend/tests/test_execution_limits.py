"""Execution-limits topic tests (ADR-005 stage 5.4, disclosure half).

Mirrors backend/tests/test_about.py's own shape - the same "zero live
state, one payload function" topic pattern - plus platform-conditional
content tests, since the whole point of this module is that the disclosed
caps differ between Windows and POSIX (see graphlink_execution_guard.py's
own stage 5.2 scoping note on why Windows has no CPU-rate control).
"""

import graphlink_execution_guard as guard
from backend.events import SessionBus
from backend.execution_limits import execution_limits_payload, register_execution_limits


def test_execution_limits_payload_matches_generated_validator_shape():
    payload = execution_limits_payload()
    assert set(payload) == {
        "pycoderResourceLimitsText",
        "codeSandboxResourceLimitsText",
    }
    assert isinstance(payload["pycoderResourceLimitsText"], str)
    assert isinstance(payload["codeSandboxResourceLimitsText"], str)
    assert payload["pycoderResourceLimitsText"]
    assert payload["codeSandboxResourceLimitsText"]


def test_code_sandbox_text_is_the_pycoder_text_plus_the_dependency_install_note():
    payload = execution_limits_payload()
    assert payload["codeSandboxResourceLimitsText"].startswith(payload["pycoderResourceLimitsText"])
    assert "pre-built binary" in payload["codeSandboxResourceLimitsText"]
    assert "pre-built binary" not in payload["pycoderResourceLimitsText"]


def test_dependency_install_note_does_not_overclaim_given_the_stage_5_5_escalation():
    # ADR-005 stage 5.5 review-fix: an adversarial review found the note
    # used to say installs "will fail...rather than run its own build code"
    # with no qualifier - an unconditional claim the SAME approval dialog's
    # own source-build escalation checkbox directly contradicts (checking
    # it and approving does let a source distribution's build code run).
    # The note must describe this as the DEFAULT, not an absolute guarantee.
    payload = execution_limits_payload()
    text = payload["codeSandboxResourceLimitsText"]
    assert "pre-built binary distributions by default" in text
    assert "unless you explicitly allow" in text


def test_windows_text_mentions_memory_and_process_cap_but_no_cpu_limit(monkeypatch):
    from backend import execution_limits as module

    monkeypatch.setattr(module.sys, "platform", "win32")

    text = module._resource_limits_sentence()
    assert "2 GB" in text
    assert "64 concurrent processes" in text
    assert "no CPU time limit" in text
    assert "CPU time" not in text.split("no CPU time limit")[0]
    # Windows's active-process cap IS genuinely scoped to the job (the
    # process tree), unlike POSIX's RLIMIT_NPROC below - "concurrent
    # processes" is accurate here, so no reword needed on this branch.


def test_posix_text_mentions_all_four_caps(monkeypatch):
    from backend import execution_limits as module

    monkeypatch.setattr(module.sys, "platform", "linux")

    text = module._resource_limits_sentence()
    assert "2 GB of reserved memory" in text
    assert f"{guard.DEFAULT_CPU_SECONDS}s of CPU time" in text
    assert "1 GB of output per file" in text
    assert "no CPU time limit" not in text
    # Review-fix (ADR-005 stage 5.4): RLIMIT_NPROC bounds the real UID's
    # TOTAL live process count system-wide, not this execution's own
    # process tree - "64 concurrent processes" would misstate its scope,
    # so the POSIX sentence must NOT use that exact phrase.
    assert "concurrent processes" not in text
    assert "total live process count to 64" in text


def test_both_platform_sentences_include_the_fail_open_caveat(monkeypatch):
    # ADR-005 stage 5.4 review-fix: graphlink_execution_guard.py has
    # several silent fail-open paths (Job Object creation/assignment
    # failing, a per-rlimit setrlimit call being refused) that leave a
    # real run uncapped with nothing surfaced beyond a logger.warning -
    # the disclosure must not assert an unconditional guarantee.
    from backend import execution_limits as module

    monkeypatch.setattr(module.sys, "platform", "win32")
    windows_text = module._resource_limits_sentence()
    monkeypatch.setattr(module.sys, "platform", "linux")
    posix_text = module._resource_limits_sentence()

    for text in (windows_text, posix_text):
        assert "may not take effect" in text


def test_format_bytes_renders_whole_gib_without_a_decimal():
    from backend.execution_limits import _format_bytes

    assert _format_bytes(2 * 1024**3) == "2 GB"
    assert _format_bytes(1 * 1024**3) == "1 GB"


def test_format_bytes_renders_fractional_gib_with_one_decimal():
    from backend.execution_limits import _format_bytes

    assert _format_bytes(int(1.5 * 1024**3)) == "1.5 GB"


def test_format_bytes_renders_sub_gib_values_in_mb():
    from backend.execution_limits import _format_bytes

    assert _format_bytes(512 * 1024**2) == "512 MB"


def test_register_execution_limits_publishes_on_the_execution_limits_topic():
    import asyncio

    bus = SessionBus("execution-limits-test")
    register_execution_limits(bus)

    class Recorder:
        def __init__(self):
            self.messages = []

        async def send_json(self, data):
            self.messages.append(data)

    recorder = Recorder()
    bus.attach(recorder)
    asyncio.run(bus.publish("execution-limits"))
    assert recorder.messages[0]["topic"] == "execution-limits"
    assert recorder.messages[0]["payload"]["pycoderResourceLimitsText"]
    assert recorder.messages[0]["payload"]["codeSandboxResourceLimitsText"]
