import { useState } from "react";
import type { GallerySection } from "../Gallery";

function CurrentControls() {
  const [agentOn, setAgentOn] = useState(false);
  const [knowledgeOn, setKnowledgeOn] = useState(true);
  return (
    <div className="gl-gallery-controls">
      <div className="gl-gallery-control-panel">
        <h3>Buttons</h3>
        <p>App bar, settings and composer button styles in their current states.</p>
        <div className="gl-gallery-control-row appbar">
          <div className="appbar-group">
            <button type="button" className="appbar-btn">Library</button>
            <button type="button" className="appbar-btn appbar-btn-checkable checked" aria-pressed="true">View</button>
            <button type="button" className="appbar-btn" disabled>Disabled</button>
          </div>
        </div>
        <div className="gl-gallery-control-row">
          <button type="button" className="settings-button">Secondary</button>
          <button type="button" className="settings-button settings-button-primary">Primary</button>
          <button type="button" className="settings-button" disabled>Unavailable</button>
          <button type="button" className="composer-send-button" aria-label="Send message">↑</button>
        </div>
      </div>
      <div className="gl-gallery-control-panel">
        <h3>Fields and selects</h3>
        <p>Native fields with the Settings and Composer classes.</p>
        <div className="gl-gallery-field-grid">
          <label className="settings-field">
            <span className="settings-field-label">Text input</span>
            <input className="settings-select" placeholder="Enter a workspace name" />
          </label>
          <label className="settings-field">
            <span className="settings-field-label">Disabled input</span>
            <input className="settings-select" value="Read only" disabled readOnly />
          </label>
          <label className="settings-field">
            <span className="settings-field-label">Select</span>
            <select className="settings-select" defaultValue="balanced">
              <option value="fast">Fast</option>
              <option value="balanced">Balanced</option>
              <option value="quality">Quality</option>
            </select>
          </label>
          <label className="settings-field">
            <span className="settings-field-label">Message composer</span>
            <textarea className="composer-input gl-gallery-composer-input" placeholder="Ask anything…" rows={2} />
          </label>
        </div>
      </div>
      <div className="gl-gallery-control-panel">
        <h3>Checkboxes and toggles</h3>
        <p>Settings checkboxes and the existing Composer mode toggles.</p>
        <div className="gl-gallery-control-row">
          <label className="settings-checkbox-row"><input type="checkbox" defaultChecked />Enabled checkbox</label>
          <label className="settings-checkbox-row"><input type="checkbox" />Unchecked checkbox</label>
          <label className="settings-checkbox-row"><input type="checkbox" disabled />Disabled checkbox</label>
        </div>
        <div className="gl-gallery-control-row">
          <button
            type="button"
            className={`composer-icon-button composer-agent-toggle composer-knowledge-toggle${knowledgeOn ? " active" : ""}`}
            aria-pressed={knowledgeOn}
            onClick={() => setKnowledgeOn((value) => !value)}
          >Knowledge</button>
          <button
            type="button"
            className={`composer-icon-button composer-agent-toggle${agentOn ? " active" : ""}`}
            aria-pressed={agentOn}
            onClick={() => setAgentOn((value) => !value)}
          >Agent</button>
          <button type="button" className="composer-control composer-reasoning-chip" aria-pressed="false">
            <span className="composer-control-label">Reasoning</span>
            <span className="composer-control-value">Medium</span>
          </button>
        </div>
      </div>
    </div>
  );
}

const section: GallerySection = { title: "Current controls", order: 20, Component: CurrentControls };
export default section;
