import type { AppPluginCategory, AppPluginEntry, AppScienceOperation } from "../../lib/bridge-core/generated/app-plugins-state";

/** A backend picker entry together with its category for cross-category search. */
export interface PluginSearchHit {
  categoryName: string;
  plugin: AppPluginEntry;
  operation?: AppScienceOperation;
}

function normalize(value: string): string {
  return value.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
}

/**
 * Instant, local search over the already-authorized app-plugins snapshot.
 * Every term must match, but terms may appear in different fields. Prefer
 * name matches without changing the backend's order for equally-ranked hits.
 */
export function searchPlugins(categories: readonly AppPluginCategory[], query: string): PluginSearchHit[] {
  const terms = normalize(query).trim().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return [];

  const phrase = terms.join(" ");
  const ranked: { hit: PluginSearchHit; rank: number; order: number }[] = [];
  for (const category of categories) {
    const categoryText = normalize(category.name + " " + category.description);
    for (const plugin of category.plugins) {
      const name = normalize(plugin.name);
      const description = normalize(plugin.description);
      const haystack = name + " " + description + " " + categoryText;
      for (const operation of plugin.scienceOperations ?? []) {
        const title = normalize(operation.title);
        const id = normalize(operation.id);
        const operationText = title + " " + id + " " + id.replaceAll("_", " ");
        if (!terms.some(term => operationText.includes(term))) continue;
        if (!terms.every(term => (name + " " + operationText).includes(term))) continue;
        const rank = title === phrase ? -1 : title.startsWith(phrase) ? 1 : 2;
        ranked.push({ hit: { categoryName: category.name, plugin, operation }, rank, order: ranked.length });
      }
      if (!terms.every((term) => haystack.includes(term))) continue;
      const rank = name.startsWith(phrase) ? 0
        : name.includes(phrase) ? 1
        : terms.every((term) => name.includes(term)) ? 2
        : categoryText.includes(phrase) ? 3
        : 4;
      ranked.push({ hit: { categoryName: category.name, plugin }, rank, order: ranked.length });
    }
  }
  ranked.sort((a, b) => a.rank - b.rank || a.order - b.order);
  return ranked.map(({ hit }) => hit);
}
