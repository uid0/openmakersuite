/**
 * FormTextarea - Reusable textarea component with react-hook-form integration
 */
import { Textarea, TextareaProps } from '@mantine/core';
import { Control, Controller, FieldPath, FieldValues } from 'react-hook-form';

export interface FormTextareaProps<T extends FieldValues> extends Omit<TextareaProps, 'name' | 'control'> {
  name: FieldPath<T>;
  control: Control<T>;
  label?: string;
  description?: string;
  required?: boolean;
}

export function FormTextarea<T extends FieldValues>({
  name,
  control,
  label,
  description,
  required,
  ...textareaProps
}: FormTextareaProps<T>) {
  return (
    <Controller
      name={name}
      control={control}
      render={({ field, fieldState: { error } }) => (
        <Textarea
          {...textareaProps}
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
