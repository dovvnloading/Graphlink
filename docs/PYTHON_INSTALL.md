# Installing Python packages from the workspace agent

The workspace tool named python.install accepts a list of index-resolved
package requirements, for example:

    {"packages": ["requests>=2,<3", "pandas==2.2.3"]}

It targets the **same interpreter as python.exec**. In a trusted project this
is the first usable .venv or venv; otherwise it is the app-managed virtualenv
for that agent and that project. Scratch workspaces have their own managed
virtualenv. No environment path, index URL, requirements-file path, or pip
option is supplied by the model.

## Consent

python.install has code.execute scope and approval="always" with fresh
re-prompting even for identical requests or previously granted sessions.
Before showing the prompt, the harness computes (without creating) the
interpreter that python.exec would use. The approval panel shows the full
interpreter path and each requested requirement. The approval identity is
unique per call; old or replayed approvals cannot authorize a new install.

The selected interpreter is recorded only after consent and checked again
before package installation. If a different .venv appears or the workspace
is rebound while the prompt is open, the install is refused and must be
approved again. Rejected or mutated arguments never reach the handler.

Wheels contain executable Python code. Wheel-only is not a security sandbox;
the approval is still necessary for every change to an environment.

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

The cache is an optimization, not an offline package repository guarantee:
uncached requirements need network access, and availability of new platform
or Python-specific wheels still depends on PyPI. A failed download leaves
previously cached wheels intact.
