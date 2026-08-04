"""ADR-005 stage 5.5: pip `--only-binary :all:` default for the Code
Sandbox's dependency installation step (`VirtualEnvSandbox.sync_requirements`).

THE THREAT. When pip resolves a requirement to a source distribution (no
wheel published for this platform/version, or a private/unindexed package),
it invokes that sdist's own PEP 517 build backend - arbitrary Python, not a
data format - to produce a wheel, during what looks like an ordinary
dependency install. An LLM-authored requirements manifest naming an
attacker-controlled or typosquatted package name is exactly the audit
finding (H2, dependency-install bounding) this stage closes.

THE PROOF. `_build_hostile_sdist` below constructs a REAL, standards-shaped
sdist tarball (via stdlib `tarfile` only - no `build` package dependency,
which matters because `build`/`twine` are CI-only tooling installed in a
separate job, never in the one that runs `pytest -q`; see
.github/workflows/ci.yml's own comment on this) whose `build_wheel()` hook
writes a marker file before failing. The marker's existence is the proof
the hook executed; its content is irrelevant, since a real attacker's
payload would never announce itself. `TestOnlyBinaryBlocksAHostileSdist`
proves both directions with this exact sdist: a plain `pip install` (the
pre-fix default) executes the hook; `--only-binary :all:` (the fix) refuses
to even attempt the build. `TestOnlyBinaryDoesNotBreakWheelInstalls` proves
the fix does not regress the ordinary case.
"""

from __future__ import annotations

import subprocess
import sys
import tarfile
import textwrap

import pytest

from graphlink_plugins.code_sandbox.domain import VirtualEnvSandbox

# Building a real venv + running real pip against a real sdist is
# inherently slower than the rest of this codebase's tests - this is a
# deliberate tradeoff (see the module docstring: a mock cannot prove an
# arbitrary-code-execution vector is actually closed), matching the same
# real-subprocess discipline ADR-005 stage 5.2's own resource-guard tests
# already established for this codebase. Every real subprocess call below
# has its own explicit `timeout=` (venv creation, pip install), so a hung
# child fails the test with a clear TimeoutExpired rather than hanging the
# suite - no pytest-timeout plugin dependency needed.


