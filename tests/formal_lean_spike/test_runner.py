import copy
import json
import os
import subprocess
import sys
import unittest
from threading import Event
from unittest.mock import Mock, patch

import runner


IMAGE = "sha256:" + "1" * 64
CID = "b" * 64


def created_job(name, stage="probe"):
    return {
        "Id": CID, "Image": IMAGE, "Name": "/" + name, "State": {"Status": "created"},
        "Mounts": [],
        "Config": {"User": "65534:65534", "WorkingDir": "/tmp", "Cmd": [stage],
                   "Entrypoint": runner.ENTRYPOINT, "Labels": {runner.LABEL: name}},
        "HostConfig": {
            "AutoRemove": True, "Privileged": False, "ReadonlyRootfs": True, "Runtime": "runc",
            "NetworkMode": "none", "IpcMode": "none", "CgroupnsMode": "private", "PidMode": "",
            "UTSMode": "", "CapDrop": ["ALL"], "NanoCpus": 10**9, "Memory": 4 * 1024**3,
            "MemorySwap": 4 * 1024**3, "PidsLimit": 64, "Tmpfs": {"/tmp": runner.TMPFS},
            "LogConfig": {"Type": "none"},
            "SecurityOpt": ["no-new-privileges=true", "apparmor=docker-default"],
        },
    }


