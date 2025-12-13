/**
 * FormNumberInput - Reusable number input component with react-hook-form integration
 */
import { NumberInput, NumberInputProps } from '@mantine/core';
import { Control, Controller, FieldPath, FieldValues } from 'react-hook-form';

export interface FormNumberInputProps<T extends FieldValues> extends Omit<NumberInputProps, 'name' | 'control'> {
  name: FieldPath<T>;
  control: Control<T>;
  label?: string;
  description?: string;
  required?: boolean;
  min?: number;
  max?: number;
  precision?: number; // Deprecated in Mantine v7, use decimalScale instead
  decimalScale?: number;
}

export function FormNumberInput<T extends FieldValues>({
  name,
  control,
  label,
  description,
  required,
  min,
  max,
  precision,
  decimalScale,
  ...numberInputProps
}: FormNumberInputProps<T>) {
  return (
    <Controller
      name={name}
      control={control}
      render={({ field, fieldState: { error } }) => (
        <NumberInput
          {...numberInputProps}
          {...field}
          value={field.value ?? undefined}
          onChange={(value) => {
            field.onChange(value === '' ? undefined : value);
          }}
          label={label}
          description={description}
          required={required}
          min={min}
          max={max}
          decimalScale={decimalScale ?? precision}
          error={error?.message}
        />
      )}
    />
  );
}
