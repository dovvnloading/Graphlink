"""Shared, dependency-light infrastructure used by more than one plugin.

Phase 3 of doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md: code that was independently
duplicated across plugin files (see section 1.6) is extracted here so a fix in one
place reaches every plugin that uses it, instead of drifting apart.
"""
