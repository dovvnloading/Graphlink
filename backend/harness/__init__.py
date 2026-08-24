"""The agent harness (PLAN-2026-08-24): a bounded, tool-using turn loop
bound to a per-node scratch workspace. H1 scope: the loop, the append-only
transcript, and read-only fs tools - mutating tools, approvals-in-loop,
compaction, and subagents land in later phases.
"""
