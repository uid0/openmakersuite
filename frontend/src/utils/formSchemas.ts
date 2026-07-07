/**
 * Reusable Zod validation schemas for forms
 */
import { z } from 'zod';

// Common validation patterns
export const emailSchema = z.string().email('Invalid email address');
export const phoneSchema = z.string().regex(/^[\d\s\-()]+$/, 'Invalid phone number format');
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

// Inventory Item Form Schema
export const inventoryItemSchema = z
  .object({
    // Basic Info
    name: z.string().min(1, 'Name is required').max(200, 'Name must be 200 characters or less'),
    description: z.string().optional(),
    sku: z.string().optional(),
    image: z.union([z.instanceof(File), z.string().url(), z.null()]).optional(),
    image_url: z.string().url('Invalid URL').optional().or(z.literal('')),

    // Stock Settings
    current_stock: z.number().int().min(0, 'Stock cannot be negative').default(0),
    minimum_stock: z.number().int().min(0, 'Minimum stock cannot be negative').default(0),
    reorder_quantity: z.number().int().min(1, 'Reorder quantity must be at least 1').default(1),

    // Case-based reordering
    use_case_based_reorder: z.boolean().default(false),
    minimum_cases: z
      .number()
      .int()
      .min(1, 'Minimum cases must be at least 1')
      .optional()
      .nullable(),
    reorder_cases: z
      .number()
      .int()
      .min(1, 'Reorder cases must be at least 1')
      .optional()
      .nullable(),

    // Category & Location
    category: z.number().int().positive().optional().nullable(),
    location: z.union([z.string(), z.number().int().positive()]).optional().nullable(),
    shelf_position: z.enum(['top', 'bottom', '']).optional(),

    // Hazmat
    is_hazardous: z.boolean().default(false),
    msds_url: z.string().url('Invalid URL').optional().or(z.literal('')),
    msds_file: z.union([z.instanceof(File), z.null()]).optional(),
    nfpa_health_hazard: z
      .number()
      .int()
      .min(0, 'Rating must be between 0 and 4')
      .max(4, 'Rating must be between 0 and 4')
      .optional()
      .nullable(),
    nfpa_fire_hazard: z
      .number()
      .int()
      .min(0, 'Rating must be between 0 and 4')
      .max(4, 'Rating must be between 0 and 4')
      .optional()
      .nullable(),
    nfpa_instability_hazard: z
      .number()
      .int()
      .min(0, 'Rating must be between 0 and 4')
      .max(4, 'Rating must be between 0 and 4')
      .optional()
      .nullable(),
    nfpa_special_hazards: z.string().max(20, 'Special hazards must be 20 characters or less').optional(),

    // Ownership
    ownership_type: z.enum(['user', 'group', 'space']).default('space'),
    owning_user: z.number().int().positive().optional().nullable(),
    owning_group: z.number().int().positive().optional().nullable(),

    // Serialized-component tracking — when enabled, individual units are
    // tracked by serial number and the mode selects the lifecycle branch.
    is_serialized: z.boolean().optional().default(false),
    serial_tracking_mode: z.enum(['consumable', 'reusable']).nullable().optional(),

    // Other
    is_active: z.boolean().default(true),
    notes: z.string().optional(),
  })
  .refine(
    (data) => {
      if (data.use_case_based_reorder) {
        return (
          data.minimum_cases !== null &&
          data.minimum_cases !== undefined &&
          data.reorder_cases !== null &&
          data.reorder_cases !== undefined
        );
      }
      return true;
    },
    {
      message: 'Minimum cases and reorder cases are required when case-based reordering is enabled',
      path: ['minimum_cases'],
    }
  )
  .refine(
    (data) => {
      if (data.is_hazardous && !data.msds_url && !data.msds_file) {
        return false;
      }
      return true;
    },
    {
      message: 'MSDS URL or file is required for hazardous materials',
      path: ['msds_url'],
    }
  );

export type InventoryItemFormData = z.infer<typeof inventoryItemSchema>;

// Supplier Form Schema
export const supplierSchema = z.object({
  name: z.string().min(1, 'Name is required').max(200, 'Name must be 200 characters or less'),
  supplier_type: z.enum(['local', 'online', 'national'], {
    required_error: 'Supplier type is required',
  }),
  // Selects the order-pad export artifact for this supplier (op-svpq).
  ordering_adapter: z
    .enum(['none', 'generic_csv', 'amazon', 'hdsupply'])
    .default('none'),
  website: z.string().url('Invalid URL').optional().or(z.literal('')),
  account_number: z.string().max(100, 'Account number must be 100 characters or less').optional(),
  tax_free_paperwork_filed: z.boolean().default(false),
  notes: z.string().optional(),
});

