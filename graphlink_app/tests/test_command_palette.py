"""Regression coverage for the Ctrl+K command palette crash (Phase 1 hot-fix,
see doc/FRONTEND_WEB_MIGRATION_MASTER_PLAN.md section 2.4).

CommandPaletteDialog.__init__ builds its stylesheet from an f-string
containing literal QSS braces (`QDialog { ... }`) alongside one real
substitution (`{get_semantic_color(...).name()}`). Python's f-string parser
reads every unescaped `{` as the start of a replacement field, so a literal
`QDialog {` was parsed as an expression `background` (the first bare name
inside the braces) followed by a `: ...` format spec - raising
`NameError: name 'background' is not defined` the moment the dialog was
constructed, i.e. every time a user pressed Ctrl+K. Verified live before
the fix (2026-07-19): constructing CommandPaletteDialog([]) raised exactly
that NameError. Fixed by doubling every literal brace ({{ / }}), leaving
only the one real substitution single-braced.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphlink_command_palette import CommandManager, CommandPaletteDialog


class TestCommandPaletteDialogConstructs:
    def test_constructs_with_no_commands_without_raising(self):
        # This is the exact reproduction of the pre-fix crash: construction
        # alone (before .exec(), before any user interaction) used to raise
        # NameError unconditionally.
        dialog = CommandPaletteDialog([])
        assert dialog is not None

    def test_constructs_with_real_registered_commands(self):
        manager = CommandManager()
        manager.register_command("Test Command", ["tc"], callback=lambda: None)
        dialog = CommandPaletteDialog(manager.get_available_commands())
        assert dialog.results_list.count() == 1


class TestStylesheetRendersCorrectly:
    def test_rendered_stylesheet_has_balanced_single_braces(self):
        dialog = CommandPaletteDialog([])
        qss = dialog.styleSheet()
        assert "{{" not in qss and "}}" not in qss, "escaped double-braces leaked into the rendered QSS"
        assert qss.count("{") == qss.count("}") == 6, "expected exactly 6 QSS rule blocks"

    def test_semantic_color_substitution_resolves_to_a_hex_color(self):
        dialog = CommandPaletteDialog([])
        qss = dialog.styleSheet()
        assert "QListWidget::item:selected {" in qss
        # The one real f-string substitution: get_semantic_color("status_success").name()
        selected_block = qss.split("QListWidget::item:selected {")[1]
        assert "background-color: #" in selected_block
