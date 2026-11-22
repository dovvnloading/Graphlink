import { Select } from "../ui/Select";

export interface CustomSelectOption {
  id: string;
  label: string;
  description?: string;
}

export function CustomSelect({
  value, options, onChange, ariaLabel, disabled, placeholder,
}: {
  value: string;
  options: CustomSelectOption[];
  onChange: (id: string) => void;
  ariaLabel: string;
  disabled?: boolean;
  placeholder?: string;
}) {
  return (
    <Select
      value={value}
      options={options.map(({ id, label, description }) => ({ value: id, label, description }))}
      onChange={onChange}
      ariaLabel={ariaLabel}
      disabled={disabled}
      placeholder={placeholder}
    />
  );
}
