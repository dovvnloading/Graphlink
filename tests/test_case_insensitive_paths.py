"""Protect Windows builds from Git-tracked case-only filename collisions."""

from tools.check_case_collisions import case_collisions, tracked_paths


def test_case_only_paths_and_windows_style_separators_are_detected():
    assert case_collisions([
        "web_ui/src/ScientificResultViews.tsx",
        "web_ui/src/scientificresultviews.tsx",
        "web_ui\\src\\scientificresultviews.TSX",
        "web_ui/src/unrelated.ts",
    ]) == [
        (
            "web_ui/src/ScientificResultViews.tsx",
            "web_ui/src/scientificresultviews.tsx",
            "web_ui\\src\\scientificresultviews.TSX",
        ),
    ]


def test_repository_index_contains_no_case_insensitive_collisions():
    conflicts = case_collisions(tracked_paths())
    assert not conflicts, f"Case-insensitive checkout conflicts: {conflicts}"
