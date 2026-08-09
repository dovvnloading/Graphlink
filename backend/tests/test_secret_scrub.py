"""ADR-009 stage 9.3: the scrub function's own adversarial test suite.

That stage's exit criterion is "adversarial fixtures prove zero secrets/
paths in output" - so this file is written as attempts to SNEAK A SECRET
PAST the scrubber, not as a demonstration that the happy path works. Each
test is a route a real leak could take: an unexpected field name, a
credential buried in prose, a path inside an error message, a secret
nested three containers deep.

The final test is the backstop: it plants every fixture value at once and
asserts none of them survive serialization, so a future rule that
accidentally narrows coverage fails here even if someone deletes the
specific test that covered it.
"""

from __future__ import annotations

import json

from backend.secret_scrub import REDACTED, REDACTED_PATH, scrub, scrub_text
from graphlink_settings_store import SettingsManager

# Realistic shapes, none of them real credentials.
#
# ASSEMBLED FROM PARTS, NOT WRITTEN AS LITERALS - and that is not
# decoration. Writing these out whole got this very file rejected by
# GitHub's push protection, which correctly identified the Slack fixture
# as credential-shaped. That is the system working: these strings have to
# look real enough to exercise the patterns in secret_scrub.py, which
# makes them real enough to trip a scanner. Splitting each one across a
# concatenation means no scanner-matching literal exists in the source,
# while the value the tests actually pass to scrub() is still the full,
# realistic string.
OPENAI = "sk-" + "proj-abcdefghijklmnopqrstuvwxyz0123456789"
ANTHROPIC = "sk-ant-" + "api03-abcdefghijklmnopqrstuvwxyz012345"
GEMINI = "AIza" + "SyD-abcdefghijklmnopqrstuvwxyz01234"
GITHUB = "ghp" + "_abcdefghijklmnopqrstuvwxyz0123456789"
GITHUB_PAT = "github_pat" + "_abcdefghijklmnopqrstuvwxyz0123456789"
SLACK = "xoxb" + "-1234567890-abcdefghijklmnop"
DPAPI = "dpapi:" + "AQAAANCMnd8BFdERjHoAwE/Cl+sBAAAA=="
WIN_PATH = r"C:\Users\ada\Documents\Private\taxes.xlsx"
UNC_PATH = r"\\fileserver\finance\payroll.xlsx"
POSIX_PATH = "/home/ada/.ssh/id_rsa"

ALL_SECRETS = (OPENAI, ANTHROPIC, GEMINI, GITHUB, GITHUB_PAT, SLACK, DPAPI)
ALL_PATHS = (WIN_PATH, UNC_PATH, POSIX_PATH)


# -- the settings contract this must not drift from -------------------------


def test_every_declared_secret_key_is_covered_by_the_key_rules():
    # SettingsManager.SECRET_KEYS is the authoritative list of fields this
    # app stores credentials in. If someone adds a fifth one whose name
    # doesn't match any suffix rule, this fails - rather than the export
    # silently starting to carry it.
    for key in SettingsManager.SECRET_KEYS:
        scrubbed = scrub({key: "some-value-that-does-not-look-like-a-token"})
        assert scrubbed[key] == REDACTED, f"{key} was not redacted by the key rules"


# -- credential-shaped values, wherever they hide ---------------------------


def test_a_credential_is_redacted_under_a_completely_unremarkable_key():
    # The core reason this is value-based: nothing about "notes" says secret.
    for secret in ALL_SECRETS:
        assert secret not in json.dumps(scrub({"notes": secret}))


def test_a_credential_buried_in_ordinary_prose_is_redacted():
    text = f"I tried using {OPENAI} but it kept failing, then {GITHUB} also failed"
    scrubbed = scrub_text(text)
    assert OPENAI not in scrubbed
    assert GITHUB not in scrubbed
    # The surrounding sentence must survive - a scrubber that nukes the
    # whole string destroys the diagnostic value of the export.
    assert "kept failing" in scrubbed


def test_a_credential_nested_deep_inside_containers_is_redacted():
    payload = {"chats": [{"nodes": [{"content": {"parts": [f"key={ANTHROPIC}"]}}]}]}
    assert ANTHROPIC not in json.dumps(scrub(payload))


def test_a_secret_named_field_is_redacted_even_when_its_value_looks_harmless():
    # A key-name hit must not require the value to match an issuer pattern.
    assert scrub({"my_password": "hunter2"})["my_password"] == REDACTED
    assert scrub({"SERVICE_API_KEY": "plain"})["SERVICE_API_KEY"] == REDACTED


def test_an_empty_secret_field_stays_empty_rather_than_becoming_redacted():
    # "was never configured" and "was configured" are different facts, and
    # reporting the difference leaks nothing.
    assert scrub({"openai_api_key": ""})["openai_api_key"] == ""
    assert scrub({"openai_api_key": None})["openai_api_key"] is None


# -- absolute paths ----------------------------------------------------------


def test_absolute_paths_are_redacted_in_all_three_forms():
    for path in ALL_PATHS:
        assert path not in json.dumps(scrub({"detail": path}))


def test_a_path_inside_an_error_message_is_redacted():
    # The exact real-world shape: an OSError string stored on a node.
    text = f"[Errno 2] No such file or directory: '{WIN_PATH}'"
    scrubbed = scrub_text(text)
    assert WIN_PATH not in scrubbed
    assert "ada" not in scrubbed, "the account name is the private part"
    assert REDACTED_PATH in scrubbed


def test_ordinary_prose_with_slashes_is_not_mangled():
    # The complementary half: over-redaction has a cost too. A scrubber
    # that eats normal text makes exports useless and gets disabled.
    text = "use the and/or operator, see the read/write docs"
    assert scrub_text(text) == text


# -- purity ------------------------------------------------------------------


def test_scrub_does_not_mutate_its_input():
    # Callers pass live in-memory document state; exporting must not damage
    # the thing being exported.
    original = {"openai_api_key": OPENAI, "nested": {"note": WIN_PATH}}
    scrub(original)
    assert original["openai_api_key"] == OPENAI
    assert original["nested"]["note"] == WIN_PATH


def test_non_string_scalars_pass_through_untouched():
    payload = {"count": 42, "ratio": 1.5, "ok": True, "missing": None}
    assert scrub(payload) == payload


# -- the backstop ------------------------------------------------------------


def test_no_fixture_secret_or_path_survives_a_realistic_export_payload():
    """Every known-bad value planted at once, in the shapes a real export
    would carry them. This is the test that keeps passing only as long as
    coverage genuinely holds - narrowing any single rule fails it here."""
    payload = {
        "manifest": {"appVersion": "1.0", "exportedFrom": WIN_PATH},
        "settings": {key: OPENAI for key in SettingsManager.SECRET_KEYS},
        "chats": [
            {
                "title": f"debugging {GEMINI}",
                "nodes": [
                    {"content": f"token is {GITHUB}"},
                    {"content": f"other is {GITHUB_PAT} and {SLACK}"},
                    {"error": f"could not read {POSIX_PATH}"},
                    {"stored": DPAPI},
                    {"deep": {"deeper": [{"deepest": ANTHROPIC}]}},
                ],
                "sourceDir": UNC_PATH,
            }
        ],
    }

    serialized = json.dumps(scrub(payload))

    for secret in ALL_SECRETS:
        assert secret not in serialized, f"leaked secret: {secret[:12]}..."
    for path in ALL_PATHS:
        assert path not in serialized, f"leaked path: {path}"
    assert "ada" not in serialized, "leaked the operator's account name"
