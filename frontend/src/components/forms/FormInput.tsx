/**
 * FormInput - Reusable text input component with react-hook-form integration
 */
import { TextInput, TextInputProps } from '@mantine/core';
import { Control, Controller, FieldPath, FieldValues } from 'react-hook-form';

export interface FormInputProps<T extends FieldValues> extends Omit<TextInputProps, 'name' | 'control'> {
  name: FieldPath<T>;
  control: Control<T>;
  label?: string;
  description?: string;
  required?: boolean;
}

export function FormInput<T extends FieldValues>({
  name,
  control,
  label,
  description,
  required,
  ...textInputProps
}: FormInputProps<T>) {
  return (
    <Controller
      name={name}
      control={control}
      render={({ field, fieldState: { error } }) => (
        <TextInput
          {...textInputProps}
          {...field}
          label={label}
          description={description}
          required={required}
          error={error?.message}
        />
      )}
    />
  );
}
