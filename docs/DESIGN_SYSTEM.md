# Graphlink design system

This document is the reference for every visible part of the Graphlink web UI (`web_ui/`). It defines the identity, the tokens, the icon grammar and catalog, the component kit, copy style, architecture rules, the UI ratchets and the review checklist. `AGENTS.md` ("UI contract (web_ui)") summarises the rules that every change must follow.

Some tokens, icons and components listed here are introduced progressively. Until a token exists in `web_ui/src/lib/tokens/gl-vars-dev.css`, or a component exists in `web_ui/src/app/ui/`, this document is its specification.

## 1. Identity

- **Graphite monochrome.** The UI is deliberately monochrome. No new hues anywhere.
- **Status is carried by icon shape, weight and fill, never by colour alone.** The semantic status tokens are identical greys.
- **One interaction tint.** `--gl-accent` (the cool graphite-blue selection tint, today `--gl-graph-selection` / `--gl-connection-active`) is used only for:
  - selection outlines;
  - the active-state indicator of toggles and segmented controls;
  - progress fills;
  - the selected row's leading bar.
- **Focus ring** is the high-contrast `--gl-focus-ring` (near-white in dark theme, near-black in light theme); focus must never blend in.
- **Primary action = inverted fill**: near-white fill with near-black text in the dark theme; near-black fill with white text in the light theme.
  - Exactly one primary action per surface.
  - Secondary = subtle raised fill. Ghost = transparent.
- **Destructive actions are never distinguished by colour.** They carry a `trash` or `status-warning` (alert) icon, an explicit verb ("Delete chat") and a confirmation step in which the destructive verb is the primary button.

## 2. Tokens

### 2.1 Typography

- **UI font:** `"Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif`. Titles use `"Segoe UI Variable Display"` first. No bundled fonts (native ClearType rendering, no licensing or download).
- **Monospace:** `--gl-font-mono` = `"Cascadia Mono", "Cascadia Code", Consolas, ui-monospace, monospace`.
- **Scale** (size / line height in px):

  | Token | Size / line height | Used for |
  | --- | --- | --- |
  | `--gl-text-2xs` | 11 / 14 | Captions, badges, axis labels, menu-group overlines. Minimum size anywhere in the chrome. |
  | `--gl-text-xs` | 12 / 16 | Meta, helper text, shortcuts, table cells in nodes |
  | `--gl-text-sm` | 13 / 18 | Default UI text: buttons, inputs, menus, node body |
  | `--gl-text-base` | 14 / 20 | Dialog body, composer input |
  | `--gl-text-lg` | 16 / 22 | Dialog titles, settings page titles |
  | `--gl-text-xl` | 20 / 26 | Empty-state and onboarding titles |
  | `--gl-text-2xl` | 24 / 30 | Rare display use |

- **Weights:** 400 regular, 500 medium (labels, buttons), 600 semibold (titles).
- **Overline** (`.gl-overline`): uppercase, 11px, 0.04em tracking, muted. Only for group labels in menus, the command palette and the settings rail. Dialog sections use 13px semibold sentence-case headers, never uppercase kickers.
- **Canvas exception:** node content follows the View popover's font settings (`--gl-node-font-*`).
- **Migration mapping:** ≤11.5px → 2xs; 12–12.5px → xs; 13px → sm; 14–15px → base; 16–17px → lg; 18–21px → xl; 24–26px → 2xl. `em`-relative sizes are allowed only inside markdown scopes (`.node-md`, `.document-view-md`) and KaTeX.

### 2.2 Spacing

Base unit 4px.

| Token | Value |
| --- | --- |
| `--gl-space-0-5` | 2px |
| `--gl-space-1` | 4px |
| `--gl-space-1-5` | 6px |
| `--gl-space-2` | 8px |
| `--gl-space-2-5` | 10px |
| `--gl-space-3` | 12px |
| `--gl-space-4` | 16px |
| `--gl-space-5` | 20px |
| `--gl-space-6` | 24px |
| `--gl-space-8` | 32px |
| `--gl-space-10` | 40px |
| `--gl-space-12` | 48px |

