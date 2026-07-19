"""Golden-diff coverage for the QSS-generation step of the Phase 1 theme
token work: the three StyleSheet.*_THEME strings are now generated from
THEME_TOKENS[theme]["qss"] / ["qss_alpha"] filled into a *_THEME_TEMPLATE,
instead of being hand-maintained literals.

The fixtures under tests/fixtures/qss_golden_*.txt are copies of
StyleSheet.DARK_THEME / MONOCHROMATIC_THEME / MUTED_THEME captured from the
running app BEFORE this refactor (via a script, not retyped by hand) - this
is the "generated == current, diffs reviewed" golden test called for in the
master plan's Phase 1 checklist and section 3.4. Compared as text (universal
newlines), not raw bytes: repo-root .gitattributes forces `*.txt eol=crlf`,
so a fresh checkout is free to rewrite this file's line endings regardless
of what was committed, while the generated StyleSheet.* strings are always
\n-only (Python normalizes source line endings when parsing a string
literal). A future edit that silently changes a resolved color still fails
this test with an actual text diff, not just a bespoke hash comparison.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import graphlink_styles as gs
from graphlink_paths import asset_url

FIXTURES = Path(__file__).resolve().parent / "fixtures"

GOLDEN_FILES = {
    "dark": ("DARK_THEME", "qss_golden_dark.txt"),
    "mono": ("MONOCHROMATIC_THEME", "qss_golden_mono.txt"),
    "muted": ("MUTED_THEME", "qss_golden_muted.txt"),
}

HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
RGBA_RE = re.compile(r"^rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*[\d.]+\s*\)$")
RGBA_SCAN_RE = re.compile(r"rgba\([^)]*\)")
PLACEHOLDER_RE = re.compile(r"\{\{([a-z0-9_]+)\}\}")


def _read_fixture(filename):
    # Universal-newline text mode (the default - no newline= override), NOT
    # newline="" byte-exact mode: repo-root .gitattributes forces `*.txt
    # text eol=crlf`, so a checkout can legitimately materialize this file
    # with \r\n regardless of what was written or committed. StyleSheet.
    # DARK_THEME etc. are always \n-only (Python's tokenizer normalizes
    # source line endings when parsing a string literal, regardless of the
    # .py file's own on-disk line endings). Comparing raw bytes against a
    # fixture whose line endings git is free to rewrite was the actual bug
    # here, caught when this test failed after nothing but a routine
    # `git checkout`/`git merge --ff-only` re-materialized the fixture as
    # CRLF. Reading in text mode normalizes \r\n -> \n on the way in, so the
    # comparison is diff-worthy on real content changes and indifferent to
    # a line-ending convention neither side of the comparison controls.
    with open(FIXTURES / filename, "r", encoding="utf-8") as f:
        return f.read()


class TestGeneratedQssMatchesGoldenByteForByte:
    @pytest.mark.parametrize("theme_name,attr,fixture", [(t, a, f) for t, (a, f) in GOLDEN_FILES.items()])
    def test_matches_pre_refactor_golden(self, theme_name, attr, fixture):
        golden = _read_fixture(fixture)
        actual = getattr(gs.StyleSheet, attr)
        assert actual == golden, (
            f"StyleSheet.{attr} no longer matches the pre-refactor golden baseline "
            f"(tests/fixtures/{fixture}). If this change is intentional, capture a new "
            f"golden baseline from the running app (not retyped by hand) and review the diff."
        )


class TestThemesDictStylesheetWiringUnaffected:
    def test_mono_and_muted_have_no_asset_substitution(self):
        # Only DARK_THEME contains the __ASSET_DOWN_ARROW__ placeholder;
        # THEMES["mono"/"muted"]["stylesheet"] should be the generated
        # string verbatim.
        assert gs.THEMES["mono"]["stylesheet"] == gs.StyleSheet.MONOCHROMATIC_THEME
        assert gs.THEMES["muted"]["stylesheet"] == gs.StyleSheet.MUTED_THEME

    def test_dark_theme_asset_url_substitution_still_applied(self):
        expected = gs.StyleSheet.DARK_THEME.replace("__ASSET_DOWN_ARROW__", asset_url("down_arrow.png"))
        assert gs.THEMES["dark"]["stylesheet"] == expected
        assert "__ASSET_DOWN_ARROW__" not in gs.THEMES["dark"]["stylesheet"]


class TestQssTokenGroupsWellFormed:
    @pytest.mark.parametrize("theme_name", ["dark", "mono", "muted"])
    def test_qss_group_is_all_flat_hex(self, theme_name):
        qss = gs.THEME_TOKENS[theme_name]["qss"]
        assert len(qss) > 0
        for key, value in qss.items():
            assert HEX_COLOR_RE.match(value), f"{theme_name}.qss.{key} = {value!r} is not a flat #RRGGBB hex color"

    @pytest.mark.parametrize("theme_name", ["dark", "mono", "muted"])
    def test_qss_alpha_group_is_all_rgba(self, theme_name):
        qss_alpha = gs.THEME_TOKENS[theme_name]["qss_alpha"]
        assert len(qss_alpha) > 0
        for key, value in qss_alpha.items():
            assert RGBA_RE.match(value), f"{theme_name}.qss_alpha.{key} = {value!r} is not an rgba(...) literal"

    @pytest.mark.parametrize("theme_name", ["dark", "mono", "muted"])
    def test_qss_and_qss_alpha_keys_are_disjoint(self, theme_name):
        tokens = gs.THEME_TOKENS[theme_name]
        assert set(tokens["qss"].keys()).isdisjoint(tokens["qss_alpha"].keys())

    # Full THEME_TOKENS group-shape coverage (all six groups, every theme)
    # lives in test_theme_tokens.py::TestThemeTokensStructure - not
    # duplicated here to avoid two suites asserting the same shape.


class TestGeneratorSubstitutesEveryPlaceholder:
    """A token present in a *_THEME_TEMPLATE but missing from THEME_TOKENS
    would leave a literal {{name}} in the generated QSS instead of raising -
    str.replace() is silent on a no-op. These tests catch that class of bug
    directly, independent of the golden-diff test above."""

    @pytest.mark.parametrize("theme_name,attr", [(t, a) for t, (a, _) in GOLDEN_FILES.items()])
    def test_no_unresolved_placeholders_remain(self, theme_name, attr):
        generated = getattr(gs.StyleSheet, attr)
        leftovers = PLACEHOLDER_RE.findall(generated)
        assert leftovers == [], f"StyleSheet.{attr} has unresolved template placeholders: {leftovers}"

    @pytest.mark.parametrize("theme_name", ["dark", "mono", "muted"])
    def test_generate_qss_is_deterministic(self, theme_name):
        assert gs._generate_qss(theme_name) == gs._generate_qss(theme_name)

    def test_generate_qss_raises_on_unknown_theme(self):
        with pytest.raises(KeyError):
            gs._generate_qss("some_theme_that_does_not_exist")


class TestTemplateAttributesArePureTemplates:
    """The *_THEME_TEMPLATE class attributes should contain no resolved
    color literal left over from before this refactor - every color must
    route through a {{token}} placeholder now."""

    HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}\b")

    @pytest.mark.parametrize("attr", ["DARK_THEME_TEMPLATE", "MONOCHROMATIC_THEME_TEMPLATE", "MUTED_THEME_TEMPLATE"])
    def test_template_has_no_bare_hex_literal(self, attr):
        template = getattr(gs.StyleSheet, attr)
        assert self.HEX_RE.findall(template) == [], f"StyleSheet.{attr} still has a bare (non-templated) hex color"

    @pytest.mark.parametrize("attr", ["DARK_THEME_TEMPLATE", "MONOCHROMATIC_THEME_TEMPLATE", "MUTED_THEME_TEMPLATE"])
    def test_template_has_no_bare_rgba_literal(self, attr):
        # A future edit adding a new hover/pressed alpha rule directly to a
        # template (instead of routing it through qss_alpha) would be
        # invisible to test_template_has_no_bare_hex_literal above, since
        # rgba(...) isn't hex - catch that value shape separately.
        template = getattr(gs.StyleSheet, attr)
        assert RGBA_SCAN_RE.findall(template) == [], f"StyleSheet.{attr} still has a bare (non-templated) rgba(...) color"


class TestNoOrphanQssTokens:
    """Every key in THEME_TOKENS[theme]["qss"/"qss_alpha"] should actually be
    referenced by that theme's template - an orphaned entry (e.g. left behind
    by a template edit that dropped the {{placeholder}} but not the token
    table row) would sit silently unused rather than fail anything else here."""

    @pytest.mark.parametrize("theme_name,attr", [(t, a) for t, (a, _) in GOLDEN_FILES.items()])
    def test_every_qss_and_qss_alpha_key_is_referenced_in_its_template(self, theme_name, attr):
        template = getattr(gs.StyleSheet, f"{attr}_TEMPLATE")
        referenced = set(PLACEHOLDER_RE.findall(template))
        tokens = gs.THEME_TOKENS[theme_name]
        declared = set(tokens["qss"].keys()) | set(tokens["qss_alpha"].keys())
        orphans = declared - referenced
        assert orphans == set(), f"THEME_TOKENS[{theme_name!r}] has qss/qss_alpha keys never referenced by {attr}_TEMPLATE: {orphans}"
