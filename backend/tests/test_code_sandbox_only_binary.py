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


def _write_benign_source_backend(pkg_dir):
    """ADR-005 stage 5.5 review-fix: unlike _write_hostile_backend, this
    PEP 517 backend's build_wheel() hook actually SUCCEEDS - constructing a
    real, importable wheel via stdlib zipfile from inside the hook itself
    - so the escalation's own POSITIVE case (a genuine source-only package
    installs cleanly when allowed) has real coverage, not just the
    negative/security-proof direction (a hostile hook's side effect fires)
    TestAllowSourceBuildsEscalation already covers."""
    (pkg_dir / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [build-system]
            requires = []
            build-backend = "benign_source_backend"
            backend-path = ["."]

            [project]
            name = "benign-source-pkg"
            version = "0.0.1"
            """
        ),
        encoding="utf-8",
    )
    (pkg_dir / "benign_source_backend.py").write_text(
        textwrap.dedent(
            """\
            import pathlib
            import zipfile


            def get_requires_for_build_wheel(config_settings=None):
                return []


            def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
                wheel_name = "benign_source_pkg-0.0.1-py3-none-any.whl"
                wheel_path = pathlib.Path(wheel_directory) / wheel_name
                with zipfile.ZipFile(wheel_path, "w") as zf:
                    zf.writestr("benign_source_pkg.py", "VALUE = 2\\n")
                    zf.writestr(
                        "benign_source_pkg-0.0.1.dist-info/METADATA",
                        "Metadata-Version: 2.1\\nName: benign-source-pkg\\nVersion: 0.0.1\\n",
                    )
                    zf.writestr(
                        "benign_source_pkg-0.0.1.dist-info/WHEEL",
                        "Wheel-Version: 1.0\\nGenerator: test\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n",
                    )
                    zf.writestr("benign_source_pkg-0.0.1.dist-info/RECORD", "")
                return wheel_name
            """
        ),
        encoding="utf-8",
    )


def _build_benign_source_sdist(tmp_path):
    """A real, pip-installable, genuinely source-only sdist (no wheel ever
    published) whose build backend actually succeeds - the positive-path
    twin of _build_hostile_sdist above."""
    pkg_dir = tmp_path / "benign_source_pkg_src"
    pkg_dir.mkdir()
    _write_benign_source_backend(pkg_dir)

    dist_dir = tmp_path / "benign_source_dist"
    dist_dir.mkdir()
    sdist_path = dist_dir / "benign_source_pkg-0.0.1.tar.gz"
    with tarfile.open(sdist_path, "w:gz") as tar:
        for filename in ("pyproject.toml", "benign_source_backend.py"):
            tar.add(pkg_dir / filename, arcname=f"benign_source_pkg-0.0.1/{filename}")
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

    def test_sync_requirements_omits_only_binary_when_allow_source_builds_is_true(self, tmp_path, monkeypatch):
        # ADR-005 stage 5.5's source-build escalation - the opt-in must
        # remove the restriction, not merely relax it, and --no-input must
        # still be present regardless (defense in depth, unrelated to the
        # source-build setting - see sync_requirements's own comment).
        sandbox = VirtualEnvSandbox("flag-check-escalation-test")
        sandbox.base_dir = tmp_path
        sandbox.requirements_file = tmp_path / "requirements.txt"
        sandbox.requirements_hash_file = tmp_path / ".requirements.sha256"
        sandbox.venv_dir = tmp_path / "venv"

        captured_args = {}

        def fake_run_subprocess(args, **kwargs):
            captured_args["args"] = args
            return "", 0

        monkeypatch.setattr(sandbox, "_run_subprocess", fake_run_subprocess)

        sandbox.sync_requirements("some-package==1.0", should_continue=lambda: True, allow_source_builds=True)

        args = captured_args["args"]
        assert "--only-binary" not in args
        assert "--no-input" in args


class TestAllowSourceBuildsEscalation:
    def test_allow_source_builds_true_lets_the_hostile_sdist_backend_execute(self, tmp_path):
        # The mirror image of TestOnlyBinaryBlocksAHostileSdist above, using
        # the identical hostile sdist fixture: proves the escalation
        # genuinely removes the restriction (the backend's side effect now
        # fires), not merely that the flag is accepted without error. The
        # hostile backend's build_wheel() still deliberately raises after
        # writing the marker (see _write_hostile_backend), so the overall
        # install still fails either way - what changes is whether the
        # backend ever RAN, which the marker file proves directly.
        marker = tmp_path / "marker.txt"
        sdist_path = _build_hostile_sdist(tmp_path, marker)
        find_links_dir = tmp_path / "find_links"
        find_links_dir.mkdir()
        (find_links_dir / sdist_path.name).write_bytes(sdist_path.read_bytes())

        sandbox = _make_sandbox_with_real_venv(tmp_path, "hostile-sdist-escalation-test")

        with pytest.raises(RuntimeError, match="Dependency installation failed"):
            sandbox.sync_requirements(
                f"hostile-pkg==0.0.1\n--no-index\n--find-links {find_links_dir.as_posix()}",
                should_continue=lambda: True,
                allow_source_builds=True,
            )

        assert marker.exists(), (
            "allow_source_builds=True should have let pip attempt the sdist build - "
            "the hostile backend's build_wheel() hook never executed"
        )


class TestAllowSourceBuildsInstallsARealSourceOnlyPackage:
    def test_allow_source_builds_true_successfully_installs_a_genuine_source_only_package(self, tmp_path):
        # ADR-005 stage 5.5 test-coverage-gap fix: the escalation test above
        # only proves the negative/security direction (a hostile backend's
        # hook executes when allowed) - it says nothing about whether a
        # LEGITIMATE source-only install actually succeeds under the same
        # flag. A regression in the pip invocation itself, env stripping
        # (safe_subprocess_env), or stage 5.3's scratch-dir permissions
        # could silently break every real source build even with the
        # escalation correctly enabled, and nothing else in this suite
        # would notice.
        sdist_path = _build_benign_source_sdist(tmp_path)
        find_links_dir = tmp_path / "find_links"
        find_links_dir.mkdir()
        (find_links_dir / sdist_path.name).write_bytes(sdist_path.read_bytes())

        sandbox = _make_sandbox_with_real_venv(tmp_path, "benign-source-install-test")

        sandbox.sync_requirements(
            f"benign-source-pkg==0.0.1\n--no-index\n--find-links {find_links_dir.as_posix()}",
            should_continue=lambda: True,
            allow_source_builds=True,
        )

        python_exe = (
            sandbox.venv_dir / "Scripts" / "python.exe"
            if sys.platform == "win32"
            else sandbox.venv_dir / "bin" / "python"
        )
        check = subprocess.run(
            [str(python_exe), "-c", "import benign_source_pkg; print(benign_source_pkg.VALUE)"],
            capture_output=True, text=True, timeout=30,
        )
        assert check.returncode == 0, check.stderr
        assert check.stdout.strip() == "2"

    def test_the_same_source_only_package_fails_without_the_escalation(self, tmp_path):
        # Negative control, proving the fixture is genuinely source-only
        # (no wheel ever published) and the positive test above is
        # exercising the real --only-binary bypass, not some other reason
        # the install happened to succeed.
        sdist_path = _build_benign_source_sdist(tmp_path)
        find_links_dir = tmp_path / "find_links"
        find_links_dir.mkdir()
        (find_links_dir / sdist_path.name).write_bytes(sdist_path.read_bytes())

        sandbox = _make_sandbox_with_real_venv(tmp_path, "benign-source-install-control-test")

        with pytest.raises(RuntimeError, match="Dependency installation failed"):
            sandbox.sync_requirements(
                f"benign-source-pkg==0.0.1\n--no-index\n--find-links {find_links_dir.as_posix()}",
                should_continue=lambda: True,
            )


class TestDirectReferenceRequirementsBypassOnlyBinary:
    """ADR-005 stage 5.5 round-3 review-fix: `--only-binary :all:` only
    governs requirements pip resolves against an index/--find-links dir (the
    ONLY shape every test above this class exercises). A direct-URL
    reference (`pkg @ <url>`) or an editable/local-path install (`-e
    <path>`) is never index-resolved at all, so pip still invokes THAT
    reference's own PEP 517 build backend even with --only-binary :all: on
    the command line - this class proves the bypass is real (negative
    control, mirroring TestOnlyBinaryBlocksAHostileSdist's own "DOES execute
    without the flag" control) and that sync_requirements now rejects it
    before pip ever runs, using the identical hostile backend fixture as the
    rest of this module."""

    def test_a_direct_file_url_reference_still_executes_its_backend_even_with_only_binary(self, tmp_path):
        # Negative control: replicates pip's ACTUAL pre-fix-equivalent
        # invocation (--only-binary :all:, no requirements-line validation)
        # directly against a hostile PEP 517 backend referenced by a direct
        # file:// URL rather than an index/--find-links lookup. Proves the
        # audit finding's mechanism, not just that sync_requirements now
        # rejects the line - if pip's own behavior for direct references
        # ever changes, this control (not just the rejection test below)
        # would catch it.
        marker = tmp_path / "marker.txt"
        pkg_dir = tmp_path / "hostile_pkg_src"
        pkg_dir.mkdir()
        _write_hostile_backend(pkg_dir, marker)

        venv_dir = tmp_path / "venv"
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            check=True, capture_output=True, text=True, timeout=180,
        )
        python_exe = venv_dir / "Scripts" / "python.exe" if sys.platform == "win32" else venv_dir / "bin" / "python"
        direct_url = pkg_dir.as_uri()

        subprocess.run(
            [
                str(python_exe), "-m", "pip", "install", "--disable-pip-version-check",
                "--only-binary", ":all:", "--no-input", f"hostile-pkg @ {direct_url}",
            ],
            capture_output=True, text=True, timeout=120,
        )

        assert marker.exists(), (
            "test harness sanity check failed: a direct file:// reference "
            "should still invoke the hostile backend even with "
            "--only-binary :all: passed - if this assert fails, pip's own "
            "behavior for direct references has changed and this finding "
            "needs re-verification, not just this test suite"
        )

    def test_sync_requirements_rejects_a_direct_url_reference_before_pip_ever_runs(self, tmp_path):
        marker = tmp_path / "marker.txt"
        pkg_dir = tmp_path / "hostile_pkg_src"
        pkg_dir.mkdir()
        _write_hostile_backend(pkg_dir, marker)
        direct_url = pkg_dir.as_uri()

        sandbox = _make_sandbox_with_real_venv(tmp_path, "direct-url-block-test")

        with pytest.raises(RuntimeError, match="Dependency installation blocked"):
            sandbox.sync_requirements(
                f"hostile-pkg @ {direct_url}",
                should_continue=lambda: True,
            )

        assert not marker.exists(), (
            "the hostile backend's build_wheel() hook executed - the new "
            "direct-URL/editable check did not block it before pip ran"
        )

    def test_sync_requirements_rejects_an_editable_local_path_reference(self, tmp_path):
        marker = tmp_path / "marker.txt"
        pkg_dir = tmp_path / "hostile_pkg_src"
        pkg_dir.mkdir()
        _write_hostile_backend(pkg_dir, marker)

        sandbox = _make_sandbox_with_real_venv(tmp_path, "editable-block-test")

        with pytest.raises(RuntimeError, match="Dependency installation blocked"):
            sandbox.sync_requirements(
                f"-e {pkg_dir.as_posix()}",
                should_continue=lambda: True,
            )

        assert not marker.exists(), (
            "the hostile backend's build_wheel() hook executed - the new "
            "editable-install check did not block it before pip ran"
        )

    def test_allow_source_builds_true_still_lets_a_direct_url_reference_reach_pip(self, tmp_path):
        # Mirrors TestAllowSourceBuildsEscalation's own mirror-image style:
        # the new check must not block the explicit opt-in - a direct
        # reference under allow_source_builds=True reaches pip exactly as
        # before, the backend runs (proven by the marker), and the hostile
        # backend's own deliberate failure then surfaces as the ordinary
        # pip-invocation "Dependency installation failed" error, not the new
        # pre-check's "Dependency installation blocked" error.
        marker = tmp_path / "marker.txt"
        pkg_dir = tmp_path / "hostile_pkg_src"
        pkg_dir.mkdir()
        _write_hostile_backend(pkg_dir, marker)
        direct_url = pkg_dir.as_uri()

        sandbox = _make_sandbox_with_real_venv(tmp_path, "direct-url-escalation-test")

        with pytest.raises(RuntimeError, match="Dependency installation failed"):
            sandbox.sync_requirements(
                f"hostile-pkg @ {direct_url}",
                should_continue=lambda: True,
                allow_source_builds=True,
            )

        assert marker.exists(), (
            "allow_source_builds=True should have let pip attempt the "
            "direct-URL install - the hostile backend's build_wheel() hook "
            "never executed"
        )

class TestGuardDefeatingOptionLinesBypassOnlyBinary:
    """SECURITY-FIX: option lines that are not package specifiers but that
    DEFEAT (`--no-binary :all:`) or ROUTE AROUND (`-r`/`-c` nested files)
    the --only-binary :all: guard. They sat on the generic option skip-list
    and were waved straight through to pip."""

    def test_no_binary_all_in_the_manifest_really_does_reverse_the_cli_flag(self, tmp_path, monkeypatch):
        # The bypass itself, proven against real pip rather than assumed:
        # with the pre-check disabled, a manifest carrying `--no-binary
        # :all:` lets the hostile sdist's build_wheel() run even though
        # --only-binary :all: is on the command line. (Same shape as
        # TestDirectReferenceRequirementsBypassOnlyBinary's own proof.)
        marker = tmp_path / "marker.txt"
        sdist_path = _build_hostile_sdist(tmp_path, marker)
        find_links_dir = tmp_path / "find_links"
        find_links_dir.mkdir()
        (find_links_dir / sdist_path.name).write_bytes(sdist_path.read_bytes())
        sandbox = _make_sandbox_with_real_venv(tmp_path, "no-binary-bypass-proof")

        from graphlink_plugins.code_sandbox import domain as sandbox_domain
        monkeypatch.setattr(sandbox_domain, "_direct_reference_requirement_lines", lambda _text: [])

        with pytest.raises(RuntimeError, match="Dependency installation failed"):
            sandbox.sync_requirements(
                f"hostile-pkg==0.0.1\n--no-binary :all:\n--no-index\n--find-links {find_links_dir.as_posix()}",
                should_continue=lambda: True,
            )
        assert marker.exists(), (
            "expected the hostile backend to RUN here - this test proves the "
            "manifest's --no-binary :all: overrides the CLI --only-binary :all:, "
            "which is the whole reason the pre-check must refuse that line"
        )

    def test_no_binary_all_in_the_manifest_is_refused_before_pip_runs(self, tmp_path):
        marker = tmp_path / "marker.txt"
        sdist_path = _build_hostile_sdist(tmp_path, marker)
        find_links_dir = tmp_path / "find_links"
        find_links_dir.mkdir()
        (find_links_dir / sdist_path.name).write_bytes(sdist_path.read_bytes())
        sandbox = _make_sandbox_with_real_venv(tmp_path, "no-binary-block-test")

        with pytest.raises(RuntimeError, match="Dependency installation blocked"):
            sandbox.sync_requirements(
                f"hostile-pkg==0.0.1\n--no-binary :all:\n--no-index\n--find-links {find_links_dir.as_posix()}",
                should_continue=lambda: True,
            )
        assert not marker.exists()

    def test_a_nested_requirements_file_cannot_smuggle_a_direct_reference(self, tmp_path):
        # The nested file's `pkg @ file://` line never passes through
        # _direct_reference_requirement_lines - pip expands -r itself - so
        # the `-r` line is what has to be refused.
        marker = tmp_path / "marker.txt"
        pkg_dir = tmp_path / "hostile_pkg_src"
        pkg_dir.mkdir()
        _write_hostile_backend(pkg_dir, marker)
        nested = tmp_path / "nested-reqs.txt"
        nested.write_text(f"hostile-pkg @ {pkg_dir.as_uri()}\n", encoding="utf-8")
        sandbox = _make_sandbox_with_real_venv(tmp_path, "nested-r-block-test")

        with pytest.raises(RuntimeError, match="Dependency installation blocked"):
            sandbox.sync_requirements(
                f"-r {nested.as_posix()}",
                should_continue=lambda: True,
            )
        assert not marker.exists()

    @pytest.mark.parametrize(
        "line",
        [
            "--no-binary :all:",
            "--no-binary=hostile-pkg",
            "-r other.txt",
            "--requirement other.txt",
            "-r https://example.invalid/reqs.txt",
            "-c constraints.txt",
            "--constraint=constraints.txt",
            "--global-option=--evil",
            "--install-option=--evil",
            "--config-settings=x=y",
            "--no-build-isolation",
        ],
    )
    def test_each_guard_defeating_option_line_is_flagged(self, line):
        from graphlink_plugins.code_sandbox.domain import _direct_reference_requirement_lines

        assert _direct_reference_requirement_lines(f"requests==2.31.0\n{line}\n") == [line]

    @pytest.mark.parametrize(
        "line",
        [
            "--no-index",
            "--find-links ./wheels",
            "--index-url https://pypi.org/simple",
            "--extra-index-url https://pypi.org/simple",
            "--only-binary :all:",
            "--prefer-binary",
            "--require-hashes",
            "--pre",
            "--no-deps",
            "--trusted-host pypi.org",
        ],
    )
    def test_wheel_source_selection_options_stay_allowed(self, line):
        from graphlink_plugins.code_sandbox.domain import _direct_reference_requirement_lines

        assert _direct_reference_requirement_lines(f"requests==2.31.0\n{line}\n") == []

    def test_allow_source_builds_true_still_lets_no_binary_reach_pip(self, tmp_path, monkeypatch):
        sandbox = VirtualEnvSandbox("no-binary-escalation-test")
        sandbox.base_dir = tmp_path
        sandbox.requirements_file = tmp_path / "requirements.txt"
        sandbox.requirements_hash_file = tmp_path / ".requirements.sha256"
        sandbox.venv_dir = tmp_path / "venv"
        captured = {}
        monkeypatch.setattr(sandbox, "_run_subprocess", lambda args, **kw: captured.setdefault("args", args) and ("", 0) or ("", 0))

        sandbox.sync_requirements(
            "requests==2.31.0\n--no-binary :all:", should_continue=lambda: True, allow_source_builds=True,
        )
        assert "args" in captured, "the explicit opt-in must still reach pip"


class TestOrdinaryManifestsStillReachPip:
    def test_an_ordinary_pinned_manifest_is_not_flagged_and_still_reaches_pip(self, tmp_path, monkeypatch):
        # False-positive guard, in TestPipCommandIncludesTheFlags's own fast
        # (non-subprocess-executing) style: an everyday manifest - including
        # extras, environment markers, and the exact --no-index/--find-links
        # option lines the real end-to-end tests above already use - must
        # still reach pip unchanged.
        sandbox = VirtualEnvSandbox("ordinary-requirement-test")
        sandbox.base_dir = tmp_path
        sandbox.requirements_file = tmp_path / "requirements.txt"
        sandbox.requirements_hash_file = tmp_path / ".requirements.sha256"
        sandbox.venv_dir = tmp_path / "venv"

        captured_args = {}

        def fake_run_subprocess(args, **kwargs):
            captured_args["args"] = args
            return "", 0

        monkeypatch.setattr(sandbox, "_run_subprocess", fake_run_subprocess)

        sandbox.sync_requirements(
            "requests>=2.31,<3\n"
            "numpy==1.26.0\n"
            "pandas[excel]==2.2.0 ; python_version >= '3.9'\n"
            "--no-index\n"
            "--find-links ./local-wheels",
            should_continue=lambda: True,
        )

        assert "args" in captured_args, "an ordinary manifest must still reach pip"
        assert "--only-binary" in captured_args["args"]
