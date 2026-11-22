import { cloneElement, isValidElement, useId, type ReactElement, type ReactNode } from "react";
import { Icon } from "./icons/Icon";
import "./field.css";

export type FieldControlProps = {
  id?: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean | "true" | "false";
};

export type FieldProps = {
  label: string;
  description?: string;
  error?: string;
  id?: string;
  required?: boolean;
  layout?: "stacked" | "inline";
  className?: string;
  children: ReactElement<FieldControlProps> | ((props: FieldControlProps) => ReactNode);
};

/** Owns the label, helper text, and error IDs; the actual control keeps its native semantics. */
export function Field({
  label, description, error, id, required, layout = "stacked", className, children,
}: FieldProps) {
  const generated = useId();
  const controlId = id ?? (isValidElement(children) && children.props.id) ?? generated;
  const descriptionId = description ? controlId + "-description" : undefined;
  const errorId = error ? controlId + "-error" : undefined;
  const existing = isValidElement(children) ? children.props["aria-describedby"] : undefined;
  const describedBy = [existing, descriptionId, errorId].filter(Boolean).join(" ") || undefined;
  const invalid = Boolean(error) || (isValidElement(children) && children.props["aria-invalid"]) || undefined;
  const controlProps: FieldControlProps = {
    id: controlId, "aria-describedby": describedBy, "aria-invalid": invalid,
  };
  const control = typeof children === "function"
    ? children(controlProps)
    : cloneElement(children, controlProps);

  return (
    <div className={["gl-field", layout === "inline" && "gl-field-inline", className].filter(Boolean).join(" ")}>
      <label className="gl-field-label" htmlFor={controlId}>
        {label}{required && <span className="gl-field-required" aria-hidden="true"> *</span>}
      </label>
      <div className="gl-field-body">
        {control}
        {description && <span className="gl-field-description" id={descriptionId}>{description}</span>}
        {error && <span className="gl-field-error" id={errorId}><Icon name="status-error" size="sm" />{error}</span>}
      </div>
    </div>
  );
}

export type FormRowProps = FieldProps;

/** A field row that can align labels in a 200 px settings column. */
export function FormRow(props: FormRowProps) {
  return <div className="gl-form-row"><Field {...props} /></div>;
}

export type FieldGroupProps = {
  title: string;
  description?: string;
  children: ReactNode;
  className?: string;
};

export function FieldGroup({ title, description, children, className }: FieldGroupProps) {
  const id = useId();
  return (
    <section className={["gl-field-group", className].filter(Boolean).join(" ")} aria-labelledby={id}>
      <header className="gl-field-group-header">
        <h3 className="gl-field-group-title" id={id}>{title}</h3>
        {description && <p className="gl-field-group-description">{description}</p>}
      </header>
      <div className="gl-field-group-rows">{children}</div>
    </section>
  );
}
