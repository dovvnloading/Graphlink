"""Regression test for doc/ARCHITECTURE_REVIEW_FINDINGS.md #19: large-paste and
dropped-text attachments were written to temp files with errors="ignore", which
silently drops any character Python can't encode to UTF-8 (e.g. an unpaired surrogate
from a malformed Windows clipboard/drop payload) with no trace in the saved attachment.
errors="replace" substitutes a visible replacement character instead, so corruption is
observable rather than silent.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graphlink_window


def test_temp_attachment_writers_do_not_silently_drop_unencodable_characters():
    source = Path(graphlink_window.__file__).read_text(encoding="utf-8")
    assert 'errors="ignore"' not in source, (
        "errors=\"ignore\" reappeared in graphlink_window.py's temp-attachment writers - "
        "this silently drops unencodable characters instead of surfacing them. Use "
        "errors=\"replace\" so corruption is visible in the saved attachment."
    )


def test_replace_semantics_surface_corruption_instead_of_hiding_it():
    # Documents *why* errors="replace" is the right choice - not a test of app code,
    # since exercising the real staging pipeline needs a full ChatWindow instance.
    #
    # Note: on the *encode* side (str -> bytes, which is what `open(..., "w").write()`
    # does), Python's "replace" handler substitutes a literal "?" byte, not U+FFFD
    # (U+FFFD is what "replace" produces on *decode*, bytes -> str). Either way the
    # substitution is what matters here: something visible survives in the file where
    # "ignore" would have left nothing at all.
    text_with_unpaired_surrogate = "before" + chr(0xD800) + "after"

    silently_dropped = text_with_unpaired_surrogate.encode("utf-8", errors="ignore")
    assert silently_dropped == b"beforeafter"  # the bad character vanishes with no trace

    visibly_replaced = text_with_unpaired_surrogate.encode("utf-8", errors="replace")
    assert visibly_replaced == b"before?after"  # the bad character is visibly marked instead
    assert len(visibly_replaced) > len(silently_dropped)
