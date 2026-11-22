import { useState } from "react";
import { Icon, type IconSize } from "../../icons/Icon";
import { iconNames, type IconName } from "../../icons/registry";
import type { GallerySection } from "../Gallery";

const sizes: readonly { size: IconSize; pixels: number }[] = [
  { size: "xs", pixels: 12 },
  { size: "md", pixels: 16 },
  { size: "lg", pixels: 20 },
  { size: "2xl", pixels: 32 },
];

const confusableGroups: readonly (readonly IconName[])[] = [
  ["refresh", "rerun", "history"],
  ["star", "flag"],
  ["branch", "merge"],
  ["search", "zoom-in"],
  ["copy", "compare"],
  ["external-link", "arrow-right"],
];

function Keylines() {
  return (
    <span className="gl-icon-guide-grid" aria-hidden="true">
      <span className="gl-icon-guide-circle" />
      <span className="gl-icon-guide-square" />
      <span className="gl-icon-guide-portrait" />
      <span className="gl-icon-guide-landscape" />
    </span>
  );
}

function Icons() {
  const [showKeylines, setShowKeylines] = useState(false);

  return (
    <div className="gl-icon-gallery">
      <div className="gl-icon-gallery-toolbar">
        <button
          type="button"
          className={["gl-icon-gallery-toggle", showKeylines && "gl-icon-gallery-toggle-active"].filter(Boolean).join(" ")}
          aria-pressed={showKeylines}
          onClick={() => setShowKeylines((current) => !current)}
        >
          {showKeylines ? "Hide keylines" : "Show keylines"}
        </button>
        <p className="gl-icon-gallery-note">
          Review each glyph at 12, 16, 20 and 32 px. Keylines appear behind the 64 px previews.
        </p>
      </div>

      <div className="gl-icon-gallery-grid">
        {iconNames.map((name) => (
          <div className="gl-icon-gallery-card" key={name}>
            <p className="gl-icon-gallery-name">{name}</p>
            <div className="gl-icon-gallery-large-frame">
              {showKeylines && <Keylines />}
              <Icon name={name} size="2xl" className="gl-icon-gallery-large" />
            </div>
            <div className="gl-icon-gallery-sizes">
              {sizes.map(({ size, pixels }) => (
                <div className="gl-icon-gallery-size" key={size}>
                  <Icon name={name} size={size} />
                  <span className="gl-icon-gallery-size-label">{pixels} px</span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="gl-icon-gallery-pairs">
        <h3 className="gl-icon-gallery-subheading">Confusable pairs</h3>
        {confusableGroups.map((group) => (
          <div className="gl-icon-gallery-pair" role="group" aria-label={"Compare " + group.join(", ")} key={group.join("-")}>
            {group.map((name) => (
              <div className="gl-icon-gallery-pair-item" key={name}>
                <Icon name={name} size="2xl" />
                <span className="gl-icon-gallery-pair-label">{name}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

const section: GallerySection = { title: "Icons", order: 15, Component: Icons };
export default section;
