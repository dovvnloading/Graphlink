# Installing Python packages from the workspace agent

The workspace tool named python.install accepts a list of index-resolved
package requirements, for example:

    {"packages": ["requests>=2,<3", "pandas==2.2.3"]}

It targets the **same interpreter as python.exec**. In a trusted project this
is the first usable .venv or venv; otherwise it is the app-managed virtualenv
for that agent and that project. Scratch workspaces have their own managed
virtualenv. No environment path, index URL, requirements-file path, or pip
option is supplied by the model.

In the workspace agent's **Isolated** profile, both Python tools instead run
in the restricted Docker/Podman container. A compatible Linux project `.venv`
or `venv` is selected first; otherwise a separate, lazily created
container-native virtualenv is used. Its scratch/project binding and
node-deletion cleanup match the host environment rules, but it never runs
host Python. A host-only project venv cannot be used inside the image.

## Consent

The tool requires `code.execute` scope. **Ask** and **Auto-edit** require fresh per-install approval even for identical or already cached requirements; prior session/fingerprint grants never suffice. **Isolated Autonomous** instead first attempts a compatible cached-wheel installation **offline** without a prompt. If the cache is incomplete, it stops **before contacting PyPI** and requests new authorization bound to the exact packages and interpreter before downloading anything. Existing shell approvals cannot authorize that download.

When a prompt is needed, the harness computes (without creating) the interpreter that `python.exec` would use. The approval panel shows that interpreter and every requirement; Isolated also discloses the temporary container networking required for approved PyPI wheel downloads. Each prompt has a fresh ID; stale/replayed approvals cannot authorize another install.

The chosen interpreter is pinned and rechecked at installation. If a different `.venv` appears or the workspace/profile changes during consent, the request is refused and must be approved again. Isolated consent is also bound to the resolved workspace root and execution profile so an identical container interpreter path cannot authorize a different project. Rejected or mutated arguments cannot reach the handler.

**Wheels contain executable Python code.** Wheel-only does not make an installation harmless. In Autonomous, cached packages can change the environment without a per-install prompt; use Ask if you want to approve every installation. See [permission modes](WORKSPACE_PERMISSION_MODES.md).

## Download and offline behavior

The tool first runs pip install with --no-index and --find-links pointing at
the **shared wheelhouse**:

    ~/.graphlink/python-wheel-cache/wheels

If the requested requirement and all needed dependencies can be satisfied by
cached wheels, pip installs them without network access. Otherwise the tool
runs pip download with --only-binary=:all: against the canonical default
index https://pypi.org/simple/ and stores the downloaded wheels in that
shared wheelhouse. It then retries the install **from the wheelhouse only**.

The HTTP download cache is a sibling under
~/.graphlink/python-wheel-cache/pip-http. Unlike an ordinary pip HTTP cache,
the wheelhouse can serve as an offline source for future installs, including
installs into other workspaces. The cache is outside temporary workspace
directories and is not deleted on agent/node deletion or workspace age sweep.
Wheels remain platform- and Python-version-specific: offline installation
works only when compatible wheels for the package and its dependencies are
already cached.

Both phases enforce --only-binary=:all:. The tool accepts plain package names,
extras and version constraints only; no direct URL, local path, VCS source,
editable option, pip flag, requirement file or environment marker is accepted.
Pip is launched with an explicit allowlisted environment, with
PIP_CONFIG_FILE set to os.devnull to disable global/user config and external
extra indexes. The online download uses the explicit default PyPI URL; the
installation phase uses --no-index. Neither step runs a shell. Subprocess
execution is guarded, time-bounded and cancellable with the agent run.

In Isolated mode, virtualenv bootstrap, cached wheel installation and the
final offline install all use `--network=none`; **only the approved
`pip download` container invocation** uses bridge networking. Every
invocation uses the same fixed image and bounded container resources. The
cache is mounted read/write only for install operations; `python.exec`
never gets network access or the shared cache mount.

The cache is an optimization, not an offline package repository guarantee:
uncached requirements need network access, and availability of new platform
or Python-specific wheels still depends on PyPI. A failed download leaves
previously cached wheels intact.
