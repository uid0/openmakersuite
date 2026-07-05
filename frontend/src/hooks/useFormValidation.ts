/**
 * Custom hook for form validation setup with react-hook-form and Zod
 */
import { zodResolver } from '@hookform/resolvers/zod';
import { FieldValues, Resolver, useForm, UseFormReturn } from 'react-hook-form';
import { z } from 'zod';

// Constrain to schemas whose Zod input *and* output are objects: react-hook-form
// requires `FieldValues` for its generic, and @hookform/resolvers v5 requires the
// schema's input type to extend `FieldValues` to select its resolver overload.
export interface UseFormValidationOptions<T extends z.ZodType<FieldValues, FieldValues>> {
  schema: T;
  defaultValues?: Partial<z.infer<T>>;
  mode?: 'onBlur' | 'onChange' | 'onSubmit' | 'onTouched' | 'all';
}

export function useFormValidation<T extends z.ZodType<FieldValues, FieldValues>>({
  schema,
  defaultValues,
  mode = 'onBlur',
}: UseFormValidationOptions<T>): UseFormReturn<z.infer<T>> {
  return useForm<z.infer<T>>({
    // v5 resolvers type field-values on the schema's Zod *input* (defaulted fields
    // are optional pre-parse); callers seed defaults, so assert the *output* type.
    resolver: zodResolver(schema) as Resolver<z.infer<T>>,
    defaultValues: defaultValues as any,
    mode,
  });
}