def _write_hostile_backend(pkg_dir, marker_path):
    (pkg_dir / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [build-system]
            requires = []
            build-backend = "hostile_backend"
            backend-path = ["."]

            [project]
            name = "hostile-pkg"
            version = "0.0.1"
            """
        ),
        encoding="utf-8",
    )
    marker_repr = repr(str(marker_path))
    (pkg_dir / "hostile_backend.py").write_text(
        textwrap.dedent(
            f"""\
            import pathlib

            MARKER = pathlib.Path({marker_repr})


            def get_requires_for_build_wheel(config_settings=None):
                return []


            def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
                MARKER.write_text("PWNED: build_wheel() executed arbitrary code during pip install")
                raise RuntimeError("hostile_backend: intentionally failing after marking execution")
            """
        ),
        encoding="utf-8",
    )


def _build_hostile_sdist(tmp_path, marker_path):
    """A real, pip-installable PEP 517 sdist whose build_wheel() hook has an
    observable side effect - built with stdlib tarfile only, deliberately
    NOT via `python -m build` (not a project runtime/test dependency; see
    module docstring)."""
    pkg_dir = tmp_path / "hostile_pkg_src"
    pkg_dir.mkdir()
    _write_hostile_backend(pkg_dir, marker_path)

    dist_dir = tmp_path / "hostile_dist"
    dist_dir.mkdir()
    sdist_path = dist_dir / "hostile_pkg-0.0.1.tar.gz"
    with tarfile.open(sdist_path, "w:gz") as tar:
        for filename in ("pyproject.toml", "hostile_backend.py"):
            tar.add(pkg_dir / filename, arcname=f"hostile_pkg-0.0.1/{filename}")
    return sdist_path


def _build_benign_wheel(tmp_path):
    """A trivial, real wheel (built directly with stdlib zipfile - a wheel
    is just a zip with a specific layout) so the "does a real wheel still
    install" test has no dependency on `build`/`setuptools` being present
    either."""
    import zipfile

    dist_dir = tmp_path / "benign_dist"
    dist_dir.mkdir()
    wheel_path = dist_dir / "benign_pkg-0.0.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, "w") as zf:
        zf.writestr("benign_pkg.py", "VALUE = 1\n")
        zf.writestr(
            "benign_pkg-0.0.1.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: benign-pkg\nVersion: 0.0.1\n",
        )
        zf.writestr(
            "benign_pkg-0.0.1.dist-info/WHEEL",
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        zf.writestr("benign_pkg-0.0.1.dist-info/RECORD", "")
    return wheel_path


def _make_sandbox_with_real_venv(tmp_path, sandbox_id):
    sandbox = VirtualEnvSandbox(sandbox_id)
    sandbox.base_dir = tmp_path / "sandbox"
    sandbox.venv_dir = sandbox.base_dir / "venv"
    sandbox.requirements_file = sandbox.base_dir / "requirements.txt"
    sandbox.requirements_hash_file = sandbox.base_dir / ".requirements.sha256"
    sandbox.script_path = sandbox.base_dir / "sandbox_entry.py"
    sandbox.base_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "venv", str(sandbox.venv_dir)],
        check=True, capture_output=True, text=True, timeout=180,
    )
    return sandbox


class TestOnlyBinaryBlocksAHostileSdist:
    def test_a_hostile_sdist_backend_never_executes_through_sync_requirements(self, tmp_path):
        marker = tmp_path / "marker.txt"
        sdist_path = _build_hostile_sdist(tmp_path, marker)
        find_links_dir = tmp_path / "find_links"
        find_links_dir.mkdir()
        (find_links_dir / sdist_path.name).write_bytes(sdist_path.read_bytes())

        sandbox = _make_sandbox_with_real_venv(tmp_path, "hostile-sdist-test")

        # Forward slashes, not str(find_links_dir): pip's requirements-file
        # option-line parser treats backslashes as escape characters, so a
        # raw Windows path here silently mangles into a nonexistent
        # location - discovered empirically when this test's find-links
        # line failed to resolve at all (confirmed via pip's own "ignored:
        # it is either a non-existing path" warning).
        with pytest.raises(RuntimeError, match="Dependency installation failed"):
            sandbox.sync_requirements(
                f"hostile-pkg==0.0.1\n--no-index\n--find-links {find_links_dir.as_posix()}",
                should_continue=lambda: True,
            )

        assert not marker.exists(), (
            "the hostile sdist's build_wheel() hook executed - "
            "--only-binary :all: did not block it"
        )

    def test_the_same_hostile_sdist_DOES_execute_without_the_flag(self, tmp_path, monkeypatch):
        # Negative control, proving the sdist itself is genuinely hostile
        # and the test harness genuinely exercises real pip - not that
        # sync_requirements() would reject ANY install for unrelated
        # reasons. Directly replicates the pre-fix command (no
        # --only-binary) against the identical sdist.
        marker = tmp_path / "marker.txt"
        sdist_path = _build_hostile_sdist(tmp_path, marker)
        find_links_dir = tmp_path / "find_links"
        find_links_dir.mkdir()
        (find_links_dir / sdist_path.name).write_bytes(sdist_path.read_bytes())

        venv_dir = tmp_path / "venv"
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            check=True, capture_output=True, text=True, timeout=180,
        )
        python_exe = venv_dir / "Scripts" / "python.exe" if sys.platform == "win32" else venv_dir / "bin" / "python"

        subprocess.run(
            [
                str(python_exe), "-m", "pip", "install", "--disable-pip-version-check",
                "--no-input", "--no-index", "--find-links", str(find_links_dir),
                "hostile-pkg==0.0.1",
            ],
            capture_output=True, text=True, timeout=120,
        )

        assert marker.exists(), (
            "test harness sanity check failed: the hostile sdist should "
            "execute its build_wheel() hook when installed WITHOUT "
            "--only-binary - if this assert fails, the sdist fixture "
            "itself is broken, not the fix under test"
        )


class TestOnlyBinaryDoesNotBreakWheelInstalls:
    def test_a_package_with_a_real_wheel_still_installs_cleanly(self, tmp_path):
        wheel_path = _build_benign_wheel(tmp_path)
        find_links_dir = tmp_path / "find_links"
        find_links_dir.mkdir()
        (find_links_dir / wheel_path.name).write_bytes(wheel_path.read_bytes())

        sandbox = _make_sandbox_with_real_venv(tmp_path, "benign-wheel-test")

        sandbox.sync_requirements(
            f"benign-pkg==0.0.1\n--no-index\n--find-links {find_links_dir.as_posix()}",
            should_continue=lambda: True,
        )

        python_exe = (
            sandbox.venv_dir / "Scripts" / "python.exe"
            if sys.platform == "win32"
            else sandbox.venv_dir / "bin" / "python"
        )
        check = subprocess.run(
            [str(python_exe), "-c", "import benign_pkg; print(benign_pkg.VALUE)"],
            capture_output=True, text=True, timeout=30,
        )
        assert check.returncode == 0, check.stderr
        assert check.stdout.strip() == "1"


class TestPipCommandIncludesTheFlags:
    def test_sync_requirements_passes_only_binary_and_no_input_to_pip(self, tmp_path, monkeypatch):
        # Fast, non-subprocess-executing check that the exact flags are
        # present in the built command - complements (does not replace)
        # the real end-to-end tests above.
        sandbox = VirtualEnvSandbox("flag-check-test")
        sandbox.base_dir = tmp_path
        sandbox.requirements_file = tmp_path / "requirements.txt"
        sandbox.requirements_hash_file = tmp_path / ".requirements.sha256"
        # python_executable is a read-only property computed from venv_dir -
        # set that instead (its own target need not exist for this test,
        # since _run_subprocess itself is monkeypatched below).
        sandbox.venv_dir = tmp_path / "venv"

        captured_args = {}

        def fake_run_subprocess(args, **kwargs):
            captured_args["args"] = args
            return "", 0

        monkeypatch.setattr(sandbox, "_run_subprocess", fake_run_subprocess)

        sandbox.sync_requirements("some-package==1.0", should_continue=lambda: True)

        args = captured_args["args"]
        assert "--only-binary" in args
        assert args[args.index("--only-binary") + 1] == ":all:"
        assert "--no-input" in args
