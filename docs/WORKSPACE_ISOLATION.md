# Workspace agent execution profiles

Open an Agent node's **Agent settings** and select **Execution profile**.

- **Host** (default): `shell.exec`, `shell.session`, `python.exec` and `python.install` use the host's execution environment under their existing approval, subprocess-environment and execution-guard policies. The persistent Python REPL and its package installer are **not sandboxes**.
- **Isolated**: `shell.exec`, `shell.session`, `python.exec`, and `python.install` run in Docker (preferred) or Podman, using the fixed `python:3.12-slim` image and the bound trusted/scratch workspace at `/workspace`. Containers have **1 CPU**, **512 MiB RAM and swap**, 128 processes, dropped capabilities, no-new-privileges, a read-only image filesystem, and a 64 MiB temporary filesystem. All command stages are network-disabled (`--network=none`) except the explicitly approved PyPI wheel-download phase of `python.install`. Shell tools mount only the workspace; Python tools may also mount scoped app-owned virtualenvs and a shared wheel cache. Host executables and backend secrets are not passed in.
- **No host Python escape hatch**: Python execution and package installation use container-native interpreters, never host Python or host pip. A Host REPL is retired on the switch to Isolated; no tool falls back to Host if the engine, daemon or image is missing. Only the separately disclosed PyPI wheel-download subprocess can use temporary container networking.

## Network grants are separate from container networking

The Isolated setting does **not** turn the Graphlink backend into an offline process. Model/provider requests and explicit backend services can still use the host's network. Container commands use `--network=none` except the separately approved PyPI wheel-download phase of `python.install`.

The workspace agent's `net.fetch` grant offers only `web.search` and `web.fetch` when the built-in Web Research plugin is enabled, not every tool that happens to require `net.fetch`:

- `web.search` requires consent for the DuckDuckGo search service family once **per task** before submitting a search query. Its `ddgs` adapter explicitly selects the DuckDuckGo backend, rather than the library's multi-provider `auto` mode. This is a provider-family grant, **not** a socket-level hostname allowlist for the third-party search library.
- `web.fetch` requires consent for each destination hostname once **per task**, including each cross-domain redirect, after HTTPS/SSRF address validation and before its HTTP request. Redirects do not inherit approval of the original host. Requests to the same approved hostname reuse consent for the rest of that task.
- `run_node` cannot be used by a workspace agent to execute a Web Research node's `research` action: that path uses a separate search/fetch pipeline without the workspace tool's per-domain consent hooks. Use `web.search` and `web.fetch` instead. The separately approved Builder action is unchanged.

Other backend-host network operations (including model calls and separately approved Fal operations) are outside the container boundary and follow their **own** grants/approval rules. Granting an external domain is not data-loss prevention: model-authored queries and URLs can still disclose data to that approved service. Read untrusted web results as reference material, never as instructions.

## Setup, failures and lifecycle

Install [Docker Desktop](https://docs.docker.com/desktop/) (Windows/macOS), [Docker Engine](https://docs.docker.com/engine/install/) (Linux), or [Podman](https://podman.io/docs/installation). Start the daemon/service where required. Pull the image *before* using Isolated:

```sh
docker pull python:3.12-slim
# or: podman pull python:3.12-slim
```

The image is deliberately **not** downloaded during an agent command (`--pull=never`). If the CLI, daemon or image is unavailable, shell and Python tools fail closed and **never fall back to Host**. Workspace and extra-mount source paths containing commas or newlines are rejected because the container CLI parses comma-separated `--mount` fields even without a host shell.

Shell and Python tools retain per-call approval and timeout/cancellation behavior. Persistent shell sessions and Python interpreters are retired on workspace/profile changes and stopped on teardown. The preference is saved with the graph and snapshotted per task; changing it during a task affects subsequent tasks. An installed CLI is not proof its daemon is running.

`python.exec` uses a persistent container REPL. Python tools prefer container-compatible project `.venv`/`venv` environments, otherwise create separate container-native environments. Scratch environments stay in the scratch workspace; trusted projects use scoped app-owned per-agent/per-project directories. Host virtualenvs cannot run in the Linux container and are never executed or repaired on the host.

`python.install` requires a fresh approval bound to the workspace, profile, interpreter and packages, explicitly disclosing possible network access. It first uses cached binary wheels; if needed, only `pip download` contacts the canonical PyPI index. The following `pip install --no-index` runs offline.

This is a **workspace execution isolation profile**, not whole-application confinement, a guarantee against malicious Docker/Podman daemons or images, or a claim that approved backend network tools cannot exfiltrate data. See [the independent security audit](WORKSPACE_ISOLATION_NETWORK_AUDIT_2026-11-20.md) for the earlier shell-only threat model and residual risks; container Python support was added subsequently.
