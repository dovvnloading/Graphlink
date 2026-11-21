# Branch editing, message versions, and chat re-runs

A **version** is an alternative saved **inside the same chat message card**. A **branch** is a separate path of cards on the canvas. Editing a sent question, choosing an older version, and re-running replies are different operations: **editing or switching versions does not automatically regenerate downstream answers**.

These features apply to individual **You** and **Assistant** chat cards. Re-running a chat path is not the same as executing a data workflow in Graph Runner.

## Keyboard basics

- **Reach a chat card:** use **Tab** / **Shift+Tab** to move through focusable canvas nodes and their controls. On the card, **Tab** to **Node actions** and press **Enter** or **Space** to open its menu. With the card focused, **Shift+F10** also opens its context menu.
- **Use Node actions:** **Up/Down Arrow** moves between items, **Home/End** jumps to the first/last, and **Enter** activates the focused item. **Escape** closes the menu and returns focus to its opener. Disabled actions cannot be activated.
- **Use dialogs:** **Tab** / **Shift+Tab** moves among controls; **Enter** or **Space** activates buttons and checkboxes; **Escape** closes the dialog. Closing a re-run dialog is **not** the same as cancelling a running job.
- **Undo canvas changes:** with focus **outside a text field**, press **Ctrl+Z** (or **Cmd+Z** on macOS). **Ctrl+Shift+Z** / **Cmd+Shift+Z** or **Ctrl+Y** redoes. While an editor has focus, Undo is the browser's text undo, not canvas undo.

The paths below name the actual controls; no separate single-key shortcut is required for the version switcher, Edit, Compare, or Re-run.

## Message versions and the version switcher

An edited **You** message or a regenerated **Assistant** reply gets another saved version. **Regenerate Response** on an assistant card generates a new answer in **that same card** rather than a new sibling card. Each chat card retains up to **20 versions**; older alternatives drop off when the limit is exceeded. The selected version and its recorded reply metadata are saved with the graph.

When a card has multiple versions, its header shows **Previous version** (‹), a **current/total** counter such as **2/3**, **Next version** (›), and **Compare**.

1. **Switch versions:** **Tab** to **Previous version** or **Next version**, then **Enter** / **Space**. The card immediately displays that saved alternative; the next/previous button is disabled at the end/start and both are disabled while that reply is generating. Selecting a version is undoable (**Ctrl/Cmd+Z** outside an editor).
2. **Generate another assistant version:** on an **Assistant** card, **Tab** to **Node actions**, **Enter**, use **Up/Down Arrow** to **Regenerate Response**, then **Enter**. This re-generates that single reply; use **Re-run from here** below for multiple downstream replies.

The active version supplies the text and other saved context seen by later chat replies. Switching to a version with different content can change downstream staleness indicators; merely having unselected alternatives does not.

## Edit a sent message: in place or as a new branch

Only **You** chat messages can be edited with these actions; the menu's edit items are unavailable while the card has a pending request. A mouse double-click on a user card's body is a shortcut to **Edit** in place.

1. **Open the editor:** **Tab** to the user's **Node actions**, **Enter**, use **Up/Down Arrow** to **Edit** or **Edit as new branch**, and **Enter**. The message textarea receives focus.
2. **Change and save text:** type the revised message and press **Ctrl+Enter** (**Cmd+Enter** on macOS). Alternatively, **Tab** to **Save changes** or **Save as new branch** and press **Enter** / **Space**. Use **Escape** or **Tab** to **Cancel**, **Enter**, to discard the draft.
3. **Understand the result:**
   - **Edit / Save changes** appends a version to the **existing user card** and selects it; its existing branch and child cards remain in place. Those older replies are **not** automatically re-generated.
   - **Edit as new branch / Save as new branch** adds a **new user card beside the original**, under the same structural parent (or as a separate root if the original has no parent). The original message, its versions, and its existing replies stay untouched. This action also does **not** generate an assistant reply.

An empty or unchanged edit is not saved as a new version. If the underlying message changes while the editor is open, saving is rejected until you reopen the latest version. The edit control changes text; an edited version does not copy the original message's media content parts, which remain available in its earlier saved version.

**Keyboard follow-up:** after an edit, move focus off the textarea before using **Ctrl/Cmd+Z** to undo that canvas edit. Undoing the creation of a new branch removes that branch card; it does not edit the original.

## What the staleness badges mean

Badges report a **saved result whose inputs changed**; they do not execute anything.

| Badge | Where it appears | Meaning |
| --- | --- | --- |
| **Out of date** | A generated **Assistant** chat reply | The reply has recorded generation-context provenance, and its current canonical ancestor conversation no longer matches the context used to produce it. |
| **Upstream changed** | A non-chat, derived or connected card | Its saved upstream snapshot differs, or it is downstream of an out-of-date chat reply. Its content stays as it was until its own workflow is run again. |

For chat replies, relevant changes can include an edited ancestor message, choosing another **selected** message version, changing ancestor model pins or knowledge preferences, changing model-visible attachments, or changing structural ancestry. Moving a card or renaming its title does **not** make the answer stale; reference/secondary connections do not create chat ancestry. The badges apply to the affected branch, not unrelated branches.

