# Independent audit: workspace-agent Isolated profile and network grants

**Date:** 2026-11-20  
**Repository / baseline:** `dovvnloading/Graphlink`, `main` at `06ecf193e20eac8d04d9a5b866cfa94965373edd`  
**Scope:** Workspace-agent task catalog, executable tool handlers, container CLI construction, task-scoped Web Research consent, and the shared `run_node` action. This report is a source-code security review and regression-test plan, not a penetration test of a running Docker daemon.

## Threat model and security contract

The trusted operator chooses a workspace and approves specific actions. The trusted Graphlink backend holds credentials and mediates tool grants. Model-generated code/commands, arguments, retrieved web data, imported graphs, and workspace files are untrusted. An Isolated shell command must not execute on the host when its container cannot start. It must receive only the mounted workspace, constrained resources and no container network. A workspace-agent network request must not be smuggled through a different tool or an unapproved destination.

Approval is an authorization decision, **not** a code sandbox, a guarantee a human read the disclosure, or a general outbound firewall. The Isolated profile is intended to isolate **shell execution**. It does not isolate backend provider requests, Web Research, scientific services or Fal integrations. See [execution profiles](WORKSPACE_ISOLATION.md), [security policy](../SECURITY.md), and [approval safety case](APPROVAL_REVIEW_SAFETY_CASE.md).

## Findings and remediation

| Severity | Finding before fix | Remediation in this PR |
| --- | --- | --- |
| **High** | `python.exec` remained a stateful **host** interpreter, exposed to Isolated agents via the shared `code.execute` grant. Agent-authored Python could read host-accessible files or make outbound requests independently of the container's `--network=none`. | Hide `python.exec` from the Isolated task catalog and refuse it in its handler even under a forged or stale catalog. Retire any persistent Host interpreter on transition to Isolated. Keep it available in Host; recommend container `shell.exec python` instead. |
| **High** | `python.install` was likewise offered to Isolated agents and could install into a host virtual environment and download PyPI wheels after its approval, regardless of the container network policy. | Hide the installer from Isolated and refuse it at the handler before preparing environments, installing packages or making outbound downloads. Host behavior retains fresh per-call approval. |
| **High** | `run_node` is registered under `graph.read` and dynamically checks `net.fetch` for a research action. Since the workspace run granted both, it could launch the ordinary Web Research pipeline (search and multiple site fetches) under a generic `run_node` approval, bypassing `web.search` / `web.fetch` **per-domain** approvals, including redirects. | Refuse `run_node` research for workspace-agent contexts, including the implicit `web_research` default action, before any network call. Direct agents to the approved web tools. Builder `run_node` behavior remains unchanged. |
| **Medium** | Container `--mount` was composed as one argv element, which avoids shell injection but **does not** prevent comma-separated mount-option parsing inside Docker/Podman. An unusual workspace directory containing commas could change interpretation of the mount fields. | Reject comma, CR and LF in the resolved mount source before invoking the container CLI; never fall back to a host shell. Normal paths, spaces and the fixed container target remain supported. |
| **Documentation** | The UI and machine snapshot described a network-disabled container but did not make the host Python and backend-network distinction prominent. | Clarify the execution scope, unavailable Python tools, backend web/provider traffic, limitations and startup requirements in the UI, machine-context prompt, and execution-profile documentation. |

### Existing controls confirmed (not newly introduced)

- Isolated `shell.exec` and `shell.session` construct Docker/Podman commands with `--network=none`, CPU/memory/process limits, `--read-only`, dropped caps, `no-new-privileges`, a bounded tmpfs, the workspace bind mount, no host-shell fallback, sanitized environment, and container cleanup.
- `web.search` and `web.fetch` are available only when the built-in Web Research plugin is enabled. They carry `net.fetch` and keep grants in the per-task `RunContext`, not persistent session grants.
- The search adapter already **explicitly pins** the third-party `ddgs` library to `backend="duckduckgo"`. The apparently broader `ddgs` default (`auto`) is *not* used; a regression assertion is added to keep this true. The approved `duckduckgo.com` name represents a provider family and its subdomains, not an enforced per-socket destination filter within the library.
- `web.fetch` checks public HTTPS destinations and applies a consent callback for each redirect before requesting that host, with bounded fetch/extraction and untrusted-content markings.

## Regression evidence and verification

The PR adds/extends tests asserting:

1. Isolated schemas omit host Python execution and installation while preserving container shell and scoped web tools; Host continues to offer Python; unrelated `net.fetch` tools stay excluded.
2. Forged direct Python tool calls in an Isolated context fail, with neither interpreter setup nor host installer invoked; existing REPL teardown is exercised.
3. A comma-containing workspace source is rejected before container launch; existing positive container-argv and no-host-fallback cases remain.
4. A workspace `run_node` research attempt is denied before calling Web Research, while the existing Builder research positive control remains.
5. DuckDuckGo search selects the explicitly pinned backend, preventing a future `auto` metasearch regression.

**Suggested focused verification (not run in this connector-only review):**

```sh
python -m pytest -q backend/tests/test_harness_isolation.py backend/tests/test_harness_web_tools.py backend/tests/test_harness_machine_context.py backend/tests/test_run_node_tool.py backend/tests/test_web_research_providers_coverage.py
python -m ruff check backend/harness/loop.py backend/harness/tools_python.py backend/harness/tools_python_install.py backend/harness/shell_isolation.py backend/harness/context.py backend/tests/test_harness_isolation.py backend/tests/test_run_node_tool.py backend/tests/test_web_research_providers_coverage.py backend/tools_graph.py backend/harness/tools_web.py
cd web_ui && npm run check
```

Tests and static checks were **not executed locally**: this audit used GitHub's repository API without a runnable checkout. Their status is unverified pending CI. Per the task instruction, CI is intentionally **not awaited** after the pull request is opened. A live Docker/Podman run, Windows-path behavior and a real approval-flow browser exercise remain necessary before claiming platform-wide security verification.

## Residual risk / non-goals

- **Not whole-process offline mode:** The backend still makes provider/model requests and can perform approved research and other separately permitted network actions (including Fal tools). `--network=none` does not govern those calls.
- **Not an egress firewall or DLP:** Once an operator approves a domain, further URLs to that domain within that task reuse consent. A model may place private content in a query or approved URL. The `ddgs` provider-family approval does not enforce a socket-level host allowlist; its dependency and backend selection remain trusted. Review search terms and approved destinations.
- **Container-engine trust:** Daemon/image integrity, isolation-runtime vulnerabilities, platform-specific rootless behavior, and write access to the deliberately shared workspace are outside this fix. Pre-pull and inspect the image; verify sandbox guarantees on each supported OS/engine.
- **Other application surfaces:** The dedicated Web Research node and Builder have separate authorization paths; this patch only closes the workspace-agent `run_node` bypass without changing those independent surfaces. This review is not a comprehensive audit of every backend network or file API.
- **Profile compatibility:** Old/invalid saved profile values still normalize to the existing Host default. Operators must explicitly select Isolated; the profile is not a guarantee against an attacker already able to tamper with application state or task settings.

**Release posture:** Request CI and maintainer review of the sensitive authorization boundary changes. Do not treat source inspection, test additions or this report as a substitute for a passing test suite or manual sandbox validation.
