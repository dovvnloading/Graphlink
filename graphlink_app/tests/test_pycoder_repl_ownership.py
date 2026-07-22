"""Phase 7 prerequisite increment 4: PyCoderNode's PythonREPL subprocess moves
from being owned/constructed directly by the node to being owned by a new
PyCoderReplManager, held on ChatWindow (self.pycoder_repl_manager).

The reason this needed its own manager rather than just staying a plain
per-node attribute: reading graphlink_scene.py's ChatScene.clear() (the "New
Chat" / chat-switch path, also used by graphlink_session/deserializers.py's
restore_chat()) directly shows it NEVER calls PyCoderNode.dispose() -
_teardown_items_before_clear() only stops hover-animation timers for
pycoder_nodes, nothing else. Before this increment, only Python's own
__del__ (fired once the node's last reference was dropped and GC ran) ever
stopped the REPL subprocess on that path; dispose() (the individual
right-click-delete path) was the only *deterministic* stop.

PyCoderReplManager preserves both guarantees without an explicit
__del__: a weakref.finalize registered at REPL-creation time stops the
subprocess when the node is garbage collected regardless of whether
stop()/dispose() was ever called, and dispose() still calls manager.stop()
directly for immediate, deterministic cleanup on the right-click-delete path.

Follow-up bug-scan finding: ChatScene._teardown_items_before_clear() now ALSO
calls dispose() on pycoder_nodes (and every other worker-owning node list),
exactly like it already did for chart_nodes - clear() is no longer a
dispose()-free path. This was found while fixing the identical gap for
ArtifactNode/ConversationNode/CodeSandboxNode/GitlinkNode: none of them had
dispose() invoked on the clear() path either, and for the four whose
worker-thread wiring in graphlink_window_actions.py uses a lambda closing
over the node/thread on a custom Signal, the node was not just left running
a beat longer - it was made permanently uncollectable by Python's GC (the
finalizer/​__del__ backstop those nodes rely on never even fires), because
disconnecting those signals only happens inside dispose(). The
weakref.finalize here still fires as a backstop for any path that somehow
bypasses both dispose() call sites, but it is no longer the only thing
protecting the clear() path.
"""

import gc
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

from graphlink_agents_pycoder import PyCoderReplManager, PythonREPL
from graphlink_pycoder import PyCoderNode
from graphlink_scene import ChatScene


class _StandinNode:
    """A minimal hashable object - the manager only needs identity-based keying,
    so most cases below don't need a real (expensive-to-construct) PyCoderNode."""


class TestGetReplCachesPerNode:
    def test_same_node_reuses_the_same_repl(self):
        manager = PyCoderReplManager()
        node = _StandinNode()

        assert manager.get_repl(node) is manager.get_repl(node)

    def test_different_nodes_get_different_repls(self):
        manager = PyCoderReplManager()
        node_a, node_b = _StandinNode(), _StandinNode()

        assert manager.get_repl(node_a) is not manager.get_repl(node_b)

    def test_get_repl_returns_a_real_python_repl_instance(self):
        manager = PyCoderReplManager()

        assert isinstance(manager.get_repl(_StandinNode()), PythonREPL)


class TestStopReleasesAndAllowsRecreation:
    def test_stop_calls_repl_stop(self):
        manager = PyCoderReplManager()
        node = _StandinNode()
        repl = manager.get_repl(node)
        repl.stop = MagicMock()

        manager.stop(node)

        repl.stop.assert_called_once()

    def test_stop_is_a_no_op_for_a_node_with_no_repl(self):
        manager = PyCoderReplManager()

        manager.stop(_StandinNode())  # must not raise

    def test_stop_then_get_repl_creates_a_fresh_instance(self):
        manager = PyCoderReplManager()
        node = _StandinNode()
        first = manager.get_repl(node)
        manager.stop(node)

        assert manager.get_repl(node) is not first


class TestFinalizerFiresOnGarbageCollectionAlone:
    """The load-bearing guarantee described in the module docstring: a REPL must
    stop even if manager.stop()/node.dispose() is never called at all."""

    def test_finalizer_stops_the_repl_once_the_node_is_collected(self):
        with patch.object(PythonREPL, "stop") as mock_stop:
            manager = PyCoderReplManager()
            node = _StandinNode()
            manager.get_repl(node)

            del node
            gc.collect()

            mock_stop.assert_called_once()

    def test_an_explicit_stop_detaches_the_finalizer_so_it_does_not_fire_twice(self):
        with patch.object(PythonREPL, "stop") as mock_stop:
            manager = PyCoderReplManager()
            node = _StandinNode()
            manager.get_repl(node)

            manager.stop(node)
            del node
            gc.collect()

            mock_stop.assert_called_once()


