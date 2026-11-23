import { useState } from "react";
import { Tabs } from "../../Tabs";
import type { GallerySection } from "../Gallery";

const items = [
  {id:"overview", label:"Overview", content:<p>Overview content is mounted only when selected.</p>},
  {id:"history", label:"History", content:<p>History content appears when this tab is selected.</p>},
  {id:"locked", label:"Unavailable", content:<p>Not available.</p>, disabled:true},
  {id:"settings", label:"Settings", content:<p>Settings panel content.</p>},
];

function TabExamples() {
  const [active, setActive] = useState("overview");
  return (
    <div className="gl-gallery-control-panel">
      <h3>Roving tabs</h3>
      <p>Use Left/Right to change tabs, and Home/End to reach either edge.</p>
      <Tabs label="Gallery panels" items={items} value={active} onValueChange={setActive} />
    </div>
  );
}
const section: GallerySection = { title: "Tabs", order: 37, Component: TabExamples };
export default section;
