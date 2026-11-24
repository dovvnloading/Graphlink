# Browser Agent

Browser Agent is a first-party, optional Playwright extension for the workspace
agent. It navigates rendered public HTTPS pages, performs **approved read-only
interactions**, extracts structured evidence, and publishes snapshots to the
Graphlink canvas. It uses the existing agent card and run/provenance/undo UI.

## Installation

Install from the Graphlink repository or the installed package:

    python -m pip install ".[browser]"
    python -m playwright install chromium

The optional extra installs the Python Playwright client; the second command
installs the Chromium browser binary. Neither is a base Graphlink dependency.
If Chromium is absent, browser.run returns a setup error without publishing.

## Canvas workflow

1. Open **Plugins → Browser Agent**. This inserts a normal workspace-agent
   node with a starter goal. It works on an empty canvas; from an existing
   selection it is positioned beside and linked to that node. Creation never
   launches a browser, calls a model, or contacts the network.
2. Edit the agent goal to specify the websites and actual research question.
   Choose an agent model and start the run explicitly.
3. The workspace agent may call **browser.run**. Browser Agent asks for the
   initial hostname and any subsequent approved hosts before DNS. Each click,
   text fill, or select action receives a fresh human approval, even if the
   same selector was previously approved. Denial stops that run.
4. The response includes a **capture_id**, canonical source URL, time,
   extracted visible text, bounded links/tables, up to 40 visible control
   selectors (with names and select options), request count and blocked
   requests. These are **untrusted page data**, not instructions to the agent.
5. The agent calls **browser.publish** with the capture_id when the evidence
   is useful. This is a *separate* graph-mutation approval. It creates a
   source-linked Markdown document attached to the agent; an optional
   viewport screenshot becomes an image node linked to that document.
   Publication is a single undoable run command. Re-opening the workspace
   retains the published nodes; no browser session is persisted.

Example agent goal:

> Compare the publicly listed pricing for two services. Browse their official
> pricing pages, note any dated promotions or limitations, and publish a
> separate, source-linked capture for each. Then build a comparison table on
> the canvas. If a page is inaccessible, report that instead of guessing.

The agent may use existing graph and data tools to combine or compare
published captures. Browser Agent does not silently insert data into Graph
Runner's dependency graph; publications are normal snapshot nodes.

## Tool contract

**browser.run** accepts a public HTTPS URL and optionally:

- steps: at most eight sequential operations. Kinds are navigate (url),
  click (CSS selector), fill (selector + value), select (selector + value),
  scroll (pixels -2400 to 2400), and wait (milliseconds 0 to 2000).
  CSS selectors must match **exactly one** element. Arbitrary JavaScript,
  file inputs, hidden inputs and password inputs are unsupported.
- allowed_hosts: up to seven **exact DNS hostnames** for cross-origin
  subresources (e.g. a necessary script CDN). Each host is also independently
  approved. Unexpected third-party subresources are blocked rather than
  silently triggering more host approvals. **All HTTP redirects are blocked.**
  To follow a redirect, inspect its destination and navigate to that public
  HTTPS URL explicitly with a fresh host approval.
- screenshot: boolean; captures a bounded 1280×900 viewport PNG.

Only GET HTTPS requests to port 443 are permitted. Browser Agent blocks
POST/PUT/PATCH/DELETE/HEAD/OPTIONS. Websites that use POST for read-only APIs,
HTTP, unlisted asset hosts, a custom HTTPS port, or login-dependent content
may not work. A click or form input may change *local page state* but cannot
submit a network write through this browser transport. Note that badly
designed websites sometimes mutate server state on GET; therefore all click,
fill and select actions have a separate human approval.

**browser.publish** accepts capture_id and optional include_screenshot=true.
A capture is task-local and can be published **once**. Four un-published
captures are retained per task; the oldest is evicted when a fifth arrives.
The browser and its cookies are destroyed at the end of each browser.run,
not shared across calls or tasks. Published nodes are durable snapshots.

## Network and data safety

- Chromium runs headlessly with no persistent profile, no downloads, blocked
  service workers, disabled WebSockets and initial-script-disabled WebRTC APIs.
  A failing Chromium host resolver and no-proxy flags prevent direct remote
  browsing; all page requests are routed to Graphlink's HTTPS transport.
