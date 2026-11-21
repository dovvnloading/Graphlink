"""User-directory workspaces: the trust gate is the whole security story,
so the tests concentrate there - a trusted dir binds, an untrusted request
degrades to scratch, confinement holds against the user dir, and the
transcript never lands inside it."""

from __future__ import annotations

import asyncio

import pytest

from backend.harness import workspace as workspace_module
from backend.harness.workspace import bound_root, ensure_workspace, resolve_under_root
from backend.harness.tools_fs import register_harness_fs_tools
from backend.providers.base import ToolCall
from backend.tools import FS_READ, FS_WRITE, RunContext, ToolRegistry


@pytest.fixture
def workspace_root(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_module, "HARNESS_WORKSPACE_ROOT", tmp_path / "scratch")
    return tmp_path


class FakeSettings:
    def __init__(self, trusted):
        self._trusted = list(trusted)

    def get_harness_trusted_dirs(self):
        return list(self._trusted)


def test_a_trusted_existing_dir_binds_as_a_user_dir(workspace_root, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    settings = FakeSettings([str(project.resolve())])
    root, is_user_dir = bound_root("ws1", str(project), settings_manager=settings)
    assert is_user_dir and root == project.resolve()


def test_replacing_a_granted_folder_with_a_symlink_does_not_transfer_trust(workspace_root, tmp_path):
    project = tmp_path / "project"
    private = tmp_path / "private"
    project.mkdir()
    private.mkdir()
    (private / "secret.md").write_text("not approved")
    settings = FakeSettings([str(project.resolve())])

    project.rename(tmp_path / "project-old")
    try:
        project.symlink_to(private, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Creating directory symlinks is unavailable")

    root, is_user_dir = bound_root("ws1", str(project), settings_manager=settings)
    assert not is_user_dir and root == ensure_workspace("ws1")
    assert root != private.resolve()
    # Only a fresh, explicit grant of the canonical private path works.
    settings._trusted.append(str(private.resolve()))
    assert bound_root("ws1", str(project), settings_manager=settings) == (private.resolve(), True)


def test_an_untrusted_dir_silently_falls_back_to_scratch(workspace_root, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    # The path is a real dir but NOT in the trust list: a session file's
    # request must never be honored on trust it does not carry.
    root, is_user_dir = bound_root("ws1", str(project), settings_manager=FakeSettings([]))
    assert not is_user_dir
    assert root == ensure_workspace("ws1")


def test_a_trusted_but_now_missing_dir_falls_back_to_scratch(workspace_root, tmp_path):
    gone = tmp_path / "gone"
    settings = FakeSettings([str((tmp_path / "gone").resolve())])
    root, is_user_dir = bound_root("ws1", str(gone), settings_manager=settings)
    assert not is_user_dir and root == ensure_workspace("ws1")


def test_no_path_or_no_settings_is_scratch(workspace_root):
    assert bound_root("ws1", "", settings_manager=FakeSettings(["/anything"]))[1] is False
    assert bound_root("ws1", "/some/dir", settings_manager=None)[1] is False


def test_confinement_and_write_hold_against_a_user_dir(workspace_root, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "readme.txt").write_text("hello from the project", encoding="utf-8")
    registry = ToolRegistry()
    register_harness_fs_tools(registry)

    async def approve(_call):
        return True

    # A run bound to the user dir carries it as the resolved root.
    ctx = RunContext(granted_scopes=frozenset({FS_READ, FS_WRITE}), request_approval=approve)
    ctx.harness_workspace_dir = project.resolve()

    async def go():
        read = await registry.invoke(ToolCall(id="1", name="fs.read", arguments={"path": "readme.txt"}), ctx)
        wrote = await registry.invoke(
            ToolCall(id="2", name="fs.write", arguments={"path": "out.txt", "content": "written"}), ctx,
        )
        escape = await registry.invoke(
            ToolCall(id="3", name="fs.read", arguments={"path": "../secret.txt"}), ctx,
        )
        return read, wrote, escape

    read, wrote, escape = asyncio.run(go())
    assert not read.is_error and "hello from the project" in read.content
    assert not wrote.is_error and (project / "out.txt").read_text(encoding="utf-8") == "written"
    assert escape.is_error and "outside" in escape.content


def test_scratch_resolve_helper_still_confines(workspace_root):
    ensure_workspace("ws1")
    with pytest.raises(workspace_module.WorkspaceError):
        resolve_under_root(workspace_module.workspace_dir("ws1"), "../elsewhere")



def test_bound_task_refuses_root_symlink_swap_for_file_tools_and_container(
    workspace_root, tmp_path, monkeypatch,
):
    """#863: an existing run cannot inherit a different folder's grant."""
    from backend.harness.shell_isolation import container_invocation

    project, private = tmp_path / "project", tmp_path / "private"
    project.mkdir()
    private.mkdir()
    (private / "secret.md").write_text("unapproved")
    root, user_dir = bound_root(
        "ws1", str(project), settings_manager=FakeSettings([str(project.resolve())]),
    )
    assert user_dir and root == project.resolve()

    project.rename(tmp_path / "project-old")
    try:
        project.symlink_to(private, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Creating directory symlinks is unavailable")

    with pytest.raises(workspace_module.WorkspaceError, match="root changed"):
        resolve_under_root(root, "secret.md")

    registry = ToolRegistry()
    register_harness_fs_tools(registry)

    async def approve(_call):
        return True

    ctx = RunContext(granted_scopes=frozenset({FS_READ}), request_approval=approve)
    ctx.harness_workspace_dir = root
    result = asyncio.run(registry.invoke(
        ToolCall(id="read", name="fs.read", arguments={"path": "secret.md"}), ctx,
    ))
    assert result.is_error and "root changed" in result.content
    assert "unapproved" not in result.content

    monkeypatch.setattr(
        "backend.harness.shell_isolation.shutil.which",
        lambda name, *, path: "/usr/bin/docker" if name == "docker" else None,
    )
    with pytest.raises(workspace_module.WorkspaceError, match="root changed"):
        container_invocation("cat secret.md", root, env={"PATH": "/usr/bin"})


def test_root_swap_during_approval_cannot_redirect_a_write(workspace_root, tmp_path):
    """The binding must be checked after an awaited human approval, too."""
    project, private = tmp_path / "project", tmp_path / "private"
    project.mkdir()
    private.mkdir()
    root, user_dir = bound_root(
        "ws1", str(project), settings_manager=FakeSettings([str(project.resolve())]),
    )
    assert user_dir

    async def approve(_call):
        project.rename(tmp_path / "project-old")
        try:
            project.symlink_to(private, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("Creating directory symlinks is unavailable")
        return True

    registry = ToolRegistry()
    register_harness_fs_tools(registry)
    ctx = RunContext(granted_scopes=frozenset({FS_WRITE}), request_approval=approve)
    ctx.harness_workspace_dir = root
    result = asyncio.run(registry.invoke(
        ToolCall(id="write", name="fs.write", arguments={"path": "pwned.txt", "content": "leak"}),
        ctx,
    ))
    assert result.is_error and "root changed" in result.content
    assert not (private / "pwned.txt").exists()




# -- the launcher's own workspace choice ------------------------------------
#
# Binding a folder only AFTER the node existed meant the first run of every
# real piece of work went to scratch: you had to let it finish, rebind, and
# re-send the same task. These cover the launch-time path that removes that,
# and the fact that it buys no trust the per-node path did not already.


class _FakeBus:
    """Enough SessionBus for the two intents under test."""

    def __init__(self):
        self.intents = {}
        self.published = []

    def register_intent(self, topic, name, fn):
        self.intents[(topic, name)] = fn

    def has_topic(self, topic):
        # The real session has Settings registered alongside Harness.
        return topic == "app-settings"

    async def publish(self, topic):
        self.published.append(topic)

    async def dispatch(self, topic, name, args):
        return await self.intents[(topic, name)](*args)


class _RecordingSettings(FakeSettings):
    def __init__(self, trusted=()):
        super().__init__(trusted)
        self.granted = []

    def add_harness_trusted_dir(self, path):
        self.granted.append(path)
        self._trusted.append(path)


def _harness_bus(monkeypatch, settings, picked=None, composer_route=None, busy=False):
    from backend import native_dialogs
    from backend.api.intents_harness import register_harness_intents
    from backend.domain.graph import SceneDocument
    from backend.notifications import NotificationState

    async def fake_pick_folder(directory=None):
        return picked

    monkeypatch.setattr(native_dialogs, "pick_folder", fake_pick_folder)
    bus, document = _FakeBus(), SceneDocument()
    bus.starts = []
    dispatcher = type("D", (), {"_settings_manager": settings, "start_harness_run": None})()
    bus.dispatcher = dispatcher
    if busy:
        dispatcher._runs = type("Runs", (), {"is_busy": lambda self, kind: kind == "harness"})()

    async def start_harness_run(**kwargs):
        bus.starts.append(kwargs)
        dispatcher.started = kwargs
        bus.started = kwargs
        # Snapshot what the dispatcher sees at claim, not a later node state.
        bus.claimed_profile = kwargs["document"].nodes[kwargs["harness_node_id"]].state.harness_execution_profile
        return "run-1"

    dispatcher.start_harness_run = start_harness_run
    composer = type("Composer", (), {"route": lambda self: composer_route})() if composer_route else None
    register_harness_intents(bus, document, NotificationState(), dispatcher, composer)
    return bus, document


def test_lost_harness_send_ack_replays_claimed_run_without_execution(workspace_root, monkeypatch):
    """Drop the transport reply AFTER the fake dispatcher accepted the run."""
    bus, document = _harness_bus(monkeypatch, _RecordingSettings())
    node = document.add_harness_node(0, 0, "first task")
    operation_id = "card-followup-0001"

    async def lose_reply():
        accepted = await bus.dispatch("harness", "send", [
            node.id, "create the file", True, operation_id,
        ])
        assert accepted == "run-1"
        raise ConnectionError("websocket reply lost after backend claim")

    with pytest.raises(ConnectionError, match="reply lost"):
        asyncio.run(lose_reply())
    assert len(bus.starts) == 1

    # The run may have completed before the card gets connectivity back.
    node.state.harness_run_id = "run-1"
    node.state.harness_status = "done"
    node.pending_request_id = None
    retried = asyncio.run(bus.dispatch("harness", "send", [
        node.id, "create the file", True, operation_id,
    ]))
    assert retried == "run-1"
    assert len(bus.starts) == 1, "no second backend invocation even after completion"

    # Reusing a key for different input cannot smuggle another task past
    # the receipt. A different, new key is a legitimate new follow-up.
    assert asyncio.run(bus.dispatch("harness", "send", [
        node.id, "delete the file", True, operation_id,
    ])) is None
    assert len(bus.starts) == 1
    assert asyncio.run(bus.dispatch("harness", "send", [
        node.id, "new task", True, "card-followup-0002",
    ])) == "run-1"
    assert len(bus.starts) == 2


def test_rejected_harness_send_does_not_poison_operation_id(workspace_root, monkeypatch):
    bus, document = _harness_bus(monkeypatch, _RecordingSettings())
    node = document.add_harness_node(0, 0, "first task")
    initial = bus.dispatcher.start_harness_run

    async def refuse(**kwargs):
        return None

    bus.dispatcher.start_harness_run = refuse
    args = [node.id, "start when idle", True, "card-followup-0003"]
    assert asyncio.run(bus.dispatch("harness", "send", args)) is None
    bus.dispatcher.start_harness_run = initial
    assert asyncio.run(bus.dispatch("harness", "send", args)) == "run-1"
    assert len(bus.starts) == 1


def test_the_launcher_binds_its_folder_before_the_first_run(workspace_root, tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    settings = _RecordingSettings([str(project.resolve())])
    bus, document = _harness_bus(monkeypatch, settings)

    node_id = asyncio.run(bus.dispatch("harness", "start", ["do the thing", 8, str(project)]))

    node = document.nodes[node_id]
    assert node.state.harness_workspace_path == str(project)
    # Bound BEFORE the run: the first task already works in the right place.
    root, is_user_dir = bound_root(
        node.state.harness_workspace_id, node.state.harness_workspace_path,
        settings_manager=settings,
    )
    assert is_user_dir and root == project.resolve()


# Regression #765: redo must restore exactly the profile and folder selected
# when the agent was created, not HarnessState's initial host/scratch defaults.
def test_isolated_launcher_undo_redo_preserves_profile_and_workspace(
    workspace_root, tmp_path, monkeypatch,
):
    from backend.api import intents_harness

    monkeypatch.setattr(intents_harness, "available_engine", lambda: "docker")
    project = tmp_path / "project"
    project.mkdir()
    bus, document = _harness_bus(monkeypatch, _RecordingSettings([str(project.resolve())]))

    node_id = asyncio.run(bus.dispatch(
        "harness", "start", ["inspect files", 8, str(project), "isolated"],
    ))

    assert bus.claimed_profile == "isolated"
    (creation,) = document.command_log
    assert creation.command_type == "harnessCreate"
    snapshot = creation.node_after[node_id]
    assert snapshot.state.harness_execution_profile == "isolated"
    assert snapshot.state.harness_workspace_path == str(project)

    document.undo()
    assert node_id not in document.nodes
    document.redo()
    restored = document.nodes[node_id]
    assert restored.state.harness_execution_profile == "isolated"
    assert restored.state.harness_workspace_path == str(project)


def test_a_launcher_path_is_a_request_not_a_grant(workspace_root, tmp_path, monkeypatch):
    """The wire can name any folder; naming it must not make it trusted.
    The run-time gate is what settles it, exactly as for a session file."""
    project = tmp_path / "not-granted"
    project.mkdir()
    bus, document = _harness_bus(monkeypatch, _RecordingSettings([]))

    node_id = asyncio.run(bus.dispatch("harness", "start", ["do the thing", 8, str(project)]))

    node = document.nodes[node_id]
    root, is_user_dir = bound_root(
        node.state.harness_workspace_id, node.state.harness_workspace_path,
        settings_manager=_RecordingSettings([]),
    )
    assert not is_user_dir and root == ensure_workspace(node.state.harness_workspace_id)


def test_picking_a_launch_workspace_grants_it_and_answers_with_the_path(
    workspace_root, tmp_path, monkeypatch,
):
    project = tmp_path / "project"
    project.mkdir()
    settings = _RecordingSettings([])
    bus, _document = _harness_bus(monkeypatch, settings, picked=str(project))

    answer = asyncio.run(bus.dispatch("harness", "pickLaunchWorkspace", []))

    assert answer == str(project.resolve())
    assert settings.granted == [str(project.resolve())], "the pick IS the grant"
    assert "app-settings" in bus.published, "Settings must refresh after a folder grant"


def test_a_cancelled_launch_picker_grants_nothing(workspace_root, monkeypatch):
    settings = _RecordingSettings([])
    bus, _document = _harness_bus(monkeypatch, settings, picked=None)

    assert asyncio.run(bus.dispatch("harness", "pickLaunchWorkspace", [])) is None
    assert settings.granted == []


def test_revoked_folder_falls_back_to_scratch_on_the_next_run(workspace_root, tmp_path):
    from graphlink_settings_store import SettingsManager

    project = tmp_path / "project"
    project.mkdir()
    settings = SettingsManager(tmp_path / "settings.dat")
    settings.add_harness_trusted_dir(str(project.resolve()))

    assert bound_root("ws1", str(project), settings_manager=settings) == (project.resolve(), True)

    settings.revoke_harness_trusted_dir(str(project.resolve()))
    root, is_user_dir = bound_root("ws1", str(project), settings_manager=settings)
    assert not is_user_dir and root == ensure_workspace("ws1")

# Composer agent mode: the backend, not the browser, resolves the model in
# this session. A selected harness is resumed; a missing selection creates a
# new scratch agent. Neither path changes the model used by normal launches.

@pytest.mark.parametrize(
    "mode,provider,model_id,expected_provider",
    [
        ("ollama", "Ollama (Local)", "qwen3:8b", "Ollama"),
        ("llama_cpp", "Llama.cpp (Local)", "/models/code.gguf", "Llama.cpp"),
        ("api", "Anthropic Claude", "claude-sonnet", "Anthropic Claude"),
    ],
)
def test_composer_send_to_selected_agent_pins_composer_model(
    monkeypatch, mode, provider, model_id, expected_provider,
):
    from graphlink_model_catalog import ModelRef

    route = {"mode": mode, "provider": provider, "modelId": model_id, "available": True}
    bus, document = _harness_bus(monkeypatch, FakeSettings([]), composer_route=route)
    agent = document.add_harness_node(0, 0, "original task")

    result = asyncio.run(bus.dispatch("harness", "composerSend", ["follow up", agent.id]))

    assert result == agent.id
    assert len(document.nodes) == 1
    assert bus.started["harness_node_id"] == agent.id
    assert bus.started["user_text"] == "follow up"
    assert bus.started["model_ref"] == ModelRef(expected_provider, model_id)


def test_composer_send_without_selected_agent_creates_scratch_node(monkeypatch):
    from graphlink_model_catalog import ModelRef

    route = {"mode": "ollama", "provider": "Ollama (Local)", "modelId": "local-chat", "available": True}
    bus, document = _harness_bus(monkeypatch, FakeSettings([]), composer_route=route)

    node_id = asyncio.run(bus.dispatch("harness", "composerSend", ["build a report", "deleted-node"]))

    assert node_id in document.nodes
    assert document.nodes[node_id].state.harness_goal == "build a report"
    assert document.nodes[node_id].state.harness_workspace_path == ""
    assert bus.started["harness_node_id"] == node_id
    assert bus.started["model_ref"] == ModelRef("Ollama", "local-chat")


@pytest.mark.parametrize("route", [
    {"mode": "ollama", "provider": "Ollama (Local)", "modelId": "", "available": False},
    {"mode": "api", "provider": "Unconfigured", "modelId": "model", "available": True},
])
def test_composer_send_rejects_unavailable_route_before_mutation(monkeypatch, route):
    bus, document = _harness_bus(monkeypatch, FakeSettings([]), composer_route=route)

    assert asyncio.run(bus.dispatch("harness", "composerSend", ["do work", None])) is None
    assert not document.nodes
    assert not hasattr(bus, "started")
    assert "notification" in bus.published


def test_composer_send_refuses_busy_run_without_creating_an_agent(monkeypatch):
    route = {"mode": "ollama", "provider": "Ollama (Local)", "modelId": "local-chat", "available": True}
    bus, document = _harness_bus(monkeypatch, FakeSettings([]), composer_route=route, busy=True)

    assert asyncio.run(bus.dispatch("harness", "composerSend", ["do work", None])) is None
    assert not document.nodes
    assert not hasattr(bus, "started")


# Issue #737: no entrypoint may accept an incomplete task and report success.

@pytest.mark.parametrize("route_kind", ["launch", "send", "composer-new", "composer-selected"])
def test_oversized_agent_task_is_rejected_before_dispatch(monkeypatch, route_kind):
    route = {"mode": "ollama", "provider": "Ollama (Local)", "modelId": "local", "available": True}
    bus, document = _harness_bus(monkeypatch, FakeSettings([]), composer_route=route)
    agent = document.add_harness_node(0, 0, "existing") if route_kind in {"send", "composer-selected"} else None
    original_nodes = set(document.nodes)
    text = "x" * 20_000 + "FINAL_INSTRUCTION_MUST_APPEAR"

    if route_kind == "launch":
        args = ("start", [text])
    elif route_kind == "send":
        args = ("send", [agent.id, text])
    else:
        args = ("composerSend", [text, agent.id if agent else None])

    assert asyncio.run(bus.dispatch("harness", *args)) is None
    assert set(document.nodes) == original_nodes
    assert not hasattr(bus, "started"), "no truncated task should reach the dispatcher"
    assert "notification" in bus.published


@pytest.mark.parametrize("route_kind", ["launch", "send", "composer-new", "composer-selected"])
def test_agent_task_at_message_cap_is_dispatched_in_full(monkeypatch, route_kind):
    route = {"mode": "ollama", "provider": "Ollama (Local)", "modelId": "local", "available": True}
    bus, document = _harness_bus(monkeypatch, FakeSettings([]), composer_route=route)
    agent = document.add_harness_node(0, 0, "existing") if route_kind in {"send", "composer-selected"} else None
    text = "x" * (20_000 - len("FINAL")) + "FINAL"

    if route_kind == "launch":
        node_id = asyncio.run(bus.dispatch("harness", "start", [text]))
    elif route_kind == "send":
        asyncio.run(bus.dispatch("harness", "send", [agent.id, text]))
        node_id = agent.id
    else:
        node_id = asyncio.run(bus.dispatch("harness", "composerSend", [text, agent.id if agent else None]))

    assert bus.started["user_text"] == text
    assert bus.started["harness_node_id"] == node_id


# Regression #768: the wire cap counts Python Unicode code points, including
# non-BMP characters; frontend editors must accept this exact same boundary.
def test_non_bmp_task_at_message_cap_reaches_dispatcher(monkeypatch):
    bus, document = _harness_bus(monkeypatch, FakeSettings([]))
    text = "😀" * 20_000

    node_id = asyncio.run(bus.dispatch("harness", "start", [text]))

    assert node_id in document.nodes
    assert bus.started["user_text"] == text


# Issue #738: a first task must see Isolated before the dispatcher claims it.

@pytest.mark.parametrize("via_composer", [False, True])
def test_isolated_profile_is_bound_before_first_task_claim(monkeypatch, via_composer):
    from backend.api import intents_harness

    monkeypatch.setattr(intents_harness, "available_engine", lambda: "podman")
    route = {"mode": "ollama", "provider": "Ollama (Local)", "modelId": "local", "available": True}
    bus, document = _harness_bus(monkeypatch, FakeSettings([]), composer_route=route)
    intent = "composerSend" if via_composer else "start"
    args = ["work", None, "isolated"] if via_composer else ["work", 8, "", "isolated"]

    node_id = asyncio.run(bus.dispatch("harness", intent, args))

    assert node_id in document.nodes
    assert document.nodes[node_id].state.harness_execution_profile == "isolated"
    assert bus.claimed_profile == "isolated"
    assert bus.started["harness_node_id"] == node_id


@pytest.mark.parametrize("profile,engine", [("isolated", None), ("sandbox", "docker")])
def test_first_task_rejects_unavailable_or_invalid_profile_before_node_creation(
    monkeypatch, profile, engine,
):
    from backend.api import intents_harness

    monkeypatch.setattr(intents_harness, "available_engine", lambda: engine)
    bus, document = _harness_bus(monkeypatch, FakeSettings([]))

    assert asyncio.run(bus.dispatch("harness", "start", ["work", 16, "", profile])) is None
    assert not document.nodes
    assert not hasattr(bus, "started")
    assert "notification" in bus.published


def test_selected_composer_agent_preserves_its_existing_profile(monkeypatch):
    from backend.api import intents_harness

    monkeypatch.setattr(intents_harness, "available_engine", lambda: None)
    route = {"mode": "ollama", "provider": "Ollama (Local)", "modelId": "local", "available": True}
    bus, document = _harness_bus(monkeypatch, FakeSettings([]), composer_route=route)
    agent = document.add_harness_node(0, 0, "existing")

    assert asyncio.run(bus.dispatch("harness", "composerSend", ["follow up", agent.id, "isolated"])) == agent.id
    assert bus.claimed_profile == "host", "a launch choice cannot override an existing agent"

@pytest.mark.parametrize("intent", ("start", "composerSend"))
def test_concurrent_new_agent_starts_do_not_leave_orphan_nodes_or_undo_entries(intent):
    """The second caller races while the winning start's scene publish is blocked."""
    import threading

    from backend.api.intents_harness import register_harness_intents
    from backend.domain.graph import SceneDocument
    from backend.notifications import NotificationState
    from backend.run_lifecycle import RunRegistry

    route = {"mode": "ollama", "modelId": "local-chat", "available": True}
    composer = type("Composer", (), {"route": lambda self: route})()

    class Dispatcher:
        def __init__(self):
            self._runs = RunRegistry()

        async def start_harness_run(self, **kwargs):
            if self._runs.is_busy("harness"):
                return None
            handle = self._runs.claim(
                "harness", node_id=kwargs["harness_node_id"], cancel_event=threading.Event(),
            )
            return handle.request_id

    async def run():
        bus, document = _FakeBus(), SceneDocument()
        register_harness_intents(bus, document, NotificationState(), Dispatcher(), composer)
        original_publish = bus.publish
        publishing = asyncio.Event()
        release_publish = asyncio.Event()

        async def pause_scene_publish(topic):
            if topic == "scene":
                publishing.set()
                await release_publish.wait()
            await original_publish(topic)

        bus.publish = pause_scene_publish
        args = ["do work", None, None] if intent == "start" else ["do work", None]
        first = asyncio.create_task(bus.dispatch("harness", intent, args))
        await asyncio.wait_for(publishing.wait(), timeout=2)
        second = asyncio.create_task(bus.dispatch("harness", intent, args))
        await asyncio.sleep(0)
        assert len(document.nodes) == 1
        assert len(document.command_log) == 1
        release_publish.set()
        accepted, rejected = await asyncio.gather(first, second)
        assert accepted in document.nodes
        assert rejected is None
        assert len(document.nodes) == 1
        assert len(document.command_log) == 1
        (creation,) = document.command_log
        assert creation.command_type == "harnessCreate"
        assert creation.run_id is None, "Undo run must never delete the Agent itself"
        assert accepted in creation.node_after

    asyncio.run(run())


def test_undo_run_keeps_agent_card_and_workspace_files(workspace_root, tmp_path, monkeypatch):
    """Only tool-made canvas edits belong to a run, not its launcher or disk."""
    from backend.api.intents_undo import register_undo_intents
    from backend.notifications import NotificationState

    bus, document = _harness_bus(monkeypatch, _RecordingSettings())
    agent_id = asyncio.run(bus.dispatch("harness", "start", ["Create a report"]))
    assert document.command_log[0].command_type == "harnessCreate"
    assert document.command_log[0].run_id is None

    report = tmp_path / "report.txt"
    report.write_text("workspace result", encoding="utf-8")
    note, _ = document.record_command(
        "addNote", "agent:run-1", lambda: document.add_note(700, 140), run_id="run-1",
    )
    notifications = NotificationState()
    register_undo_intents(bus, document, notifications)

    assert asyncio.run(bus.dispatch("scene", "undoRun", ["run-1"])) == 1
    assert agent_id in document.nodes
    assert note.id not in document.nodes
    assert report.read_text(encoding="utf-8") == "workspace result"
    assert "Workspace files are not reverted" in notifications.message
    assert "notification" in bus.published
    assert asyncio.run(bus.dispatch("scene", "undoRun", ["run-1"])) == 0
    assert "Workspace files are not reverted" in notifications.message
    assert agent_id in document.nodes
    # Ctrl+Z still independently undoes the original user-created Agent.
    assert document.undo().command_type == "harnessCreate"
    assert agent_id not in document.nodes


def test_undo_run_does_not_remove_legacy_run_stamped_agent_creation():
    """Guard old in-memory command histories created before the fix."""
    from backend.domain.graph import SceneDocument

    document = SceneDocument()
    agent, _ = document.record_command(
        "harnessCreate", "agent", lambda: document.add_harness_node(120, 120, "Task"), run_id="old-run",
    )
    note, _ = document.record_command(
        "addNote", "agent:old-run", lambda: document.add_note(680, 140), run_id="old-run",
    )
    assert document.undo_run("old-run") == 1
    assert note.id not in document.nodes
    assert agent.id in document.nodes
    assert document.undo_run("old-run") == 0
    assert agent.id in document.nodes
