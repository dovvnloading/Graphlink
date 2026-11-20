# Workspace agent execution profiles

Open an Agent node's **Agent settings** and select **Execution profile**.

- **Host** (default): `shell.exec`, `shell.session`, `python.exec` and `python.install` use the host's execution environment under their existing approval, subprocess-environment and execution-guard policies. The persistent Python REPL and its package installer are **not sandboxes**.
- **Isolated**: `shell.exec` and `shell.session` run in Docker (preferred when both CLIs are present) or Podman. The currently bound trusted/scratch workspace is mounted read/write at `/workspace`. The fixed `python:3.12-slim` image runs `/bin/sh` with **no container network** (`--network=none`), **1 CPU**, **512 MiB RAM and swap**, 128 processes, dropped Linux capabilities, no-new-privileges, a read-only image filesystem and a 64 MiB temporary filesystem. Only the workspace is bind-mounted; host executables and backend secrets are not passed into the container.
- **No host Python escape hatch**: `python.exec` and `python.install` are **not offered** in Isolated. A direct/forged call also fails closed rather than creating a host interpreter or downloading packages. A persistent REPL left from a prior Host task is stopped on entry to Isolated. To run Python in Isolated, use `shell.exec` with the container's `python`. That is **not** a persistent REPL, and the container has no network for `pip` downloads.

## Network grants are separate from container networking

The Isolated setting does **not** turn the Graphlink backend into an offline process. Model/provider requests and other explicit backend services can use the host's network. The `--network=none` rule applies **only** to commands running inside the shell container.

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

The image is deliberately **not** downloaded during an agent command (`--pull=never`). If the CLI/daemon/image is unavailable, shell execution reports an error and **never falls back to Host**. Workspace paths containing commas or newlines are rejected in Isolated because the container CLI's `--mount` option parses comma-separated fields even when constructed as one argv element.

Shell commands retain per-call approval and timeout/cancellation behavior. Long-running sessions are retired when a node switches workspace or profile, and removed from the container engine on stop or app teardown. The preference is saved with the graph and snapshotted per task; changing it during a task affects subsequent tasks. An installed CLI is not proof that its daemon is running.

This is a **shell-execution isolation profile**, not whole-application confinement, a guarantee against malicious Docker/Podman daemons or images, or a claim that approved network tools cannot exfiltrate data. See [the independent security audit](WORKSPACE_ISOLATION_NETWORK_AUDIT_2026-11-20.md) for the threat model, changes, tests, and residual risks.
