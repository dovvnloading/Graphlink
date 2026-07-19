"""Coverage for the composer island's CSS token retrofit.

Two independent guarantees live here, and the distinction matters:

1. `styles.css` contains no hardcoded color literal (the "zero hardcoded hex"
   requirement itself).
2. Every `var(--gl-composer-*)` in `styles.css` resolves, for every theme, to
   exactly the literal that used to sit at that same site.

(1) alone would pass if every literal were replaced by the WRONG token - the
file would be clean and the app would be visibly broken. (2) is what actually
proves the retrofit was value-preserving, by textually re-substituting every
token back and comparing against a checked-in copy of the pre-retrofit file.
It is the direct analog of the QSS golden test in test_qss_generation.py.

Line endings: both the fixture and the live file are read in ordinary
universal-newline text mode, never with newline="". `.gitattributes` sets
`* text=auto`, so a fresh checkout on Windows can materialize either file with
CRLF; a byte-exact comparison would then fail for a reason that has nothing to
do with this retrofit. That exact bug already shipped once here and had to be
fixed in the QSS golden test - not repeating it.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import graphlink_styles as gs
from graphlink_styles import THEMES

_REPO_ROOT = Path(__file__).resolve().parents[2]
_STYLES = _REPO_ROOT / "web_ui" / "src" / "islands" / "composer" / "styles.css"
_FIXTURE = Path(__file__).parent / "fixtures" / "composer_styles_pre_retrofit.css"
_DEV_VARS = _REPO_ROOT / "web_ui" / "src" / "lib" / "tokens" / "gl-vars-dev.css"
_MAIN_TSX = _REPO_ROOT / "web_ui" / "src" / "islands" / "composer" / "main.tsx"

# Matches the color literals this retrofit removed. Deliberately the same two
# patterns used to capture them in the first place, so the "did we get them
# all" question is answered by the same definition that answered "what is
# there".
_COLOR_LITERAL_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)")
_VAR_RE = re.compile(r"var\((--gl-composer-[a-z0-9-]+)\)")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestNoHardcodedColors:
    def test_styles_css_contains_no_color_literal(self):
        found = _COLOR_LITERAL_RE.findall(_read(_STYLES))

        assert found == [], (
            f"{_STYLES.name} still contains hardcoded color literal(s): {found}. "
            "Every color in this island must come from a --gl-composer-* token."
        )

    def test_the_fixture_still_records_the_pre_retrofit_literals(self):
        # Guards the guard: if someone regenerated the fixture from the
        # retrofitted file, the golden test below would pass vacuously.
        found = _COLOR_LITERAL_RE.findall(_read(_FIXTURE))

        assert len(found) == 43, (
            f"The pre-retrofit fixture should hold exactly 43 color literals, found "
            f"{len(found)}. If this fixture was regenerated from the current "
            "styles.css, the resolution-golden test below is no longer proving "
            "anything - restore it from history instead."
        )


class TestResolutionGolden:
    """Substitute every token back and compare against the pre-retrofit file."""

    @pytest.mark.parametrize("theme_name", sorted(THEMES))
    def test_resolving_every_token_reproduces_the_original_file(self, theme_name):
        properties = gs.css_custom_properties(theme_name)
        styles = _read(_STYLES)

        def resolve(match: re.Match[str]) -> str:
            name = match.group(1)
            # A typo'd token name raises here rather than silently rendering as
            # an invalid computed value in the browser.
            return properties[name]

        resolved = _VAR_RE.sub(resolve, styles)

        assert resolved == _read(_FIXTURE), (
            f"Resolving styles.css against the {theme_name!r} theme does not "
            "reproduce the pre-retrofit file byte-for-byte."
        )

    def test_every_theme_resolves_identically_today(self):
        """The machine-checkable statement "composer is theme-invariant".

        This is expected to FAIL LOUDLY the day a real per-theme composer
        palette is authored - that is the point, not collateral damage. When it
        does, this assertion should be deliberately inverted, not deleted, and
        the per-theme resolution test above becomes the one that matters.
        """
        styles = _read(_STYLES)
        resolved = {}
        for theme_name in THEMES:
            properties = gs.css_custom_properties(theme_name)
            resolved[theme_name] = _VAR_RE.sub(
                lambda m: properties[m.group(1)], styles
            )

        assert len(set(resolved.values())) == 1, (
            "Composer no longer resolves identically across themes. If that is "
            "intentional (a real per-theme palette was authored), invert this "
            "assertion deliberately and record the design decision."
        )


class TestTokenSurface:
    def test_every_token_referenced_by_css_exists_in_every_theme(self):
        referenced = set(_VAR_RE.findall(_read(_STYLES)))

        assert referenced, "expected styles.css to reference --gl-composer-* tokens"
        for theme_name in THEMES:
            exported = set(gs.css_custom_properties(theme_name))
            missing = referenced - exported
            assert not missing, f"{theme_name}: styles.css references undefined {missing}"

    def test_every_island_token_is_actually_used_by_the_css(self):
        # The reverse direction: a token nobody references is dead weight that
        # a future reader would reasonably assume is load-bearing.
        referenced = set(_VAR_RE.findall(_read(_STYLES)))
        island = gs.island_property_names("dark")

        unused = island - referenced
        assert not unused, (
            f"These island tokens are exported but referenced nowhere in "
            f"styles.css: {sorted(unused)}"
        )

    def test_island_tokens_are_excluded_from_the_tailwind_theme_block(self):
        block = gs.tailwind_theme_css()

        assert "composer" not in block, (
            "Island-scoped tokens must not be registered as Tailwind design "
            "tokens - that would publish one island's private palette as a "
            "workspace-wide utility surface."
        )

    def test_island_token_names_are_computed_from_key_sets_not_prefix_matching(self):
        # A same-prefixed app-wide token must NOT be swallowed by the carve-out.
        names = gs.island_property_names("dark")

        assert "--gl-composer-shell-background" in names
        assert "--gl-composer-not-a-real-token" not in names

    @pytest.mark.parametrize("theme_name", sorted(THEMES))
    def test_the_two_composer_groups_have_disjoint_keys(self, theme_name):
        # They flatten under one shared prefix, so a collision would mean one
        # silently overwrites the other.
        flat = set(gs.THEME_TOKENS[theme_name]["composer"])
        alpha = set(gs.THEME_TOKENS[theme_name]["composer_alpha"])

        assert not (flat & alpha)

    def test_composer_groups_have_identical_keys_across_themes(self):
        # mono's qss group is genuinely 9 keys short of dark's, so this failure
        # mode is live in this table - guard the new groups against it.
        for group in ("composer", "composer_alpha"):
            key_sets = {t: set(gs.THEME_TOKENS[t][group]) for t in THEMES}
            assert len({frozenset(k) for k in key_sets.values()}) == 1, (
                f"{group} has differing keys across themes: "
                f"{ {t: sorted(k) for t, k in key_sets.items()} }"
            )


class TestDevServerVariables:
    """The dev path has no Python in the loop to inject :root values.

    Without a dev-only source for --gl-*, `npm run dev` renders the composer
    unstyled - and "npm run dev serves the composer in a browser" is a stated
    Phase 1 exit criterion, so this is a real regression, not a nicety.
    """

    def test_file_exists(self):
        assert _DEV_VARS.is_file(), f"{_DEV_VARS} is missing - regenerate from css_root_block('dark')"

    def test_checked_in_file_matches_regenerating_it_now(self):
        checked_in = _read(_DEV_VARS)
        block_start = checked_in.index(":root {")
        assert checked_in[block_start:] == gs.css_root_block("dark"), (
            f"{_DEV_VARS} is stale - regenerate it from "
            "graphlink_styles.py::css_root_block('dark')"
        )

    def test_it_defines_every_token_the_composer_css_references(self):
        referenced = set(_VAR_RE.findall(_read(_STYLES)))
        defined = set(re.findall(r"^\s*(--gl-[a-z0-9-]+):", _read(_DEV_VARS), re.MULTILINE))

        missing = referenced - defined
        assert not missing, f"gl-vars-dev.css does not define {missing}"

    def test_it_is_imported_only_behind_the_dev_flag(self):
        # An unconditional import would ship a hardcoded dark :root block that
        # lands after the host's injected block at equal specificity, silently
        # overriding the real theme and masking a failed injection.
        main_tsx = _read(_MAIN_TSX)

        assert "gl-vars-dev.css" in main_tsx, "main.tsx must import the dev variables"
        import_line_index = main_tsx.index("gl-vars-dev.css")
        guard_index = main_tsx.index("import.meta.env.DEV")
        assert guard_index < import_line_index, (
            "gl-vars-dev.css must be imported behind an import.meta.env.DEV guard "
            "so Vite eliminates it from the production bundle"
        )
