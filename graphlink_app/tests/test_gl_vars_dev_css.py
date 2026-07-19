"""Staleness coverage for web_ui/src/lib/tokens/gl-vars-dev.css.

IMPORTANT, AND THE REASON THIS LIVES IN ITS OWN FILE: this generated file
mirrors css_root_block("dark") in full - EVERY dark-theme custom property, not
just the island tokens whose retrofit first required it. So editing any dark
value anywhere in THEME_TOKENS (a graph-node gradient, a semantic status color,
a frame preset, the font family) makes this file stale and fails the test
below. That is correct behavior, but it means the failure has to be named for
what it actually guards. It previously sat in test_composer_token_retrofit.py,
where an unrelated palette tweak would fail a test whose name pointed at the
wrong subsystem entirely.

This is a real Python-to-frontend coupling introduced by the dev-server fix,
and it is the price of the dev server rendering correctly at all: `npm run dev`
is plain vite with no Python in the loop, so nothing else can supply values for
the var(--gl-*) references island CSS now contains.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graphlink_styles as gs

_REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATED_FILE = _REPO_ROOT / "web_ui" / "src" / "lib" / "tokens" / "gl-vars-dev.css"
_MAIN_TSX = _REPO_ROOT / "web_ui" / "src" / "islands" / "composer" / "main.tsx"

_REGENERATE_HINT = (
    "Regenerate it: write graphlink_styles.css_root_block('dark') to "
    f"{GENERATED_FILE.name}, preserving the existing header comment. NOTE: this "
    "file mirrors EVERY dark-theme value, so any dark color edit anywhere in "
    "THEME_TOKENS makes it stale - that is expected, not a sign you broke the "
    "composer."
)


def _read(path: Path) -> str:
    # Universal-newline mode, never newline="" - .gitattributes sets
    # `* text=auto`, so a fresh checkout can materialize this file with CRLF.
    return path.read_text(encoding="utf-8")


_JS_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_JS_LINE_COMMENT_RE = re.compile(r"//[^\n]*")


def _strip_js_comments(text: str) -> str:
    """Real code, not prose, is the only reason to write the literal string
    "gl-vars-dev.css" in a .ts/.tsx file - importing it (in whatever syntax:
    static, dynamic, awaited, void'd, default-named, template-literal path)
    is the only thing that string is ever used for outside a comment. So
    rather than pattern-matching every JS import syntax shape (a whack-a-mole
    an adversarial review found gaps in - `import(...)` without `await`,
    `.then()`, `void import(...)`, template-literal paths, and
    `import x from "..."` all slipped past the first, narrower attempt),
    stripping comments first and then doing a plain substring check on what
    remains covers every real import shape automatically, while still
    excluding the exact false-positive case this test was rewritten for (a
    doc-comment merely mentioning the filename in prose)."""
    text = _JS_BLOCK_COMMENT_RE.sub("", text)
    text = _JS_LINE_COMMENT_RE.sub("", text)
    return text


class TestGlVarsDevCssIsNotStale:
    def test_file_exists(self):
        assert GENERATED_FILE.is_file(), f"{GENERATED_FILE} is missing. {_REGENERATE_HINT}"

    def test_checked_in_file_matches_regenerating_it_now(self):
        checked_in = _read(GENERATED_FILE)
        block_start = checked_in.index(":root {")

        assert checked_in[block_start:] == gs.css_root_block("dark"), (
            f"{GENERATED_FILE.name} is stale. {_REGENERATE_HINT}"
        )

    def test_it_defines_every_property_the_app_exports_for_dark(self):
        defined = set(re.findall(r"^\s*(--gl-[a-z0-9-]+):", _read(GENERATED_FILE), re.MULTILINE))

        assert defined == set(gs.css_custom_properties("dark"))


class TestGlVarsDevCssIsDevOnly:
    """It must never reach production.

    An unconditional import would land a hardcoded dark :root block later in
    <head> than the host's injected block, at equal specificity - silently
    overriding whichever theme is actually active, and masking a failed
    injection instead of letting it show.
    """

    def test_it_is_imported_behind_the_dev_flag(self):
        main_tsx = _read(_MAIN_TSX)

        assert "gl-vars-dev.css" in main_tsx, "main.tsx must import the dev variables"
        # Match the real import expression rather than a bare substring, so a
        # comment mentioning the filename can't satisfy (or spuriously break)
        # this, and an unconditional import below an unrelated
        # `const isDev = import.meta.env.DEV` can't sneak past a naive
        # index comparison.
        guarded_import = re.search(
            r"if\s*\(\s*import\.meta\.env\.DEV\s*\)\s*\{[^}]*gl-vars-dev\.css[^}]*\}",
            main_tsx,
            re.DOTALL,
        )
        assert guarded_import, (
            "gl-vars-dev.css must be imported inside an `if (import.meta.env.DEV)` "
            "block so Vite statically eliminates it from the production bundle"
        )

    def test_no_island_imports_it_unconditionally(self):
        # main.tsx is not the only possible importer once a second island
        # exists; scan the whole workspace source tree. See _strip_js_comments()
        # for why this checks "does real code mention the filename" via
        # comment-stripping rather than pattern-matching import syntax shapes.
        sources = list((_REPO_ROOT / "web_ui" / "src").rglob("*.ts")) + list(
            (_REPO_ROOT / "web_ui" / "src").rglob("*.tsx")
        )
        assert sources, "expected to find TypeScript sources to scan"

        for path in sources:
            code = _strip_js_comments(_read(path))
            if "gl-vars-dev.css" not in code:
                continue
            assert "import.meta.env.DEV" in code, (
                f"{path.relative_to(_REPO_ROOT)} imports gl-vars-dev.css without "
                "an import.meta.env.DEV guard - it would ship to production"
            )

    def test_scanner_catches_every_import_shape_review_found_gaps_in(self):
        # Adversarial review confirmed the FIRST fix (matching specific
        # import-statement regexes) missed real, equally-dangerous shapes.
        # Prove the comment-stripping rewrite catches all of them, plus the
        # one the fix must still correctly ignore.
        unconditional_shapes = [
            'import "../../lib/tokens/gl-vars-dev.css";',
            'import styles from "../../lib/tokens/gl-vars-dev.css";',
            'import("../../lib/tokens/gl-vars-dev.css");',
            'import("../../lib/tokens/gl-vars-dev.css").then(() => {});',
            'void import("../../lib/tokens/gl-vars-dev.css");',
            "await import(`../../lib/tokens/gl-vars-dev.css`);",
        ]
        for shape in unconditional_shapes:
            code = _strip_js_comments(shape)
            assert "gl-vars-dev.css" in code, f"comment-stripping should not remove real code: {shape!r}"
            assert "import.meta.env.DEV" not in code, (
                f"scanner must be able to catch this unconditional shape as a violation: {shape!r}"
            )

        prose_mention = (
            "/**\n * gl-vars-dev.css is deliberately excluded here, see elsewhere.\n */"
        )
        assert "gl-vars-dev.css" not in _strip_js_comments(prose_mention), (
            "a comment-only mention must not be treated as an import"
        )

    def test_the_built_production_bundle_does_not_contain_it(self):
        # Regression guard for the elimination itself, previously only ever
        # verified by hand. Skipped rather than failed when no build output is
        # present, so a fresh clone that hasn't run `npm run build` isn't a
        # false alarm.
        built = list((_REPO_ROOT / "assets" / "composer" / "assets").glob("*.css"))
        if not built:
            import pytest

            pytest.skip("no built composer assets present; run `npm run build` first")

        for asset in built:
            content = _read(asset)
            assert "gl-vars-dev" not in content
            # The dev sheet's signature is a :root block DEFINING island tokens.
            # The production bundle should only ever REFERENCE them via var().
            assert not re.search(r"--gl-composer-[a-z0-9-]+\s*:", content), (
                f"{asset.name} contains --gl-composer-* definitions; the dev-only "
                "variables sheet appears to have leaked into the production build"
            )
