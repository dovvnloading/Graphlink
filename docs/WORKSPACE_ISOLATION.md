# Workspace agent execution profiles

Open an Agent node's **Agent settings** and select **Execution profile**.

- **Host** (default): `shell.exec` and `shell.session` use the selected host shell and the existing execution guard.
- **Isolated**: both tools run using Docker (preferred when both CLIs are present) or Podman. The currently bound trusted/scratch workspace is mounted read/write at `/workspace`. The container uses `python:3.12-slim`, `/bin/sh`, **no network by default** (`--network=none`), **1 CPU**, **512 MiB RAM and swap**, a 128-process cap, no Linux capabilities, no-new-privileges, a read-only image filesystem and a 64 MiB temporary filesystem. Only the workspace is bind-mounted; the container does not inherit backend secrets or the host shell. `python.exec` and other tools are **not** containerized.

Install [Docker Desktop](https://docs.docker.com/desktop/) (Windows/macOS), [Docker Engine](https://docs.docker.com/engine/install/) (Linux), or [Podman](https://podman.io/docs/installation). Start the daemon/service where required. Pull the image *before* using Isolated mode, for example:

```sh
docker pull python:3.12-slim
# or: podman pull python:3.12-slim
```

### Network approval for a command

Isolated `shell.exec` commands remain offline by default. When the agent needs network access for **one command**, it must request it explicitly with `network: true` and a nonblank `network_reason`, e.g. `{"command":"python -m pip index versions requests","network":true,"network_reason":"Check the available package versions"}`. The command-approval panel shows the complete command **and why network is needed**, with distinct choices:

- **Allow network once** — enable the container's bridge network only for that approved command; the next command is offline again unless separately approved.
- **Allow network for this task** — enable the bridge network for that command and subsequent `shell.exec` commands in the *current* task. This grant disappears when the task ends; it is not a session-wide permission.
- **Deny** — do not run the proposed command.

The agent node's **Agent settings → Allow network for every task** checkbox is an explicit, **off-by-default** persistent opt-in. It is available only in the Isolated profile and applies to future tasks; each `shell.exec` command still needs ordinary command approval. Changes to the setting during a running task cannot silently expand that task's permissions. `shell.session` remains network-disabled in Isolated mode. Network-enabled containers use bridge networking, **not** host networking; all other container resource, filesystem and privilege restrictions remain in force.

The image is deliberately **not** downloaded during an agent command (`--pull=never`); if it is missing, the engine reports an error. Shell tools never fall back to Host when Isolated is selected. Commands retain per-call approval and timeout/cancellation behavior. Long-running sessions are forcibly retired when a node switches its workspace or execution profile, and force-removed from the engine on stop or app teardown.

The preference is saved with the graph and snapshotted at the start of each task. Changing it while a task runs only affects subsequent tasks. Installing a CLI is not proof the engine/daemon is running; use its normal diagnostics if a container fails to start.
