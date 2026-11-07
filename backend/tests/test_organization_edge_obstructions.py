"""Regression tests for centreline edge/card obstruction measurements (#476).

These are measurement-only quality indicators, not hard routing guarantees.
"""

from backend.tests.perf.measure_organization import _segment_passes_through_rect


def test_crossing_card_interior_is_counted() -> None:
    rect = (10.0, 10.0, 20.0, 20.0)
    assert _segment_passes_through_rect((0.0, 20.0), (40.0, 20.0), rect)
    assert _segment_passes_through_rect((20.0, 0.0), (20.0, 40.0), rect)


def test_border_tangent_and_parallel_segments_are_not_obstructions() -> None:
    rect = (10.0, 10.0, 20.0, 20.0)
    assert not _segment_passes_through_rect((0.0, 10.0), (40.0, 10.0), rect)
    assert not _segment_passes_through_rect((0.0, 0.0), (10.0, 10.0), rect)
    assert not _segment_passes_through_rect((0.0, 5.0), (40.0, 5.0), rect)


def test_segment_ending_inside_card_counts() -> None:
    assert _segment_passes_through_rect(
        (0.0, 20.0), (15.0, 20.0), (10.0, 10.0, 20.0, 20.0)
    )


def test_zero_area_rectangles_do_not_count() -> None:
    assert not _segment_passes_through_rect(
        (0.0, 20.0), (40.0, 20.0), (10.0, 10.0, 0.0, 20.0)
    )
