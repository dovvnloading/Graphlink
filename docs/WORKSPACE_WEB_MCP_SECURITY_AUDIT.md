# Independent security audit: workspace web tools and MCP task control

Date: 2026-11-20  
Scope: `backend/harness/tools_web.py`, `graphlink_plugins/web_research/{providers,fetch_policy}.py`, the harness run/approval pipeline, `backend/control_agent_tasks.py`, `backend/mcp_server.py`, and focused tests.

## Trust boundaries reviewed

* **Agent → Web Research:** The model chooses search terms and URLs. Remote search snippets, result titles, fetched page text, and redirect locations are attacker-controlled data. Network requests require `net.fetch` and task-local host consent; the fetcher must pin a *public* IP per hop, disallow proxy credential inheritance and block implicit redirects.
* **Agent → app approvals:** The actual Graphlink app owns pending approval IDs, human approval/denial, folder picks, and session-level tool grants. An MCP write token is **not** an authorization to approve a tool or silently reuse prior UI approvals.
* **MCP → live canvas:** MCP's explicit write-token capability allows only scoped start, status, cancel and revisioned canvas operations. Returned agent replies and checklists may contain untrusted model/web/file-authored prose.

## Findings and remediation

| ID / severity | Finding (pre-fix) | Change |
| --- | --- | --- |
| WEB-01 / High | Each `web.fetch` hop resolved its hostname **before** asking the user to approve the host. A refused initial URL or cross-site redirect still caused a DNS lookup; a malicious hostname can encode secrets in DNS labels. | Added DNS-free URL/literal-IP validation before consent, followed by full public-address validation and IP-pinned transport **after** consent. Applied on every redirect, before robots.txt or page traffic. |
| WEB-02 / High | `web.search` prompted for `duckduckgo.com`, but `ddgs.text()` defaulted to an automatic multi-provider backend and could contact other search engines without consent. | Explicitly select `backend="duckduckgo"` with no automatic fallback. Errors fail the search, not the approval boundary. |
| MCP-01 / High | `graphlink_start_agent_task` reused the app's normal follow-up run path, which attaches previously remembered `approve for session` tool grants. An MCP client could start a task that used those grants without a new per-call approval. | Threaded an internal `allow_session_grants=False` for MCP starts through the validated harness send/dispatcher/run, setting this run's grants to `None`. The UI still retains its grants; the MCP task cannot consume or persist them. The app no longer advertises session approval for this run. |
| WEB-03 / Medium | URL canonicalization relied on parser cleanup and could silently remove control characters, accept empty userinfo, or raise on malformed Unicode authority. These can make the approved URL differ from the attempted one or produce unhandled errors. | Reject C0/DEL controls, ambiguous backslashes in authority, empty userinfo, malformed IPv6/NFKC hosts, and invalid ports without contacting the network. |
| MCP-02 / Defense in depth | `graphlink_get_agent_task` returned unqualified model-authored reply/checklist text to upstream MCP clients, potentially laundering instructions copied from hostile web/file content. | Mark its text response `UNTRUSTED WORKSPACE AGENT OUTPUT` and explain that embedded text is data, not instructions or approvals. Preserve structured JSON compatibility. |

## Safeguards verified in the existing implementation

* Workspace tools are registered only if Web Research is loaded and require `net.fetch`; non-harness runs cannot invoke them.
* Fetches use HTTPS, independent public-IP checks, an IP-pinned TLS connection, no ambient HTTP proxy, no automatic HTTP redirects, robots gating, bounded bytes/timeouts, and cancellation.
* Web search/fetch responses already label page content as untrusted, and the harness system prompt tells the model not to follow instructions from files or tool results.
* Domain approvals are task-local, distinct per host, and bound to fresh approval IDs on the app's approval panel. Denials do not populate the approved-host cache.
* MCP exposes no approval/answer/folder-grant endpoint. The HTTP adapter is loopback-only, refuses redirects, and uses a separate write credential. Start is revision- and operation-ID-guarded; cancel targets a specific run; status does not disclose approval tokens.

## Regression coverage

Added/extended **offline** tests in:

* `test_web_research_fetch_policy.py`: DNS-free pre-consent syntax checks; private literals; ambiguous URLs; post-consent private DNS answers still rejected.
* `test_web_research_providers_fetcher.py`: denied initial and redirect domains provoke no DNS resolution or HTTP traffic to the rejected host; private literals never reach consent; an explicit `False` callback decision also denies.
* `test_web_research_providers_coverage.py`: `ddgs` must be called with the explicitly approved backend.
* `test_harness_web_tools.py`: ambiguous/credentialed URLs cannot elicit approval or fetches.
* `test_control_agent_tasks.py`: MCP start passes `allow_session_grants=False`; requests cannot set the flag; earlier UI grants remain intact for app use.
* `test_mcp_server.py`: status output remains visibly untrusted; forged approval calls still fail.

Suggested focused command (run from repo root):  
`pytest -q backend/tests/test_web_research_fetch_policy.py backend/tests/test_web_research_providers_fetcher.py backend/tests/test_web_research_providers_coverage.py backend/tests/test_harness_web_tools.py backend/tests/test_control_agent_tasks.py backend/tests/test_mcp_server.py`

**Verification limit:** This change was authored and reviewed through the connected GitHub repository, without a local checkout capable of executing the repository's suite in this environment. Regression tests are committed, not claimed as executed. CI should validate them; the PR was opened without waiting for CI, per request.

## Remaining risks / assumptions

* Labeling web and MCP text **reduces authority confusion; it cannot guarantee an LLM will resist every prompt injection**. Upstream MCP clients must likewise treat `structuredContent` and text fields as untrusted and require their own user approval for consequential actions.
* An approved host receives additional requests for that task without a new prompt; users should treat domain consent as permission to send subsequent search terms/paths to that host. The design does **not** implement per-query/per-path approval.
* `ddgs` is a third-party search client: pinning its backend prevents documented automatic engine selection but cannot prove every indirect infrastructure endpoint without controlled runtime network tests. The native Web Research node's search flow shares the provider.
* These changes do not sandbox the agent's local shell/Python execution, and the MCP task-control write token still allows starting arbitrary **user-text tasks** within the already configured workspace. Host access and prompt-injection-resistant tool behavior remain necessary.
