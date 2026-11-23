import { Icon } from "../ui/icons/Icon";

/**
 * Compatibility wrapper for the legacy app-bar and cloud-provider controls.
 * Their owning PRs will replace this import; the glyph itself now comes
 * exclusively from the shared kit registry.
 */
export function SearchIcon() {
  return <Icon name="search" />;
}