export type SupplierFormData = z.infer<typeof supplierSchema>;

// Asset Form Schema
export const assetFormSchema = z
  .object({
    // Basics — the minimum needed to describe what this asset is.
    name: z.string().min(1, 'Name is required').max(200, 'Name must be 200 characters or less'),
    description: z.string().optional(),
    serial_number: z.string().max(100, 'Serial number must be 100 characters or less').optional(),
    asset_tag: z.string().max(50, 'Asset tag must be 50 characters or less').optional(),
    inventory_item: z.number().int().positive().optional().nullable(),
    category: z.number().int().positive().optional().nullable(),
    location: z.union([z.string(), z.number().int().positive()]).optional().nullable(),

    // Acquisition — where it came from + how much it cost.
    date_received: z.string().optional().nullable(),
    amount_paid: z.number().min(0, 'Amount cannot be negative').default(0),
    is_donation: z.boolean().default(false),
    donor_name: z.string().max(200, 'Donor name must be 200 characters or less').optional(),

    // Media — single image + optional manual / wiki / product links.
    image: z.union([z.instanceof(File), z.string().url(), z.null()]).optional(),
    manual_pdf: z.union([z.instanceof(File), z.null()]).optional(),
    wiki_page_url: z.string().url('Invalid URL').optional().or(z.literal('')),
    product_url: z.string().url('Invalid URL').optional().or(z.literal('')),

    // Status & ownership — who owns it, what lifecycle state it's in.
    status: z
      .enum(['implementing', 'testing', 'active', 'maintenance', 'retired', 'lost', 'donated_out'])
      .default('active'),
    ownership_type: z.enum(['user', 'group', 'space']).default('space'),
    owning_user: z.number().int().positive().optional().nullable(),
    owning_group: z.number().int().positive().optional().nullable(),
    is_active: z.boolean().default(true),

    // Operating requirements — collapsed under an "Advanced" section.
    // ``circuit`` and ``mac_address`` used to live here as free-text;
    // they're now superseded by the Asset.breaker FK and the ForgeKey
    // device-pairing flow respectively. Dropped.
    // Note: ``maintenance_plan`` (freeform Textarea) is replaced by
    // structured MaintenanceItem rows created via a modal in the
    // Maintenance section.
    needs_compressed_air: z.boolean().default(false),
    needs_ventilation: z.boolean().default(false),
    is_chargeable: z.boolean().default(false),
    training_required: z.boolean().default(false),
    required_certifications: z.array(z.number().int().positive()).default([]),
    report_only: z.boolean().default(false),
    notes: z.string().optional(),
    condition_notes: z.string().optional(),
  })
  .refine(
    (data) => {
      if (data.ownership_type === 'user' && !data.owning_user) {
        return false;
      }
      return true;
    },
    {
      message: 'Owning user is required when ownership type is user',
      path: ['owning_user'],
    }
  )
  .refine(
    (data) => {
      if (data.ownership_type === 'group' && !data.owning_group) {
        return false;
      }
      return true;
    },
    {
      message: 'Owning group is required when ownership type is group',
      path: ['owning_group'],
    }
  );

export type AssetFormData = z.infer<typeof assetFormSchema>;

// Webhook Form Schema
export const webhookSchema = z.object({
  name: z.string().min(1, 'Name is required').max(200, 'Name must be 200 characters or less'),
  description: z.string().optional(),
  url: z.string().url('Invalid URL format').min(1, 'URL is required'),
  event_type: z.enum([
    'reorder_request_created',
    'reorder_request_approved',
    'reorder_request_ordered',
    'reorder_request_received',
    'item_low_stock',
    'purchase_order_created',
    'delivery_received',
    'fixture_refill_requested',
    'location_checkin',
    'location_feedback',
    'security_report',
  ]),
  is_active: z.boolean().default(true),
  secret: z.string().optional(),
  headers: z
    .string()
    .optional()
    .refine(
      (val) => {
        if (!val || val.trim() === '') return true;
        try {
          JSON.parse(val);
          return true;
        } catch {
          return false;
        }
      },
      {
        message: 'Headers must be valid JSON',
      }
    ),
});

export type WebhookFormData = z.infer<typeof webhookSchema>;

// Maintenance Item Form Schema
export const maintenanceItemFormSchema = z.object({
  title: z.string().min(1, 'Title is required').max(200),
  description: z.string().optional().default(''),
  instructions: z.string().optional().default(''),
  estimated_time_minutes: z.number().int().min(1).nullable().optional(),
  estimated_cost: z.number().min(0, 'Cost must be zero or greater').default(0),
  interval_days: z.number().int().min(1).nullable().optional(),
  is_active: z.boolean().default(true),
});

export type MaintenanceItemFormData = z.infer<typeof maintenanceItemFormSchema>;
