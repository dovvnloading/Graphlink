import { Icon } from "../ui/icons/Icon";

/**
 * Transitional compatibility for ViewportDock and CloudProviderSettings, which
 * still import AppBarIcon outside UI-14's allowed edit scope. The application
 * toolbar itself now uses Icon/IconButton directly. Remove this adapter when
 * those two consumers migrate, rather than retaining the legacy SVG table.
 */
export type AppBarIconName = "fit" | "chevron";

export function AppBarIcon({ name }: { name: AppBarIconName }) {
  return <Icon name={name === "fit" ? "fit-view" : "chevron-down"}
    size="md" className={name === "chevron" ? "icon icon-chevron" : "icon"} />;
}
