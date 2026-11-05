# System Prompt plugin

A first-party Graphlink plugin for setting the system instruction of a conversation branch. System prompts are ordinary `note` nodes with `is_system_prompt=True`; the plugin does **not** introduce a new node kind or persistence format.

## Usage

1. **Before starting a conversation:** open the plugin picker with no selection and choose **System Prompt**. A prompt note appears at the viewport center. Edit its text. The first parentless chat node can automatically adopt that note.
2. **On an existing branch:** select any node within the branch and choose **System Prompt**. The plugin resolves the branch root and creates a prompt note above it, with a `prompt note -> branch root` edge.
3. **Assign a draft to an existing branch:** create a prompt with nothing selected, edit it, then select an existing branch. Choosing **System Prompt** connects that same note to the branch without changing its content or position.
4. **Reopen an existing prompt:** choose **System Prompt** while an existing prompt note is selected, or select a branch that already has one. The same note is reused instead of creating duplicates.

Graphlink uses the first matching prompt-to-root edge to resolve a branch override, so creating more than one attached prompt for a root is not useful. The picker is idempotent for already-associated prompts.

## Integrity guarantees

- Creating a prompt and attaching a draft are each **one** `pluginSystemPrompt` undoable command. Undoing a draft attachment removes its edge **without deleting the authored note**.
- A draft is considered unattached only if it has **no incoming or outgoing edges**. This is shared with `SceneDocument.adopt_pending_system_prompt` to prevent a connected note from being repurposed or creating a cycle.
- If **multiple** unattached prompt notes exist, the picker displays a warning rather than choosing one arbitrarily. Connect or delete the extras before assigning one.
- Selecting an existing prompt note is read-only and never creates a second prompt attached to that note.
- Missing or non-finite spawn coordinates default to `(0, 0)`, preventing invalid canvas coordinates.
- The plugin does not send information to a network service or execute arbitrary user code. It changes the local graph only.

## Implementation

`plugin.py` is deliberately small and separates **lookup**, **branch resolution**, **coordinate normalization**, **pending creation**, **draft attachment**, and **branch creation**. The edge-connectedness rule lives in `backend/domain/graph.py::unattached_system_prompt_notes`, shared with new-root adoption.

Regression coverage: `backend/tests/test_system_prompt_refactor.py`, plus the existing picker, persistence, and undo tests in `backend/tests/test_plugins.py` and `backend/tests/test_plugin_builtin_migration.py`.