- **Allowed raw values:** `0`, `1px` hairlines, `auto`, percentages, `calc()` on geometry, React Flow internals.
- **Rhythm:** dialog padding 20px; section gap 24px; gap between field rows 12px; label to control 6px; gap inside a control 6px; toolbar groups 12px between, 2px within; node padding 12px horizontal, 10px vertical.

### 2.3 Radius

| Token | Value | Used for |
| --- | --- | --- |
| `--gl-radius-xs` | 4px | Badges, chips, inline code, checkboxes |
| `--gl-radius-sm` | 6px | Buttons, inputs, menu items, segments |
| `--gl-radius-md` | 8px | Nodes, popovers, menus, cards, code blocks |
| `--gl-radius-lg` | 12px | Dialogs, toasts, composer shell, frames |
| `--gl-radius-full` | 999px | Status dots, avatars, toggle knobs, real pills only |

Migration mapping: 2–4px → xs; 5–7px → sm; 8–10px → md; 11–24px → lg; 50%, 999px or 9999px → full.

### 2.4 Elevation and layering

- **Surfaces:** `--gl-surface-canvas`, `--gl-surface-raised` and `--gl-surface-overlay`, alongside `--gl-surface-window`, `--gl-surface-field`, `--gl-surface-inset` and `--gl-surface-inset-deep`.
  - Dark: overlay (popover, menu, dialog) is lighter than raised (node).
  - Light: canvas `#F2F2F2`, raised `#FFFFFF` with a border, overlay `#FFFFFF` with a shadow. Dialogs are never darker than cards.
- **Shadows:** `--gl-shadow-1` (resting card), `--gl-shadow-2` (hover/raised), `--gl-shadow-popover` (`0 8px 28px rgba(0,0,0,.45)`), `--gl-shadow-dialog` (`0 12px 40px rgba(0,0,0,.55)`). Light-theme versions use about 40% of those alphas.
- **Scrim** `--gl-scrim`: `rgba(0,0,0,.43)` dark, `rgba(15,15,15,.16)` light. The light scrim must not turn the whole app grey.
- **Z-index:** one ordered scale in `gl-vars-dev.css`:

  ```
  --gl-z-canvas-hud < --gl-z-app-layer < --gl-z-search < --gl-z-popover < --gl-z-node-menu < --gl-z-toast < --gl-z-scrim < --gl-z-dialog
    < --gl-z-dialog-popover (nested) < --gl-z-approval < --gl-z-tooltip < --gl-z-skip-link
  ```

  Includes `--gl-z-popover` and `--gl-z-tooltip`. A raw z-index is allowed only for local stacking (0, 1 or 2 inside one component) and React Flow group nodes (−1, −2).

### 2.5 Motion

- **Durations:** `--gl-motion-fast` 120ms (hover, press), `--gl-motion-base` 180ms (popovers, menus, toasts), `--gl-motion-slow` 240ms (dialogs, panels).
- **Easing:** `--gl-ease` `cubic-bezier(.2,0,0,1)`, `--gl-ease-enter` `cubic-bezier(.05,.7,.1,1)`, `--gl-ease-exit` `cubic-bezier(.3,0,.8,.15)`.
- **Overlays:** enter with opacity 0→1 plus a 4px translate or 0.98 scale; exit with a 120ms fade driven by a `data-state="closing"` phase.
- **Indicators:** the spinner turns at 0.8s linear. Skeleton shimmer is off under reduced motion.
- **Reduced motion:** the blanket rule in `lib/ui/base.css` applies. React Flow viewport animations keep their explicit duration checks.

### 2.6 Controls: sizes and states

