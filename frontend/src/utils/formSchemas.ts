/**
 * Reusable Zod validation schemas for forms
 */
import { z } from 'zod';

// Common validation patterns
export const emailSchema = z.string().email('Invalid email address');
export const phoneSchema = z.string().regex(/^[\d\s\-\(\)]+$/, 'Invalid phone number format');
export const urlSchema = z.string().url('Invalid URL format');
export const positiveNumberSchema = z.number().positive('Must be a positive number');
export const nonNegativeNumberSchema = z.number().min(0, 'Must be zero or greater');

// Common field schemas
export const requiredString = z.string().min(1, 'This field is required');
export const optionalString = z.string().optional();
export const requiredNumber = z.number({ required_error: 'This field is required' });
export const optionalNumber = z.number().optional();

// Date schemas
export const dateSchema = z.date();
export const optionalDateSchema = z.date().optional();

// File schemas
export const imageFileSchema = z
  .instanceof(File)
  .refine((file) => file.type.startsWith('image/'), 'File must be an image')
  .refine((file) => file.size <= 5 * 1024 * 1024, 'Image size must be less than 5MB');

export const fileSchema = z
  .instanceof(File)
  .refine((file) => file.size <= 10 * 1024 * 1024, 'File size must be less than 10MB');

// Helper function to create a schema with custom error messages
export const createRequiredSchema = <T extends z.ZodTypeAny>(
  schema: T,
  message?: string
): z.ZodType<z.infer<T>> => {
  return schema.refine((val) => val !== undefined && val !== null, {
    message: message || 'This field is required',
  });
};

// Common validation helpers
export const minLength = (min: number, message?: string) =>
  z.string().min(min, message || `Must be at least ${min} characters`);

export const maxLength = (max: number, message?: string) =>
  z.string().max(max, message || `Must be no more than ${max} characters`);

export const minValue = (min: number, message?: string) =>
  z.number().min(min, message || `Must be at least ${min}`);

export const maxValue = (max: number, message?: string) =>
  z.number().max(max, message || `Must be no more than ${max}`);
