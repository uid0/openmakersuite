/**
 * Custom hook for form validation setup with react-hook-form and Zod
 */
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm, UseFormReturn } from 'react-hook-form';
import { z } from 'zod';

export interface UseFormValidationOptions<T extends z.ZodTypeAny> {
  schema: T;
  defaultValues?: Partial<z.infer<T>>;
  mode?: 'onBlur' | 'onChange' | 'onSubmit' | 'onTouched' | 'all';
}

export function useFormValidation<T extends z.ZodTypeAny>({
  schema,
  defaultValues,
  mode = 'onBlur',
}: UseFormValidationOptions<T>): UseFormReturn<z.infer<T>> {
  return useForm<z.infer<T>>({
    resolver: zodResolver(schema),
    defaultValues: defaultValues as any,
    mode,
  });
}
