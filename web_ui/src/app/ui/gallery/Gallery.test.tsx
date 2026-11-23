import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import Gallery from "./Gallery";
import type { GallerySection } from "./Gallery";

const modules = import.meta.glob<{ default: GallerySection }>("./sections/*.tsx", { eager: true });
const discoveredSections = Object.values(modules).map(({ default: section }) => section);
const stubs = discoveredSections.map((section) => ({ ...section, Component: () => null }));

afterEach(() => document.documentElement.removeAttribute("data-theme"));

describe("visual gallery", () => {
  it("renders the title of every discovered section", () => {
    render(<Gallery sections={stubs} />);
    for (const section of discoveredSections) {
      expect(screen.getByRole("heading", { name: section.title })).toBeVisible();
    }
  });

  it("switches data-theme on the html element", () => {
    render(<Gallery sections={[]} />);
    fireEvent.click(screen.getByRole("button", { name: "Light" }));
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(screen.getByRole("button", { name: "Light" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Dark" }));
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(screen.getByRole("button", { name: "Dark" })).toHaveAttribute("aria-pressed", "true");
  });

  it.each(discoveredSections)("smoke-renders $title in isolation", ({ Component }) => {
    render(<Component theme="dark" />);
  });
});
