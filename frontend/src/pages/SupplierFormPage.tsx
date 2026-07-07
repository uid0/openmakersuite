/**
 * Supplier Form Page
 * Create/Edit form for suppliers
 */
import { zodResolver } from '@hookform/resolvers/zod';
import {
    Alert,
    Button,
    Group,
    Paper,
    Switch,
    Text,
} from '@mantine/core';
import { IconAlertCircle } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { Controller, Resolver, useForm } from 'react-hook-form';
import { useNavigate, useParams } from 'react-router-dom';
import { FormInput } from '../components/forms/FormInput';
import { FormLayout } from '../components/forms/FormLayout';
import { FormSelect } from '../components/forms/FormSelect';
import { FormTextarea } from '../components/forms/FormTextarea';
import WorkspacePage from '../components/landing/WorkspacePage';
import { inventoryAPI } from '../services/api';
import { SupplierFormData, supplierSchema } from '../utils/formSchemas';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const SupplierFormPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const isEditMode = !!id;

  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const {
    control,
    handleSubmit,
    formState: { errors },
    reset,
  } = useForm<SupplierFormData>({
    // v5 resolvers infer the schema's Zod *input* type (fields with `.default()`
    // are optional pre-parse); this form seeds all fields via defaultValues, so
    // its field values are the *output* shape — assert against SupplierFormData.
    resolver: zodResolver(supplierSchema) as Resolver<SupplierFormData>,
    defaultValues: {
      name: '',
      supplier_type: 'local',
      ordering_adapter: 'none',
      website: '',
      account_number: '',
      tax_free_paperwork_filed: false,
      notes: '',
    },
  });

  useEffect(() => {
    if (isEditMode) {
      loadSupplier();
    }
  }, [id, isEditMode]);

  const loadSupplier = async () => {
    if (!id) return;

    try {
      setLoading(true);
      const response = await inventoryAPI.getSupplier(id);
      const supplier = response.data;

      reset({
        name: supplier.name,
        supplier_type: supplier.supplier_type,
        ordering_adapter: supplier.ordering_adapter || 'none',
        website: supplier.website || '',
        account_number: supplier.account_number || '',
        tax_free_paperwork_filed: supplier.tax_free_paperwork_filed,
        notes: supplier.notes || '',
      });
    } catch (err: any) {
      console.error('Error loading supplier:', err);
      setError(extractErrorMessage(err, 'Failed to load supplier. Please try again.'));
    } finally {
      setLoading(false);
    }
  };

  const onSubmit = async (data: SupplierFormData) => {
    try {
      setSaving(true);
      setError(null);

      if (isEditMode && id) {
        await inventoryAPI.updateSupplier(id, data);
      } else {
        await inventoryAPI.createSupplier(data);
      }

      navigate('/inventory/suppliers');
    } catch (err: any) {
      console.error('Error saving supplier:', err);
      setError(
        extractErrorMessage(err, '') ||
          err.response?.data?.message ||
          'Failed to save supplier. Please try again.'
      );
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <WorkspacePage
        testId="supplier-form-page"
        hero={{ eyebrow: 'Inventory · Supplier', title: 'Supplier', description: 'Loading…' }}
      >
        <Paper withBorder p="md">
          <Text c="dimmed">Loading supplier…</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  return (
    <WorkspacePage
      testId="supplier-form-page"
      hero={{
        eyebrow: 'Inventory · Supplier',
        title: isEditMode ? 'Edit supplier' : 'New supplier',
        description: isEditMode
          ? 'Update contact info, tax-free paperwork status, and notes.'
          : 'Add a new supplier so items can be sourced from it.',
      }}
    >
      {error && (
        <Alert icon={<IconAlertCircle size={16} />} title="Error" color="red">
          {error}
        </Alert>
      )}

      <form onSubmit={handleSubmit(onSubmit)}>
        <Paper p="md" withBorder>
          <FormLayout
            sections={[
              {
                title: 'Basic Information',
                children: (
                  <>
                    <FormInput
                      name="name"
                      control={control}
                      label="Name"
                      placeholder="Supplier name"
                      required
                      error={errors.name?.message}
                    />

                    <FormSelect
                      name="supplier_type"
                      control={control}
                      label="Type"
                      placeholder="Select supplier type"
                      required
                      data={[
                        { value: 'local', label: 'Local' },
                        { value: 'online', label: 'Online' },
                        { value: 'national', label: 'National' },
                      ]}
                      error={errors.supplier_type?.message}
                    />

                    <FormSelect
                      name="ordering_adapter"
                      control={control}
                      label="Ordering adapter"
                      placeholder="Select ordering adapter"
                      description="How the order-pad export builds an order for this supplier."
                      data={[
                        { value: 'none', label: 'None (generic part#,qty pad)' },
                        { value: 'generic_csv', label: 'Generic CSV (part#,qty pad)' },
                        { value: 'amazon', label: 'Amazon (add-to-cart URL)' },
                        { value: 'hdsupply', label: 'HD Supply (Part#,Qty CSV)' },
                      ]}
                      error={errors.ordering_adapter?.message}
                    />

                    <FormInput
                      name="website"
                      control={control}
                      label="Website"
                      placeholder="https://example.com"
                      error={errors.website?.message}
                    />

                    <FormInput
                      name="account_number"
                      control={control}
                      label="Account Number"
                      placeholder="Account number with this supplier"
                      error={errors.account_number?.message}
                    />
                  </>
                ),
              },
              {
                title: 'Additional Information',
                children: (
                  <>
                    <Controller
                      name="tax_free_paperwork_filed"
                      control={control}
                      render={({ field }) => (
                        <Switch
                          label="Tax-Free Paperwork Filed"
                          description="Check if tax-free paperwork has been filed with this supplier"
                          checked={field.value}
                          onChange={field.onChange}
                        />
                      )}
                    />

                    <FormTextarea
                      name="notes"
                      control={control}
                      label="Notes"
                      placeholder="Additional notes about this supplier"
                      error={errors.notes?.message}
                    />
                  </>
                ),
              },
            ]}
          />

          <Group justify="flex-end" mt="xl">
            <Button variant="subtle" onClick={() => navigate(-1)}>
              Cancel
            </Button>
            <Button type="submit" loading={saving}>
              {isEditMode ? 'Save Changes' : 'Create Supplier'}
            </Button>
          </Group>
        </Paper>
      </form>
    </WorkspacePage>
  );
};

export default SupplierFormPage;
