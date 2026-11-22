import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Field, FormRow, FieldGroup } from "./Field";

describe("Field and layout", () => {
  it("links native controls to labels, descriptions, and errors without losing existing references", () => {
    render(<Field label="Access key" description="Keep this private" error="Invalid key">
      <input aria-describedby="external-help" />
    </Field>);
    const input = screen.getByRole("textbox", { name: "Access key" });
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAttribute("aria-describedby", expect.stringContaining("external-help"));
    expect(input).toHaveAttribute("aria-describedby", expect.stringContaining("description"));
    expect(input).toHaveAttribute("aria-describedby", expect.stringContaining("error"));
    expect(screen.getByText("Invalid key").closest(".gl-field-error")?.querySelector("svg")).toBeTruthy();
  });
  it("supports a render-prop control and required/inline/stacked form rows", () => {
    render(<FieldGroup title="Network" description="Connection settings">
      <FormRow label="Endpoint" layout="inline" required>{(props) => <input {...props} />}</FormRow>
      <FormRow label="Timeout" layout="stacked"><input /></FormRow>
    </FieldGroup>);
    expect(screen.getByRole("region", { name: "Network" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /Endpoint/ })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Timeout" })).toBeInTheDocument();
    expect(screen.getByText("Connection settings")).toBeInTheDocument();
    expect(screen.getByText("Endpoint").closest(".gl-field-inline")).toBeTruthy();
  });
  it("preserves a control's existing invalid state without an error", () => {
    render(<Field label="Optional"><input aria-invalid="true" /></Field>);
    expect(screen.getByRole("textbox", { name: "Optional" })).toHaveAttribute("aria-invalid", "true");
  });
});