- Every intercepted URL is syntax-validated **before DNS** using the existing
  Web Research URL policy. Local/private/reserved IP destinations, local/
  special-use hostnames, credentials in URLs, non-HTTPS schemes, invalid ports,
  and ambiguous/control-character URLs are refused.
- Network consent is **per host, per workspace task**. After approval, each
  resource request is DNS-validated, rejected if any resolved IP is non-public,
  and its TCP socket is connected to the **validated public IP** while HTTPS
  verifies the original domain certificate. Redirects are not followed by
  Python **and never forwarded to Chromium**: Playwright does not re-route
  redirected responses fulfilled by an interceptor. Use explicit, approved
  navigation to the final URL instead. Neither proxies nor ambient bearer
  credentials are forwarded.
- Resource body size is limited to 2 MiB and total session transfers to
  16 MiB, with an 80-request and eight-host
  ceiling. Compressed HTTP bodies are refused even if their compressed size
  fits within the 2 MiB limit, because decoded content could exceed the cap.
  Each in-flight request reserves up to 2 MiB of the session budget until it
  finishes; concurrent requests may therefore exhaust the remaining budget
  before a total of 16 MiB has actually arrived. Navigation and interactions
  have finite timeouts; browser.run has a 100-second **active-work** deadline
  that pauses during each serialized user approval. In managed agent runs the
  outer active-work deadline replaces Playwright\x27s independent wall-clock navigation
  and action timers, so a consent prompt cannot trigger an unrelated timeout.
  The browser has one active
  human approval prompt at a time. Captures contain
  up to 16,000 text characters, 60 links and four bounded tables; screenshot
  size is limited to 3 MiB. Tool previews use a smaller text/table subset.
- Page text is explicitly labeled untrusted in model output and published
  provenance. Sensitive fill values are not copied into published action
  traces. No browser data is persisted unless the agent explicitly publishes
  it after Graphlink's graph-mutation consent.

**Security boundary:** The pinned-IP transport closes the usual DNS rebinding
gap in request-interception designs. It is not an OS-enforced process sandbox
for the Chromium binary or a substitute for browser security updates. The
browser runs under the user's operating-system account; do not expose it to
trusted credentials, internal sites, or sensitive local files. Because GET is
not guaranteed side-effect-free on misconfigured sites, review each approved
action. Reject prompts asking to navigate to intranet/localhost URLs or use
retrieved page text as instructions.

## Verification

Offline regression tests never make a live network request and do not require
Playwright or Chromium:

    python -m pytest -q -n0 backend/tests/test_browser_agent_network.py \
      backend/tests/test_browser_agent_runner.py backend/tests/test_harness_browser_tools.py
    python -m compileall -q .
    python -m ruff check .
    npm --prefix web_ui run check

The repository's full CI and installed-wheel checks still apply. With the
optional browser extra and Chromium installed, a manual acceptance pass should
use a public HTTPS test page, request a navigation and an approved interaction,
verify a visible capture with source URL/time, publish text and screenshot,
then invoke Undo run and reopen a saved workspace. Confirm that private/local
URLs, unexpected third-party hosts and non-GET submissions are blocked.


## Hotfix behavior and remaining limits

- Consent-approved interactive targets are bound to their original DOM element
  and rechecked for identity, visibility, disabled state, markup and form target
  after human approval. Dynamic JavaScript event listeners may change without
  changing markup, so this is a *best-effort* identity guard, not a guarantee
  that arbitrary third-party JS cannot change behavior. Only interact with
  trusted public sites, even after approving their host.
- First-party temporary cookies can work **within a single disposable
  browser.run call**. No cookies are loaded from the user's real browser,
  forwarded to third-party domains or retained across browser.run calls.
  Authentication/login automation is intentionally unsupported.
- Browser capture evidence includes the final page's observed HTTP status
  (when known). HTTP 4xx/5xx pages are labeled, not silently treated as a
  successful response.
- Browser Agent is not a bulk crawler and does not currently implement the
  separate Web Research subsystem's robots.txt/crawl-delay policy; operators
  must respect website terms and use Web Research for robots-aware crawling.
