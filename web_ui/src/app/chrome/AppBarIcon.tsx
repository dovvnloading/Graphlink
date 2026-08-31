/**
 * The app bar's icon set.
 *
 * Same convention as ChatLibraryDialog's own Icon component (24x24 view
 * box, `className="icon"`, stroke inherited from the button's colour, no
 * fills) so toolbar glyphs sit on the identical visual footing as the rest
 * of the app's chrome rather than introducing a second icon language.
 *
 * DRAWING CONTRACT. Every glyph here renders at 16px (.appbar-btn .icon),
 * i.e. a 24-unit view box scaled to 0.67 - so a stroke that crosses another
 * stroke inside a ~4-unit span merges into a blob at the size it actually
 * ships at. Two rules follow, and both are load-bearing rather than
 * stylistic: keep interior detail at least 3 view-box units clear of any
 * other stroke, and prefer one recognisable silhouette over an accurate
 * miniature. `knowledge` and `builder` were both redrawn against exactly
 * this rule after rendering as an anonymous rectangle and an anonymous
 * diagonal respectively.
 *
 * Its own module rather than a local in AppBar.tsx: `react-refresh/
 * only-export-components` flags a component file that exports more than
 * components, and keeping the glyph table separate leaves AppBar.tsx as
 * layout and behaviour only.
 */

import { SearchIcon } from "./SearchIcon";

export type AppBarIconName =
  | "undo"
  | "redo"
  | "zoom-in"
  | "zoom-out"
  | "fit"
  | "organize"
  | "pin"
  | "export"
  | "search"
  | "knowledge"
  | "builder"
  | "agent"
  | "diagnostics"
  | "settings"
  | "help"
  | "about"
  | "chevron"
  | "more";

export function AppBarIcon({ name }: { name: AppBarIconName }) {
  switch (name) {
    case "undo":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <path d="M4 9h10a5 5 0 0 1 0 10h-4" />
          <path d="m4 9 4-4M4 9l4 4" />
        </svg>
      );
    case "redo":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <path d="M20 9H10a5 5 0 0 0 0 10h4" />
          <path d="m20 9-4-4M20 9l-4 4" />
        </svg>
      );
    case "zoom-in":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <circle cx="10.5" cy="10.5" r="6.5" />
          <path d="m20 20-4.8-4.8M10.5 7.5v6M7.5 10.5h6" />
        </svg>
      );
    case "zoom-out":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <circle cx="10.5" cy="10.5" r="6.5" />
          <path d="m20 20-4.8-4.8M7.5 10.5h6" />
        </svg>
      );
    case "fit":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
        </svg>
      );
    case "organize":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <rect x="4" y="4" width="7" height="7" rx="1" />
          <rect x="13" y="4" width="7" height="7" rx="1" />
          <rect x="4" y="13" width="7" height="7" rx="1" />
          <rect x="13" y="13" width="7" height="7" rx="1" />
        </svg>
      );
    case "pin":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <path d="M9 4h6l-1 6 3 3H7l3-3Z" />
          <path d="M12 13v7" />
        </svg>
      );
    case "export":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <path d="M12 4v10" />
          <path d="m8 10 4 4 4-4" />
          <path d="M5 17v2a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-2" />
        </svg>
      );
    case "search":
      return <SearchIcon />;
    case "knowledge":
      // An open book: two facing pages around a visible spine. The earlier
      // single-outline version rendered as a plain rectangle at 15px.
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <path d="M12 6.5v13" />
          <path d="M12 6.5C10.5 5 8.5 4.5 4 4.5v13c4.5 0 6.5.5 8 2" />
          <path d="M12 6.5c1.5-1.5 3.5-2 8-2v13c-4.5 0-6.5.5-8 2" />
        </svg>
      );
    case "builder":
      // A wand with one spark - the Builder writes a whole graph from a
      // prompt (ADR-008), which is a "generate this for me" affordance, not
      // a "tighten this bolt" one. The wrench it replaces collapsed into a
      // single anonymous diagonal at 16px because its jaw was a 4-unit
      // detail; a shaft plus a detached spark keeps two clearly separated
      // marks at any size.
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <path d="M4.5 19.5 14 10" />
          <path d="m12.5 8.5 3 3 4-4-3-3z" />
          <path d="M6 3.5v3M4.5 5h3" />
        </svg>
      );
    case "agent":
      // A simple terminal/prompt glyph - the workspace agent works a
      // scratch directory, and a prompt chevron is the established visual
      // shorthand for that kind of surface.
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <rect x="3" y="4.5" width="18" height="15" rx="2.5" />
          <path d="m7 9.5 3.5 2.5L7 14.5" />
          <path d="M13 15h4" />
        </svg>
      );
    case "diagnostics":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <path d="M3 12h4l2.5-6 5 12L17 12h4" />
        </svg>
      );
    case "settings":
      // Sliders rather than a cog: at a 15px render a cog's teeth collapse
      // into radial strokes and read as a sun/brightness mark, which is
      // exactly what this glyph was mistaken for. Sliders stay legible at
      // this size and carry the same "adjust how this behaves" meaning.
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <path d="M4 7h8M17 7h3M4 12h11M20 12h0M4 17h5M14 17h6" />
          <circle cx="14.5" cy="7" r="2.1" />
          <circle cx="17.5" cy="12" r="2.1" />
          <circle cx="11.5" cy="17" r="2.1" />
        </svg>
      );
    case "help":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <circle cx="12" cy="12" r="8.5" />
          <path d="M9.6 9.4a2.5 2.5 0 1 1 3.2 2.9c-.6.2-.9.7-.9 1.3v.4" />
          <path d="M12 17.2v.01" />
        </svg>
      );
    case "about":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <circle cx="12" cy="12" r="8.5" />
          <path d="M12 11v5.5" />
          <path d="M12 7.8v.01" />
        </svg>
      );
    case "chevron":
      // The disclosure mark on View/Plugins/Help. Previously a literal
      // "&#9662;" text glyph, which took its weight from the font rather
      // than from the icon set and therefore sat visibly heavier and lower
      // than every stroke next to it. As an <svg class="icon"> it inherits
      // the same 1.7 stroke and the same currentColor as its own label.
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon icon-chevron">
          <path d="m7 10 5 5 5-5" />
        </svg>
      );
    case "more":
    default:
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <circle cx="12" cy="5.5" r="1.4" fill="currentColor" stroke="none" />
          <circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" />
          <circle cx="12" cy="18.5" r="1.4" fill="currentColor" stroke="none" />
        </svg>
      );
  }
}