- **Heights:** `--gl-control-h-sm` 24px, `--gl-control-h-md` 28px (default), `--gl-control-h-lg` 32px. The composer input auto-grows.
- **Hit targets:** at least 24×24px (WCAG 2.5.8), including node-header action buttons.
- **Icon sizes:** `--gl-icon-xs` 12px, `--gl-icon-sm` 14px, `--gl-icon-md` 16px (default), `--gl-icon-lg` 20px, `--gl-icon-xl` 24px, `--gl-icon-2xl` 32px (empty states), `--gl-icon-3xl` 40px (large empty states).
- **State layers** (washes over the control's surface):

  | State | Dark wash | Light wash |
  | --- | --- | --- |
  | hover | white .06 | black .05 |
  | pressed | white .10 | black .09 |
  | selected | white .12, plus a 2px `--gl-accent` leading bar or a check icon | black .12, same indicator |

  Disabled: opacity .45, `cursor: not-allowed`, and the reason in the tooltip.
- **Focus:** `:focus-visible` only; a 2px outline in `--gl-focus-ring` with a 2px offset. Never a box-shadow ring (node selection keeps its box-shadow).

## 3. Icons

### 3.1 Grammar

1. **Grid:** 20×20 viewBox, live area 2–18. Keylines: circle r=8; square 14×14 (3–17); portrait 12×16; landscape 16×12.
2. **Stroke:** `fill="none" stroke="currentColor" stroke-width="1.5"`, round caps and joins, coordinates on the 0.5 grid. Rendered at 12, 14, 16, 20, 24, 32 and 40px, scaling naturally. `<Icon>` never changes the stroke width.
3. **Detail:** parallel strokes at least 2.5 units apart (smaller gaps merge into a blob at 16px); no detail smaller than 2 units; at most 3 primary marks; no letters or text inside icons.
4. **Corners:** rects use rx 2, or rx 1.5 when under 8 units.
5. **Fills:** only for dots (r ≤ 1.25) and explicit `-filled` toggle variants (`star-filled`, `pin-filled`), drawn with `fill="currentColor" stroke="none"`.
6. **Semantics:** arrows point in the action's direction; `external-link` only for things that leave the app; send uses `arrow-up`, run uses `play` (a stroked triangle), `stop` is a rounded square. Confusable pairs have different silhouettes: branch / repo / merge; agent / code / runner; refresh / rerun / history / stale; favourite star / final-deliverable flag.
7. **Status set** (distinct silhouettes so grey-only status reads): success = check in circle; error = x in octagon; warning = ! in triangle; info = i in circle; pending = dashed circle; running = Spinner (static fallback: circle with centre dot); skipped = circle with skip-arrow; cancelled = circle with slash; stale = circular arrow with a dot; approval = shield with !.
8. **Contract:** `<Icon name size label?>` renders `<svg viewBox="0 0 20 20" class="gl-icon" focusable="false">`; without `label` it renders `aria-hidden="true"`, and with `label` it renders `role="img" aria-label`. A control's accessible name comes from the control, never from its icon.
9. **Storage:** path data lives in `web_ui/src/app/ui/icons/glyphs/{chrome,nodes,status,feature}.ts`; the registry imports all four files.
10. **Aliases** are registry entries pointing at another glyph, so one concept never has two drawings.

### 3.2 Catalog: chrome and common actions (`glyphs/chrome.ts`)

| Name | Metaphor / construction | Replaces / first users |
| --- | --- | --- |
| `close` | two diagonals, 6→14 | `×` and `&times;` everywhere |
| `check` | tick (5,10.5)→(8.5,14)→(15,6.5) | `✓` in pickers, menus, copied state |
| `plus` / `minus` | 10-unit cross / bar | "+ Pin view", CSS `"＋ "` and `"− "`, viewport dock |
| `chevron-down` / `-up` / `-left` / `-right` | 6-unit 45° chevrons | `▾ ▼ ⌄ &#9662;` / `▲ ⌃` / `‹` / `› ▸ &rsaquo;` |
| `arrow-up` / `-down` / `-left` / `-right` | shaft plus 45° head | search prev/next; `arrow-up` is also composer send |
| `external-link` | open box corner with arrow leaving top-right | `↗`, links leaving the app only |
| `more-horizontal` / `more-vertical` | 3 filled dots, r=1.25 | node `⋯`, app-bar overflow |
| `search` | circle r=5.5 at (8.5,8.5), handle to (16,16) | search fields |
| `undo` / `redo` | hooked arrows | undo/redo |
| `zoom-in` / `zoom-out` | magnifier with + / − | zoom commands |
| `fit-view` | four corner brackets | Fit all |
| `organize` | auto-layout tree: 1 rounded rect on top, 2 below, elbow lines | Organize |
| `pin` / `pin-filled` | push-pin head and needle | navigation pins, pinned model |
| `download` | arrow down into a tray | export, download, pull model |
| `upload` | arrow up out of a tray | upload, add files |
| `document-text` | page with folded corner and 2 lines | export methods, Document View |
| `knowledge` | open book with spine | Knowledge |
| `builder` | wand with detached spark | Builder |
| `agent` | terminal window, prompt chevron and cursor line | Agent |
| `diagnostics` | pulse line | Diagnostics |
| `settings` | three sliders (a cog reads as a sun at 16px) | Settings |
| `help` | ? in circle | Help |
| `info` | i in circle | About, info alerts |
| `stop` | rounded square 8×8 | stop generation, stop run |
| `attach` | paperclip | composer attach |
| `layers` | 3 stacked rhombi | context inspector |
| `compare` | two offset rounded rects | compare models or versions |
| `gauge` | semicircle dial with needle | reasoning level |
| `copy` | two offset rounded rects, front one solid outline | copy |
| `edit` | pencil | rename, edit message |
| `trash` | bin with lid | delete |
| `refresh` | single clockwise circular arrow, 60° gap | check status, reload |
| `rerun` | circular arrow around a small play triangle | re-run from here, retry |
| `play` / `pause` | stroked triangle / two bars | run |
| `keyboard` | keyboard outline, keys as dots | shortcuts |
| `panel-left` | rect with left column | Document View toggle |
| `maximize` / `minimize` | outward / inward corner arrows | expand and restore |
| `grip` | 2×3 filled dots | drag handles, splitters |
| `eye` / `eye-off` | eye / eye with slash | reveal secret, hide branches |
| `lock` / `unlock` | padlock closed / open | aspect lock, permissions |
| `link` / `unlink` | chain links | URL fields, connections |
| `filter` | funnel | filters |
| `clock` | clock face | durations, "Updated" meta |
| `history` | clock with counter-clockwise arrow | versions, run history, Superseded |
| `star` / `star-filled` | 5-point star | favourites |
| `archive` | box with lid and slot | archive chat |
| `tag` | tag with hole | tags |
| `folder` / `folder-open` / `file` | — | workspaces, watched folders, files |
| `sparkle` | 4-point star plus a small star | "Generate …" actions |
| `flag` | flag on pole | Final deliverable |
| `branch` | Y-fork: dot on top, two strokes to two dots | Branch from here |
| `merge` | two strokes converging into one arrow | synthesis |
| `brand` | 3 connected nodes | app mark |

### 3.3 Catalog: node kinds and statuses (`glyphs/nodes.ts`, `glyphs/status.ts`)

| Name | Metaphor | Kind |
| --- | --- | --- |
| `node-user` | head and shoulders | chat (You) |
| `node-assistant` | 4-point spark inside a circle (distinct from `sparkle`) | chat (Assistant) |
| `node-thinking` | thought cloud with 2 trailing dots | thinking |
| `node-code` | `</>` brackets | code |
| `node-runner` | play triangle inside a rounded square | code_sandbox |
| `node-document` | alias of `document-text` | document |
| `node-html` | browser window: top bar with 2 dots | html |
| `node-image` | frame, mountain and sun | image |
| `node-media` | rect with play triangle | fal_media |
| `node-conversation` | two overlapping speech bubbles | conversation |
| `node-web` | globe: circle, meridian and equator | web_research |
| `node-artifact` | box with top seam | artifact |
| `node-git` | commit: horizontal line through a circle | gitlink |
| `node-review` | page with +/− lines | code_review |
| `node-note` | sticky note with folded bottom-right corner | note |
| `node-model` | isometric cube outline | model |
| `node-science` | Erlenmeyer flask | scientific |
| `node-chart` | axes plus polyline | chart |
| `node-table` | 3×3 rounded grid | table source/output |
| `node-plan` | checklist (2 tick rows) | plan |
| `node-agent` | alias of `agent` | harness |
| `node-frame` | rect with a title tab at top-left (distinct from `fit-view`) | frame |
| `node-container` | box with header bar | container |
| `node-placeholder` | dashed rounded square | placeholder |

| Name | Construction | Used for |
| --- | --- | --- |
| `status-success` | check in circle | Done, Indexed, Approved |
| `status-error` | x in octagon | Failed, provider errors |
| `status-warning` | ! in triangle | warnings |
| `status-info` | alias of `info` | info |
| `status-pending` | dashed circle | pending, queued |
| `status-running` | circle with centre dot (static fallback for Spinner) | running |
| `status-skipped` | circle with skip-arrow | skipped |
| `status-cancelled` | circle with slash | cancelled |
| `status-stale` | circular arrow with centre dot | Out of date, Upstream changed |
| `status-approval` | shield with ! | Waiting for approval |
| `status-approved` | shield with check | session grant active |
| `status-rejected` | x in circle (distinct from the error octagon) | Rejected branch |

Badges reuse `history` (Superseded), `flag` (Final deliverable), `merge` (Synthesis) and `pin` (Pinned model).

### 3.4 Catalog: feature and domain (`glyphs/feature.ts`)

| Name | Metaphor | Used for |
| --- | --- | --- |
| `chat` / `chat-plus` | speech bubble with 2 lines / with + | library rows, New chat |
| `workspace` | rounded square with 2×2 cells | workspaces |
| `cloud` | cloud | API endpoint provider |
| `cpu` | chip with pins | local models |
| `server` | two stacked units with dots | Ollama server status |
| `key` | key | API keys, secrets |
| `plug` | two-prong plug | Integrations |
| `puzzle` | puzzle piece | Plugins |
| `hub` | centre dot with 3 spokes to dots | MCP servers |
| `shield` | shield | Resource limits |
| `coins` | two stacked ellipses | Model pricing, cost |
| `palette` | painter's palette with dots | Theme |
| `bell` | bell | Notification types |
| `database` | cylinder | embeddings, knowledge store |
| `globe` | alias of `node-web` | web links |
| `terminal` | alias of `agent` | terminal output |
| `chart-line` / `chart-bar` / `chart-pie` / `chart-scatter` / `chart-box` / `chart-heatmap` / `chart-sankey` | chart-type glyphs (heatmap: 3×3 grid with one cell outlined twice; sankey: 3 curved bands) | chart types |
| `flask` | alias of `node-science` | Scientific Workbench |
| `ruler` | ruler | units |
| `crosshair` | crosshair | inspect, focus |
| `video` / `audio` / `image` | rect with play / 5 waveform bars / alias of `node-image` | media categories |
| `cube` | alias of `node-model` | 3D / model |
| `grid-dots` / `grid-lines` / `grid-cross` | grid style previews | grid style |
| `magnet` | horseshoe magnet | snap to grid |
| `guides` | crossing dashed guide lines | smart guides |
| `route-curved` / `route-orthogonal` | S-curve / elbow path | connection routing |
| `text-cursor` | I-beam | add text input |
| `bookmark` | bookmark | save as recipe |
| `hourglass` | hourglass | queued |
| `file-code` / `file-table` / `file-image` | page variants | document types |

## 4. Component kit

All kit components live in `web_ui/src/app/ui/`, one component per file, imported directly from that file (there is no barrel `index.ts`). Each has co-located CSS (`gl-*` classes), a gallery section file, and Vitest tests for roles and accessible names, keyboard behaviour, and every variant and state. Once the kit is complete its APIs are frozen; later changes must be additive.

| Component | Contract |
| --- | --- |
| **Icon** | Props: `name: IconName`, `size: xs\|sm\|md\|lg\|xl\|2xl\|3xl` (default md = 16), `label?`, `className?`. Grammar §3.1; `aria-hidden` without `label`, `role="img"` with it. Tests: every registry name renders; glyphs use only path, circle, rect, line or polyline; coordinates stay inside [1.5, 18.5]; no colours; aliases resolve. |
| **Button** | Props: `variant: primary\|secondary\|ghost`, `size: sm\|md\|lg`, `iconStart`, `iconEnd`, `loading` (Spinner replaces `iconStart`, width kept, `aria-busy`), `disabled` + `disabledReason` (shown in a Tooltip), `destructive` (leading `trash` icon, `data-destructive`, colour unchanged), `fullWidth`. Always `type="button"` unless `type="submit"`. One primary per surface. |
| **IconButton** | Props: `icon`, `label` (required; becomes `aria-label` and the tooltip), `shortcut?` (a `Kbd` in the tooltip), `size: sm(24)\|md(28)\|lg(32)`, `variant: ghost (default)\|secondary\|primary`. Dev-time console error if `label` is empty. No `title` attribute. |
| **ToggleButton** | `aria-pressed`; optional icon and label; pressed = selected wash plus an icon-weight change. |
| **SegmentedControl** | `radiogroup` with arrow-key roving; options `{value, label, icon?}`; sizes sm and md; accent indicator on the selected segment. |
| **Checkbox / RadioGroup / Switch** | A visually hidden native input inside a custom-drawn box, dot or knob, so `getByRole('checkbox'\|'radio'\|'switch', {name})` keeps working. Checkbox supports `indeterminate`. RadioGroup has `plain` and `card` variants (card: title + description; selected card shows the accent bar and a check). Switch uses `role="switch"`. |
| **TextField** | Label (via Field), description, error, `prefixIcon`, `suffix` (unit, clear button, or password reveal `eye`/`eye-off`), sizes sm and md, `mono` for ids and keys; `type` text, search, url, password or email. |
| **TextArea** | Auto-grows between `minRows` and `maxRows`; `resize: none`. Code editors keep their own resizers. |
| **NumberField** | Minus/plus stepper IconButtons, `min`/`max`/`step`, unit suffix, arrows and PageUp/PageDown. Native spinners hidden. |
| **Slider** | Native range input restyled (track, fill, thumb), optional tick and value labels, full keyboard support. |
| **Select** | Button trigger plus a listbox on the nested overlay layer. Options `{value, label, description?, icon?, group?, disabled?}`; a search field above 8 options (combobox pattern); type-ahead; Home/End; Enter/Esc. No "Selected: X" echo. Replaces native `<select>` and `CustomSelect`. |
| **Field / FormRow / FieldGroup** | Field: label (13 medium), control, description (12 muted), error (12, with `status-error` icon), linked by `aria-describedby`. FormRow: `inline` (200px label column, Settings) or `stacked` (dialogs). FieldGroup: section header (13 semibold, sentence case), optional description, rows. Replaces fieldset/legend boxes and uppercase kickers. |
| **Disclosure** | Button (`chevron-right` rotating to down) plus a region with `aria-expanded`/`aria-controls`; variants `inline` and `section`. Replaces `<details>`. |
| **Tabs** | `tablist` with arrow-key roving, accent underline, lazy panels. |
| **Dialog** | Sizes sm 420, md 560, lg 760, xl 960; max-height 85vh. Header: title (16 semibold), optional description, `close` IconButton. Body scrolls. Sticky footer: status text left, actions right (secondary then primary). Initial focus: `data-autofocus`, else the first form field, else the title region — never the close button. Nested layers; Escape closes the topmost layer; enter/exit motion; focus restored on close. |
| **Popover** | Anchored with flip/clamp; nested-aware; motion; max-height plus scroll. |
| **Menu / ContextMenu / MenuButton** | Items: `icon`, `label`, `shortcut`, `description?`, `checked` (`menuitemcheckbox`/`menuitemradio`), `destructive` (`trash` icon, always in a separated last group), `disabled` + `disabledReason`. Structure: `MenuGroup` (overline label), `MenuSeparator`, `SubMenu` (`chevron-right`; opens on 150ms hover intent or ArrowRight). Type-ahead, roving focus, clamped placement, max-height plus scroll. |
| **Tooltip** | Portal on `--gl-z-tooltip`; 500ms hover delay, immediate on keyboard focus; hides on Esc or pointerdown; label plus `Kbd`; bottom placement by default in toolbars; `aria-describedby`; never covers its trigger. |
| **Toast / Toaster** | Top-right below the app bar, at most 3 visible ("+N more" collapses the rest), with motion. Info and success auto-dismiss after 5s (paused on hover or focus); warning and error persist. Anatomy: status icon, title, optional "Details" disclosure with a copy button, optional action, close. `role="status"` for info/success, `role="alert"` for error. |
| **ConfirmDialog + useConfirm** | `await confirm({title, body, confirmLabel, cancelLabel?, destructive?, billing?: {estimate}, detail?})` resolves true or false. Renders on the nested layer, so it works over any dialog. Destructive confirms focus Cancel first; the confirm button is primary and carries the verb. |
| **InlineAlert** | Status icon, title, body, optional action; variants info, success, warning, error (icon and weight, monochrome). |
| **EmptyState** | 32/40px icon, title, one-sentence body, actions; sizes sm (panels) and lg (canvas). |
| **Badge / StatusPill** | Badge: neutral chip for counts and labels (2xs/xs, `--gl-radius-xs`). StatusPill: status icon plus status word; Running embeds Spinner. |
| **Spinner** | SVG arc, sizes 12–20. Under reduced motion it becomes the static `status-running` glyph. |
| **ProgressBar** | Determinate ("3 of 8") or indeterminate; accent fill; static under reduced motion. |
| **Skeleton** | Lines and blocks for loading lists; shimmer off under reduced motion. |
| **Kbd** | Key caps ("Ctrl", "K"), 2xs, bordered. |
| **Card** | Bordered raised panel with an optional header row (title, StatusPill, actions). |
| **Formatters** (`ui/format.ts`) | `formatCount`, `formatDuration` ("12 min 43 s", "<1 s"), `formatBytes`, `formatTokens` ("42.7k"), `formatRelativeTime` ("2 h ago"). |
| **Scrollbars** | Native scrollbars themed through `scrollbar-color`. Windows 11 Fluent overlay scrollbars ignore `::-webkit-scrollbar`. |

The Formatters and Scrollbars entries describe supporting utilities and styles, not standalone kit components.

## 5. Copy style

- **Sentence case for every UI string**: titles, buttons, menu items, tabs, settings labels, palette commands, tooltips. Proper nouns keep their case: Graphlink, Ollama, Llama.cpp, Fal.ai, MCP, API, GitHub, Python, Docker, Podman. Examples: "Open library", "Branch from here", "Mark as final deliverable", "Show token counter".
- **Buttons start with a verb.** A dialog's primary button repeats the title's verb: "Delete chat?" → [Delete chat].
- **Ellipsis "…" (U+2026)** for in-progress states ("Saving…") and for commands that need a further step ("Choose folder…"). Never "...".
- **Dashes:** a spaced em dash " — " for parenthetical breaks, an en dash "–" for ranges, never a spaced hyphen as a dash.
- **Quotes:** curly quotes “ ” ‘ ’ in UI strings; straight quotes only in code and monospace.
- **Numbers and units** always go through the formatters: "12 min 43 s", "2.5 GB", "42.7k / 80k tokens". Never raw seconds ("763s/1800s").
- **Errors:** "[What failed]. [Why, if known]. [What to do]." The generic fallback is "Couldn't <action>. Details were written to the log." with an [Open log folder] action. Never "Something went wrong" alone.
- **Empty states:** a title, one sentence of explanation, one primary action.
- **Tooltips:** the label plus the shortcut in a `Kbd` ("Undo  Ctrl+Z"). Never a tooltip that repeats visible text.
- **Status words:** Running, Done, Failed, Cancelled, Waiting for approval, Out of date, Skipped.
- **No transport jargon** in the main UI ("intent", "publish", "backend"). Technical detail belongs under Diagnostics › Advanced.

## 6. Architecture rules

- **Kit location:** `web_ui/src/app/ui/`, one component per file with co-located CSS. No barrel file: surfaces import each component from its own file. `ui/icons/` holds the icon registry; `ui/gallery/` is the `#gallery` hash route (lazy-loaded, linked from nowhere) used for visual review.
- **Class namespace:** kit classes are `gl-*` with single-class specificity. Surface classes keep their own prefixes.
- **Cascade layers come last.** Unlayered CSS beats every layered rule, so all CSS stays unlayered and strictly class-scoped until `styles.css` has been reduced to globals; only then is everything wrapped in `@layer vendor, base, kit, surfaces, nodes, overrides`.
- **Move or delete.** A surface change that adopts kit components deletes that surface's legacy rules from `web_ui/src/app/styles.css` in one contiguous hunk, or moves them into the surface's own CSS file. Never add rules to `styles.css`; never reformat neighbouring rules. This also stops legacy descendant selectors (`.settings-page button`) from overriding kit styles.
- **Overlay stack:** top-level surfaces are single-open. A dialog may host nested layers (select, menu, popover, confirm, tooltip) that do not close it. Escape closes the topmost layer first.
- **No component library.** The kit is built in-house.
- **Non-goals:** no responsive or mobile layouts (desktop only, one spill guard); no colour branding; no bundled fonts; no re-theme of chart data colours (charts get typography and chrome only).

## 7. UI ratchets

`web_ui/src/lib/ui-ratchets.test.ts` enforces these rules with exact per-file inventories in `web_ui/src/lib/ui-ratchet-inventory.ts`. Numbers may only go down: when you touch a file, lower its entries to the new exact counts. `app/styles.css` totals are maxima.

| Rule | What is counted | Target | Why |
| --- | --- | --- | --- |
| R1 | Text and emoji glyphs used as icons in TSX (`× ✓ ★ ☆ ⌕ ↻ ⟳ 📁 📄 📌 ♫ ◈ ▸ ▾ ▼ ▲ ◂ ▶ ◀ ▣ ▧ ⌄ ⌃ ⇄ ⧉ ✎ ⚠ ⚙ ● ○ ↗ ↑ ↓ ‹ ›` and `&times; &rsaquo; &lsaquo; &#9662;`) | 0, plus a reasoned allowlist (scientific-notation `×`, the diff legend `− +`, the multiplier "N×") | glyphs render inconsistently and are not icons |
| R2 | Inline `<svg` outside `app/ui/icons/**`, `canvas/charts/**`, `canvas/ScientificResultViews.tsx`, `canvas/connections/**`, `canvas/OrthogonalEdge.tsx` | 0 | one icon system |
| R3 | `window.confirm` / `alert` / `prompt` | 0 | use ConfirmDialog |
| R4 | Native `<select` outside `app/ui/**` | 0 | use Select |
| R5 | `<details` outside `app/ui/**` | 0 | use Disclosure |
| R6 | Native `type="checkbox"`, `"radio"` or `"range"` outside `app/ui/**` | 0 | use Checkbox, RadioGroup, Switch, Slider |
| R7 | Raw `role="menuitem"` (and `-checkbox` / `-radio`) outside `app/ui/**` | 0 | use Menu |
| R8 | `title=` outside `app/ui/**` (`iframe` exempt) | 0 | use Tooltip |
| R9 | Raw font-size per CSS file | 0 (`em` only in markdown scopes and KaTeX) | type scale |
| R10 | Raw border-radius per CSS file | 0 (`0`, `inherit`, `50%` allowed) | radius scale |
| R11 | Raw z-index per CSS file | 0 (local `0/1/2`, React Flow `−1/−2` allowed) | layering scale |
| R12 | `styles.css` line count | ≤ 400 | styles live with their surfaces |
| R13 | `"..."` in TSX strings and JSX text | 0 | use "…" |
| R14 | Raw px in padding, margin, gap and inset per CSS file | 0 (`0`, `1px` hairlines, geometry `calc()` allowed) | spacing scale |

The existing guards (`no-raw-colors`, `token-theme-contrast`, `renderCountGate`, `wireTypeCastGuard`, `queueableClassificationGuard`) and every Python ratchet stay as they are.

## 8. Review checklist

**Icons**, at 16px in both themes:
- the silhouette is recognisable without its label;
- the stroke weight matches its neighbours;
- the glyph sits within the keylines;
- no blobs (parallel strokes at least 2.5 units apart);
- confusable pairs are distinct;
- no colour or fill leaks.

**Surfaces:**
- one primary action;
- type only from the scale;
- 4px rhythm, aligned baselines and button rows;
- tokens only;
- hover, pressed, disabled and focus states all visible;
- empty, loading and error states present;
- sentence case and correct punctuation;
- nothing clipped or overlapping at 1440×900;
- correct light-theme elevation;
- keyboard operation: Tab and Shift+Tab, Enter, Esc, arrows in menus and lists, Shift+F10 for context menus.
