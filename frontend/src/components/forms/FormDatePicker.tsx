/**
 * FormDatePicker - Reusable date picker component with react-hook-form integration
 */
import { DatePickerInput, DatePickerInputProps } from '@mantine/dates';
import dayjs from 'dayjs';
import { Control, Controller, FieldPath, FieldValues } from 'react-hook-form';

export interface FormDatePickerProps<T extends FieldValues> extends Omit<DatePickerInputProps, 'name' | 'control' | 'value' | 'onChange'> {
  name: FieldPath<T>;
  control: Control<T>;
  label?: string;
  description?: string;
  required?: boolean;
}

export function FormDatePicker<T extends FieldValues>({
  name,
  control,
  label,
  description,
  required,
  ...datePickerProps
}: FormDatePickerProps<T>) {
  return (
    <Controller
      name={name}
      control={control}
      render={({ field, fieldState: { error } }) => {
        const value = field.value ? (field.value instanceof Date ? field.value : dayjs(field.value).toDate()) : null;
        
        return (
          <DatePickerInput
            {...datePickerProps}
            {...field}
            value={value}
            onChange={(date) => {
              field.onChange(date);
            }}
            label={label}
            description={description}
            required={required}
            error={error?.message}
            valueFormat="YYYY-MM-DD"
          />
        );
      }}
    />
  );
}