class TestDockerBoundary(unittest.TestCase):
    def test_preflight_rejects_inputs_before_accessing_engine(self):
        with patch.object(runner.platform, "system", side_effect=AssertionError):
            for args in [("mutable:tag", "probe", b""), (IMAGE, "info", b""),
                         (IMAGE, "export", b"x"), (IMAGE, "probe", b"not empty"),
                         (IMAGE, "replay", bytes(runner.ARTIFACT_LIMIT + 1))]:
                with self.subTest(stage=args[1]), self.assertRaises(ValueError):
                    runner.run_stage(*args)
            event = Event()
            event.set()
            with self.assertRaises(runner.StageCancelled):
                runner.run_stage(IMAGE, "probe", b"", cancel=event)

    def test_unavailable_engine_or_platform_never_spawns_host_process(self):
        with patch.object(subprocess, "Popen", side_effect=AssertionError), \
                patch.object(runner.platform, "system", return_value="Windows"):
            with self.assertRaisesRegex(runner.IsolationError, "Linux"):
                runner.run_stage(IMAGE, "probe", b"")
        with patch.object(subprocess, "Popen", side_effect=AssertionError), \
                patch.object(runner.platform, "system", return_value="Linux"), \
                patch.object(runner.platform, "machine", return_value="x86_64"), \
                patch.object(runner.shutil, "which", return_value=None):
            with self.assertRaisesRegex(runner.IsolationError, "unavailable"):
                runner.run_stage(IMAGE, "probe", b"")

    def test_pre_start_inspection_rejects_drift(self):
        spec = created_job("job")
        runner._validate_container(spec, IMAGE, "probe", "job")
        for key, value in [
                ("NetworkMode", "bridge"), ("Privileged", True), ("ReadonlyRootfs", False),
                ("PidsLimit", 0), ("Memory", 0), ("Tmpfs", {}), ("CapDrop", []),
                ("CapAdd", ["SYS_ADMIN"]), ("Binds", ["/extra:/test"]), ("SecurityOpt", []),
                ("Runtime", "unreviewed"), ("DeviceRequests", [{"Count": 1}])]:
            changed = copy.deepcopy(spec)
            changed["HostConfig"][key] = value
            with self.subTest(key=key), self.assertRaises(runner.IsolationError):
                runner._validate_container(changed, IMAGE, "probe", "job")
        spec["Mounts"] = [{"Type": "bind", "Destination": "/tmp"}]
        with self.assertRaises(runner.IsolationError):
            runner._validate_container(spec, IMAGE, "probe", "job")

    def test_failed_creation_or_inspection_settles_ticket(self):
        docker = Mock()
        with patch.object(runner, "_exchange", side_effect=AssertionError):
            for responses in [[runner.IsolationError("create failed")], [b"bad id"],
                              [CID.encode(), b"[]"]]:
                docker.command.side_effect = responses
                with self.assertRaises(runner.IsolationError):
                    runner._run(docker, IMAGE, "probe", b"", 120, None)
                docker.cleanup.assert_called_once()
                docker.reset_mock()

    def test_stage_uses_exact_container_id_and_cleanup_has_final_say(self):
        def create_or_inspect(args):
            if args[0] == "create":
                nonlocal name
                name = args[args.index("--name") + 1]
                self.assertIn("--pull=never", args)
                self.assertEqual(args[-2:], [IMAGE, "probe"])
                return CID.encode()
            self.assertEqual(args, ["container", "inspect", CID])
            return json.dumps([created_job(name)]).encode()

        name = None
        docker = Mock(prefix=["docker"], env={})
        docker.command.side_effect = create_or_inspect
        with patch.object(runner, "_exchange") as exchange:
            exchange.return_value = runner.StageResult(0, b"result", b"")
            self.assertEqual(runner._run(docker, IMAGE, "probe", b"", 120, None).stdout, b"result")
            self.assertEqual(exchange.call_args.args[0], ["docker", "start", "--attach", "--interactive", CID])
            docker.cleanup.assert_called_once_with(name)
            exchange.side_effect = runner.StageTimeout("timed out")
            with self.assertRaises(runner.StageTimeout):
                runner._run(docker, IMAGE, "probe", b"", 120, None)
            docker.cleanup.assert_called_with(name)
            exchange.side_effect = None
            docker.cleanup.side_effect = runner.CleanupError("unconfirmed")
            with self.assertRaises(runner.CleanupError):
                runner._run(docker, IMAGE, "probe", b"", 120, None)

    def test_daemon_cleanup_confirms_absence_after_forced_removal(self):
        docker = runner._Docker("/usr/bin/docker", "/empty")
        with patch.object(docker, "command", side_effect=[CID.encode(), CID.encode(), b""]) as commands:
            docker.cleanup("job")
        query = commands.call_args_list[0].args[0]
        self.assertIn(f"label={runner.LABEL}=job", query)
        self.assertIn("name=^/job$", query)
        self.assertEqual(commands.call_args_list[1].args[0], ["rm", "--force", CID])
        self.assertEqual(commands.call_args_list[2].args[0], query)
        with patch.object(docker, "command", side_effect=runner.IsolationError("offline")):
            with self.assertRaises(runner.CleanupError):
                docker.cleanup("job")


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux CLI pipe transport")
class TestBoundedTransport(unittest.TestCase):
    @staticmethod
    def exchange(script, payload=b"", timeout=3, limit=100, cancel=None):
        return runner._exchange([sys.executable, "-I", "-c", script], dict(os.environ),
                                payload, timeout, limit, cancel)

    def test_transport_drains_both_pipes_while_sending_all_input(self):
        payload = b"x" * 180000
        result = self.exchange(
            "import sys; sys.stderr.buffer.write(b'y'*180000); "
            "sys.stdout.buffer.write(sys.stdin.buffer.read()); sys.exit(7)", payload, limit=len(payload))
        self.assertEqual(result, runner.StageResult(7, payload, b"y" * 180000))

    def test_overflow_and_wall_time_discard_results(self):
        with self.assertRaises(runner.OutputLimit):
            self.exchange("import sys; sys.stdout.write('too large')", limit=2)
        with self.assertRaises(runner.OutputLimit):
            self.exchange("import sys; sys.stderr.write('x' * 270000)")
        with self.assertRaises(runner.StageTimeout) as expired:
            self.exchange("import sys, time; sys.stdout.write('ready'); sys.stdout.flush(); time.sleep(2)",
                          timeout=0.5)
        self.assertEqual(expired.exception.stdout, b"ready")
        event = Event()
        event.set()
        with self.assertRaises(runner.StageCancelled):
            self.exchange("import time; time.sleep(2)", cancel=event)
