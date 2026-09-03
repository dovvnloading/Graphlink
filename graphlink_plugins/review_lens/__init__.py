"""Qt-free Review Lens helper package.

The domain logic behind the Review Lens node (the guided PR
reviewer): PR URL parsing, PR diff fetching over the shared GitHub REST
client, and the review engine (deterministic rubric + severity-tiered
findings, ported from the retired single-file Code Review plugin's own
scoring engine and extended to multi-file PR diffs with a guided
walkthrough).

Split out so all of it is directly unit-testable without any backend
session, provider state, or UI - the same reason graphlink_plugins/gitlink/
exists as its own package. Nothing here imports backend/, api_provider
state, or any widget toolkit; the LLM call goes through api_provider.chat
(the same module-level call GitlinkAgent already makes), which the test
suite monkeypatches.
"""
