/**
 * FormSelect - Reusable select component with react-hook-form integration
 */
import { Select, SelectProps } from '@mantine/core';
import { Control, Controller, FieldPath, FieldValues } from 'react-hook-form';

export interface FormSelectProps<T extends FieldValues> extends Omit<SelectProps, 'name' | 'control' | 'data'> {
  name: FieldPath<T>;
  control: Control<T>;
  label?: string;
  description?: string;
  required?: boolean;
  data: Array<{ value: string; label: string }>;
  searchable?: boolean;
}

export function FormSelect<T extends FieldValues>({
  name,
  control,
  label,
  description,
  required,
  data,
  searchable = false,
  ...selectProps
}: FormSelectProps<T>) {
  return (
    <Controller
      name={name}
      control={control}
      render={({ field, fieldState: { error } }) => (
        <Select
          {...selectProps}
          {...field}
          label={label}
          description={description}
          required={required}
          data={data}
          searchable={searchable}
          error={error?.message}
        />
      )}
    />
  );
}