class TestRealPyCoderNodeIntegration:
    def test_manager_accepts_a_real_pycoder_node_as_its_key(self):
        # Confirms PyCoderNode has no custom __eq__/__hash__ that would break
        # WeakKeyDictionary keying (verified directly: grep found neither defined).
        manager = PyCoderReplManager()
        node = PyCoderNode(parent_node=None)

        repl = manager.get_repl(node)

        assert isinstance(repl, PythonREPL)
        assert manager.get_repl(node) is repl

    def test_pycoder_node_init_no_longer_constructs_its_own_repl(self):
        node = PyCoderNode(parent_node=None)

        assert not hasattr(node, "repl")

    def test_pycoder_node_del_method_does_not_touch_the_repl(self):
        # At increment 4, __del__'s only content was self.repl.stop() - dead
        # once REPL ownership moved to the manager, so it was deleted rather
        # than left as a no-op. A bug-scan follow-up later reintroduced
        # __del__ for a DIFFERENT reason (a GC-time backstop for
        # worker_thread, mirroring CodeSandboxNode/ArtifactNode) - it must
        # stay REPL-agnostic; the manager's own weakref.finalize is still the
        # only thing that stops the REPL subprocess at GC time.
        assert not hasattr(PyCoderNode, "repl")
        node = PyCoderNode(parent_node=None)
        assert not hasattr(node, "repl")
        node.__del__()  # must not raise - no self.repl to call .stop() on

    def test_dispose_calls_through_to_the_real_manager_via_scene_window(self):
        window = MagicMock()
        window.pycoder_repl_manager = PyCoderReplManager()
        scene = ChatScene(window=window)
        node = PyCoderNode(parent_node=None)
        scene.addItem(node)
        scene.pycoder_nodes.append(node)
        repl = window.pycoder_repl_manager.get_repl(node)
        repl.stop = MagicMock()

        node.dispose()

        repl.stop.assert_called_once()

    def test_dispose_does_not_raise_for_a_node_never_added_to_a_scene(self):
        node = PyCoderNode(parent_node=None)

        node.dispose()  # self.scene() is None here - must not raise

    def test_dispose_is_a_no_op_on_the_manager_when_no_repl_was_ever_created(self):
        window = MagicMock()
        window.pycoder_repl_manager = PyCoderReplManager()
        scene = ChatScene(window=window)
        node = PyCoderNode(parent_node=None)
        scene.addItem(node)
        scene.pycoder_nodes.append(node)

        node.dispose()  # must not raise even though get_repl() was never called


class TestClearPathNowCallsDisposeDeterministically:
    """Bug-scan follow-up: ChatScene.clear() now calls dispose() on
    pycoder_nodes (matching the pre-existing chart_nodes treatment), so the
    REPL stops synchronously when New Chat happens - it no longer depends on
    Python's GC ever running the finalizer, non-deterministic timing that
    could leave a REPL subprocess (and, for the sibling node types, the whole
    node) alive far longer than the user's screen suggested."""

    def test_scene_clear_calls_dispose_and_stops_the_repl_synchronously(self):
        with patch.object(PythonREPL, "stop") as mock_stop:
            window = MagicMock()
            window.pycoder_repl_manager = PyCoderReplManager()
            scene = ChatScene(window=window)
            parent = scene.add_chat_node("parent", is_user=True)
            node = PyCoderNode(parent_node=parent)
            scene.addItem(node)
            scene.pycoder_nodes.append(node)
            window.pycoder_repl_manager.get_repl(node)

            scene.clear()

            assert node.is_disposed is True
            mock_stop.assert_called_once()  # synchronous - no del/gc.collect() needed

    def test_the_finalizer_still_backstops_a_node_that_never_goes_through_clear_or_dispose(self):
        # Defense-in-depth check: the finalizer must still work standalone
        # (e.g. a node dropped without ever being added to a scene, as in a
        # headless test) - clear()-calling-dispose() is an additional
        # guarantee, not a replacement for this one.
        with patch.object(PythonREPL, "stop") as mock_stop:
            manager = PyCoderReplManager()
            node = PyCoderNode(parent_node=None)
            manager.get_repl(node)

            del node
            gc.collect()

            mock_stop.assert_called_once()


class TestWindowActionsUsesTheManager:
    def test_execute_pycoder_node_manual_mode_fetches_repl_through_the_manager(self):
        from graphlink_pycoder import PyCoderMode
        from graphlink_window_actions import WindowActionsMixin

        class _FakeWindow(WindowActionsMixin):
            pass

        window = _FakeWindow()
        window.pycoder_repl_manager = PyCoderReplManager()
        node = PyCoderNode(parent_node=None, mode=PyCoderMode.MANUAL)
        node.set_code("print(1)")
        node.is_running = False

        with patch("graphlink_window_actions.CodeExecutionWorker") as mock_worker_cls:
            mock_worker_cls.return_value = MagicMock()
            window.execute_pycoder_node(node)

        used_repl = mock_worker_cls.call_args[0][1]
        assert used_repl is window.pycoder_repl_manager.get_repl(node)
