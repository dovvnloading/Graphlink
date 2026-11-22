import { useEffect, useState } from "react";
import type { GallerySection } from "../Gallery";

type Token = { name: string; value: string };
type TokenGroup = "Colours" | "Type" | "Spacing" | "Radii" | "Shadows" | "Motion" | "Other";
const GROUPS: TokenGroup[] = ["Colours", "Type", "Spacing", "Radii", "Shadows", "Motion", "Other"];

// Inspect the active CSSOM, not an inventory copied into TS. Vite dev injects
// style rules; production bundles them. Both paths expose the same --gl- names.
function readTokens(): Record<TokenGroup, Token[]> {
  const names = new Set<string>();
  function visit(rules: CSSRuleList) {
    for (const rule of Array.from(rules)) {
      if ("style" in rule) {
        const style = (rule as CSSStyleRule).style;
        for (let i = 0; i < style.length; i += 1) {
          const property = style.item(i);
          if (property.startsWith("--gl-")) names.add(property);
        }
      } else if ("cssRules" in rule) {
        visit((rule as CSSGroupingRule).cssRules);
      }
    }
  }
  for (const sheet of Array.from(document.styleSheets)) {
    try { visit(sheet.cssRules); } catch { /* A foreign stylesheet may forbid CSSOM access. */ }
  }
  const computed = getComputedStyle(document.documentElement);
  const result: Record<TokenGroup, Token[]> = {
    Colours: [], Type: [], Spacing: [], Radii: [], Shadows: [], Motion: [], Other: [],
  };
  for (const name of [...names].sort()) {
    const value = computed.getPropertyValue(name).trim();
    if (!value) continue;
    const group: TokenGroup = name.startsWith("--gl-space-") ? "Spacing"
      : name.startsWith("--gl-radius-") ? "Radii"
      : name.startsWith("--gl-shadow-") ? "Shadows"
      : name.startsWith("--gl-motion-") ? "Motion"
      : /--gl-(?:text-|font-|weight-)/.test(name) ? "Type"
      : (typeof CSS !== "undefined" && typeof CSS.supports === "function"
        ? CSS.supports("color", value)
        : /^(?:#|rgba?\(|hsla?\()/i.test(value)) ? "Colours" : "Other";
    result[group].push({ name, value });
  }
  return result;
}

function Tokens({ theme }: { theme: "dark" | "light" }) {
  const [groups, setGroups] = useState(readTokens);
  useEffect(() => { setGroups(readTokens()); }, [theme]);
  const count = GROUPS.reduce((total, group) => total + groups[group].length, 0);

  return (
    <div className="gl-gallery-token-groups">
      <p className="gl-gallery-intro">{count} live <code>--gl-*</code> values from the active theme.</p>
      {GROUPS.map((group) => groups[group].length > 0 && (
        <div key={group} className="gl-gallery-token-group">
          <h3>{group} <span>{groups[group].length}</span></h3>
          {group === "Colours" ? (
            <div className="gl-gallery-swatch-grid">
              {groups[group].map(({ name, value }) => (
                <div key={name} className="gl-gallery-swatch">
                  <div className="gl-gallery-swatch-colour" style={{ backgroundColor: `var(${name})` }} />
                  <code>{name}</code><span>{value}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="gl-gallery-token-grid">
              {groups[group].map(({ name, value }) => (
                <div key={name} className="gl-gallery-token">
                  {group === "Type" && name.startsWith("--gl-text-") && (
                    <span className="gl-gallery-type-preview" style={{ fontSize: `var(${name})` }}>Ag 0123</span>
                  )}
                  {group === "Spacing" && <span className="gl-gallery-space-preview" style={{ width: `var(${name})` }} />}
                  {group === "Radii" && <span className="gl-gallery-radius-preview" style={{ borderRadius: `var(${name})` }} />}
                  {group === "Shadows" && <span className="gl-gallery-shadow-preview" style={{ boxShadow: `var(${name})` }} />}
                  <code>{name}</code><span>{value}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
      {count === 0 && <p className="gl-gallery-intro">The token stylesheet has not loaded.</p>}
    </div>
  );
}

const section: GallerySection = { title: "Tokens", order: 10, Component: Tokens };
export default section;