Older saved replies or derived nodes with **no recorded fingerprint or baseline** are not retroactively marked stale; the absence of a badge does not certify that an old result is fresh. Re-selecting the input/version used for a reply can clear its badge. **Out of date** and **Upstream changed** are visible, text-labelled status indicators, **not buttons**; use the card's accessible controls to act on a result.

## Compare two versions in Document View

**Compare** appears in the chat card's version switcher when at least two versions exist. It opens the read-only **Document View · Compare reply versions** dialog.

1. **Open:** **Tab** to the header's **Compare message versions** button and press **Enter** / **Space**.
2. **Choose a pair:** **Tab** to **Left version** and **Right version**. Use each native selector with the **Arrow keys** (and **Enter** where your platform requires it). The same version cannot be chosen on both sides. Initially, the dialog compares the selected version with the preceding one, or versions 1 and 2 if version 1 is selected.
3. **Read the difference:** the two panes show **raw reply text**, including Markdown syntax, with word-level **struck-through deletions** and **underlined insertions**. Each side shows recorded provider, model, and time when available.
4. **Navigate by keyboard:** **Tab** to **Focus left pane** or **Focus right pane**, then **Enter**; while reading a focused pane, **Alt+Left Arrow** / **Alt+Right Arrow** switches between them. **Escape** closes the dialog.

Comparison **never selects a different active version** and never changes the graph. To adopt a version, close the comparison and use the header's Previous/Next controls.

## Re-run from here: preview, scope, progress, and undo

**Re-run from here** is offered on a **You** message with more than one saved version, or an **Assistant** reply labelled **Out of date**. It is not available on arbitrary unedited user messages or current assistant replies.

1. **Open the preview:** **Tab** to the card header's **Re-run from here** quick-action button and press **Enter** / **Space**, or open **Node actions** and choose **Re-run from here** with **Up/Down Arrow**, **Enter**. The dialog **previews first**; opening it does not send any new model requests.
2. **Choose a scope:** **This path** (default) follows the active conversation tip when it descends from this card; otherwise it uses the first available canonical structural continuation. **All branches below** includes ordinary chat replies in all canonical descendant paths. **Tab** to the scope radio buttons and use **Arrow keys** / **Space** to choose. Changing scope refreshes the preview.
3. **Review the preview:** it lists the **number of replies**, each reply's **title, provider/model route, and estimated input tokens**, in **parent-before-child** order. These are estimates, not a promise of actual usage or cost. The scope may include ordinary replies that are not currently badged stale. When there are no eligible replies, **Re-run replies** is disabled.
4. **Confirm and start:** if the preview contains **more than 10 replies**, **Tab** to the explicit confirmation checkbox and press **Space** before starting. For 10 or fewer, no checkbox is required. **Tab** to **Re-run replies** and press **Enter** / **Space**. If the graph changed since the preview, the server refuses the old plan; close and reopen the dialog to preview again.
5. **Watch progress:** replies regenerate **sequentially**, parent before child. The dialog displays completed/total counts, a progress bar, the current reply ID, and completed/cancelled/failed status. Each successfully completed answer becomes a **new version of its existing assistant card**. The previous answer is preserved as an alternative; a cancelled or failed in-flight reply does **not** replace its original answer with partial text.
6. **Stop or dismiss:** **Tab** to **Cancel re-run** and press **Enter** / **Space** to request a cooperative stop (including cancellation of the current model request). Replies completed before the stop remain; an answer that finishes just before Cancel may still be counted. **Close** or **Escape** only dismisses the dialog—the job continues. When the run ends, **Preview another re-run** resets the dialog for a new preview.
7. **Undo:** after a completed, cancelled, or failed run, move focus outside any text editor and press **Ctrl+Z** / **Cmd+Z**. **All successfully regenerated replies from that one run are undone in a single canvas step**, even if the run stopped partway through. A run with no completed replies has no regeneration change to undo. Use **Ctrl+Shift+Z** / **Cmd+Shift+Z** (or **Ctrl+Y**) to redo.

Only one batch re-run can run at a time; it also cannot start if a selected reply or another chat generation is already busy.

### Important: what re-run never executes

**Re-run from here regenerates ordinary Assistant chat replies only.** It does **not** re-execute workspace agents, Builder steps, branch-synthesis agent outputs, code sandbox nodes, tool nodes, plugin nodes, or other non-chat nodes. It can traverse past non-chat cards to find later ordinary chat replies, but **those cards and their results are left untouched**. User messages are never regenerated.

**Data-bound nodes are run through Graph Runner instead**, not by chat re-run. The keyboard path is **Ctrl+K** / **Cmd+K**, type **Graph Runner**, **Enter**; then use **Tab** and **Enter** to choose the graph scope, **Preview run**, and (after reviewing that separate preview) **Run graph**. This is the workflow for refreshing connected tables, charts, reports, text-input notes, and supported Model/data-processing nodes.
