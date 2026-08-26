/**
 * The magnifying-glass glyph - shared by AppBarIcon.tsx's "search" case and
 * ChatLibraryDialog.tsx's own local Icon component, which previously each
 * hand-rolled an identical `<svg>` (same viewBox, same circle+path). Pulled
 * into its own module rather than folded into either dialog's icon set: it
 * is the one glyph both sets actually need to render pixel-identically
 * (search's own AppBar toolbar button and the library's inline search
 * field), while the rest of each set is specific to its own surface.
 */
export function SearchIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m20 20-4.8-4.8" />
    </svg>
  );
}
