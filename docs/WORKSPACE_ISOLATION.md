# Workspace agent execution profiles

Open an Agent node's **Agent settings** and select **Execution profile**.

- **Host** (default): `shell.exec` and `shell.session` use the selected host shell and the existing execution guard.
- **Isolated**: `shell.exec`, `shell.session`, `python.exec`, and `python.install` run using Docker (preferred when both CLIs are present) or Podman. The currently bound trusted/scratch workspace is mounted read/write at `/workspace`. The container uses `python:3.12-slim`, **1 CPU**, **512 MiB RAM and swap**, a 128-process cap, no Linux capabilities, no-new-privileges, a read-only image filesystem and a 64 MiB temporary filesystem. Shell and Python execution are **network-disabled** (`--network=none`). Only the workspace is exposed to shell tools; Python tools additionally mount their scoped managed virtualenv (for trusted projects) and, for installs, the shared wheel cache. Containers never inherit backend secrets or the host shell. The only network exception is the PyPI wheel-download phase of an explicitly approved `python.install` call.

Install [Docker Desktop](https://docs.docker.com/desktop/) (Windows/macOS), [Docker Engine](https://docs.docker.com/engine/install/) (Linux), or [Podman](https://podman.io/docs/installation). Start the daemon/service where required. Pull the image *before* using Isolated mode, for example:

```sh
docker pull python:3.12-slim
# or: podman pull python:3.12-slim
```

The image is deliberately **not** downloaded during an agent command (`--pull=never`); if it is missing, the engine reports an error. No Python or shell tool falls back to Host when Isolated is selected. Commands retain per-call approval and timeout/cancellation behavior. Long-running shell sessions and Python interpreters are retired when a node switches its workspace or execution profile, and force-removed from the engine on stop or app teardown.

`python.exec` stays in a persistent container REPL across calls, with the workspace as its working directory. Python tools prefer a container-compatible project `.venv` or `venv`; otherwise the workspace gets a lazily created container-only virtualenv, separate from any host Python environment. Scratch virtualenvs live within their scratch workspace; trusted projects use app-owned per-agent/per-project environments outside the project directory. An existing host virtualenv that cannot run in Linux must be recreated for the container or removed so the managed container environment can be selected; Python tools never attempt to run it on the host.

`python.install` requires fresh approval identifying the container interpreter, requested packages, **and network permission**. It installs compatible binary wheels from the shared cache with no network. If wheels are missing, only the `pip download` subprocess gets bridge networking to contact the canonical PyPI index. The subsequent `pip install --no-index` remains offline. `python.exec`, shell tools, and virtualenv bootstrap never receive network access.

The preference is saved with the graph and snapshotted at the start of each task. Changing it while a task runs only affects subsequent tasks. Installing a CLI is not proof the engine/daemon is running; use its normal diagnostics if a container fails to start.
