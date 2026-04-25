/**
 * Asset Form Page
 * Create/Edit form for assets with all fields
 */
import { zodResolver } from '@hookform/resolvers/zod';
import { Alert, Button, Group, Modal, Paper, Stack, Switch, Text, TextInput, Title } from '@mantine/core';
import { IconAlertCircle } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { Controller, useForm } from 'react-hook-form';
import { useNavigate, useParams } from 'react-router-dom';
import { FormFileUpload } from '../components/forms/FormFileUpload';
import { FormImageUpload } from '../components/forms/FormImageUpload';
import { FormInput } from '../components/forms/FormInput';
import { FormLayout } from '../components/forms/FormLayout';
import { FormNumberInput } from '../components/forms/FormNumberInput';
import { FormSelect } from '../components/forms/FormSelect';
import { FormTextarea } from '../components/forms/FormTextarea';
import { assetsAPI, inventoryAPI, sigAPI } from '../services/api';
import { Asset, Category, InventoryItem, Location, SIG } from '../types';
import { AssetFormData, assetFormSchema } from '../utils/formSchemas';

const AssetFormPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const isEditMode = !!id;

  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [inventoryItems, setInventoryItems] = useState<InventoryItem[]>([]);
  const [sigs, setSigs] = useState<SIG[]>([]);
  const [showCreateCategory, setShowCreateCategory] = useState(false);
  const [newCategoryName, setNewCategoryName] = useState('');

  const {
    control,
    handleSubmit,
    watch,
    setValue,
    reset,
  } = useForm<AssetFormData>({
    resolver: zodResolver(assetFormSchema),
    defaultValues: {
      name: '',
      description: '',
      serial_number: '',
      asset_tag: '',
      inventory_item: null,
      date_received: null,
      amount_paid: 0,
      is_donation: false,
      donor_name: '',
      location: null,
      category: null,
      manufacturer: null,
      manufacturer_name: '',
      circuit: '',
      mac_address: '',
      needs_compressed_air: false,
      needs_ventilation: false,
      is_chargeable: false,
      image: null,
      image_url: '',
      manual_pdf: null,
      wiki_page_url: '',
      product_url: '',
      maintenance_plan: '',
      status: 'active',
      ownership_type: 'space',
      owning_user: null,
      owning_group: null,
      groups_can_enable: [],
      is_active: true,
      report_only: false,
      notes: '',
      condition_notes: '',
    },
  });

  const isDonation = watch('is_donation');
  const ownershipType = watch('ownership_type');

  useEffect(() => {
    loadInitialData();
    if (isEditMode) {
      loadAsset();
    }
  }, [id, isEditMode]);

  const loadInitialData = async () => {
    try {
      const [categoriesRes, locationsRes, itemsRes, sigsRes] = await Promise.all([
        inventoryAPI.listCategories(),
        inventoryAPI.listLocations(),
        inventoryAPI.listItems(),
        sigAPI.listMySIGs(),
      ]);
      setCategories(categoriesRes.data.results || []);
      setLocations(locationsRes.data.results || []);
      setInventoryItems(itemsRes.data.results || []);
      setSigs(sigsRes.data.results || []);
    } catch (err) {
      console.error('Error loading initial data:', err);
      setError('Failed to load form data. Please refresh the page.');
      // Ensure arrays remain empty arrays even on error
      setCategories([]);
      setLocations([]);
      setInventoryItems([]);
      setSigs([]);
    }
  };

  const loadAsset = async () => {
    if (!id) return;

    try {
      setLoading(true);
      const response = await assetsAPI.getAsset(id);
      const asset = response.data;

      // Map asset data to form
      reset({
        name: asset.name,
        description: asset.description || '',
        serial_number: asset.serial_number || '',
        asset_tag: asset.asset_tag || '',
        inventory_item: asset.inventory_item ? Number(asset.inventory_item) : null,
        date_received: asset.date_received || null,
        amount_paid: parseFloat(asset.amount_paid) || 0,
        is_donation: asset.is_donation,
        donor_name: asset.donor_name || '',
        location: asset.location ? (typeof asset.location === 'string' ? asset.location : Number(asset.location)) : null,
        category: asset.category ? Number(asset.category) : null,
        manufacturer: asset.manufacturer ? Number(asset.manufacturer) : null,
        manufacturer_name: asset.manufacturer_name || '',
        circuit: asset.circuit || '',
        mac_address: asset.mac_address || '',
        needs_compressed_air: asset.needs_compressed_air,
        needs_ventilation: asset.needs_ventilation,
        is_chargeable: asset.is_chargeable,
        image_url: asset.image_url || '',
        wiki_page_url: asset.wiki_page_url || '',
        product_url: asset.product_url || '',
        maintenance_plan: asset.maintenance_plan || '',
        status: asset.status as any,
        ownership_type: asset.ownership_type,
        owning_user: asset.owning_user ? Number(asset.owning_user) : null,
        owning_group: asset.owning_group ? Number(asset.owning_group) : null,
        groups_can_enable: asset.groups_can_enable || [],
        is_active: asset.is_active,
        report_only: asset.report_only,
        notes: asset.notes || '',
        condition_notes: asset.condition_notes || '',
      });
    } catch (err) {
      console.error('Error loading asset:', err);
      setError('Failed to load asset. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleCreateCategory = async () => {
    if (!newCategoryName.trim()) return;

    try {
      const response = await inventoryAPI.createCategory({ name: newCategoryName.trim() });
      setCategories([...categories, response.data]);
      setValue('category', response.data.id);
      setShowCreateCategory(false);
      setNewCategoryName('');
    } catch (err) {
      console.error('Error creating category:', err);
      alert('Failed to create category. Please try again.');
    }
  };

  const onSubmit = async (data: AssetFormData) => {
    try {
      setSaving(true);
      setError(null);

      // Prepare form data
      const formData = new FormData();

      // Add all fields
      Object.entries(data).forEach(([key, value]) => {
        if (value === null || value === undefined || value === '') {
          return;
        }

        if (key === 'image' && value instanceof File) {
          formData.append('image', value);
        } else if (key === 'manual_pdf' && value instanceof File) {
          formData.append('manual_pdf', value);
        } else if (key === 'image_url' && typeof value === 'string') {
          formData.append('image_url', value);
        } else if (key === 'groups_can_enable' && Array.isArray(value)) {
          value.forEach((groupId) => formData.append('groups_can_enable', String(groupId)));
        } else if (typeof value === 'boolean') {
          formData.append(key, String(value));
        } else if (typeof value === 'number') {
          formData.append(key, String(value));
        } else if (typeof value === 'string') {
          formData.append(key, value);
        }
      });

      // Handle location - can be string or number
      if (data.location) {
        if (typeof data.location === 'string') {
          formData.append('location', data.location);
        } else {
          formData.append('location', String(data.location));
        }
      }

      // Save asset
      let savedAsset: Asset;
      if (isEditMode && id) {
        savedAsset = (await assetsAPI.updateAsset(id, formData)).data;
      } else {
        savedAsset = (await assetsAPI.createAsset(formData)).data;
      }

      navigate(`/assets/${savedAsset.id}`);
    } catch (err: any) {
      console.error('Error saving asset:', err);
      setError(err.response?.data?.detail || 'Failed to save asset. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <Paper p="md">
        <Text>Loading...</Text>
      </Paper>
    );
  }

  const categoryOptions = [
    ...categories.map((c) => ({ value: String(c.id), label: c.name })),
    { value: '__create_new__', label: '+ Create New Category' },
  ];

  const locationOptions = locations.map((l) => ({ value: String(l.id), label: l.name }));

  const inventoryItemOptions = inventoryItems.map((item) => ({
    value: String(item.id),
    label: item.name,
  }));

  const statusOptions = [
    { value: 'implementing', label: 'Implementing' },
    { value: 'testing', label: 'Testing' },
    { value: 'active', label: 'Active' },
    { value: 'maintenance', label: 'Maintenance' },
    { value: 'retired', label: 'Retired' },
    { value: 'lost', label: 'Lost' },
    { value: 'donated_out', label: 'Donated Out' },
  ];

  const ownershipTypeOptions = [
    { value: 'space', label: 'Space (Makerspace)' },
    { value: 'group', label: 'Group (SIG)' },
    { value: 'user', label: 'User' },
  ];

  const sigOptions = sigs.map((sig) => ({ value: String(sig.id), label: sig.name }));

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={2}>{isEditMode ? 'Edit Asset' : 'Create Asset'}</Title>
        <Button variant="subtle" onClick={() => navigate(-1)}>
          Cancel
        </Button>
      </Group>

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
                      required
                      placeholder="Asset name or model"
                    />
                    <FormTextarea
                      name="description"
                      control={control}
                      label="Description"
                      placeholder="Detailed description of the asset"
                    />
                    <FormInput
                      name="serial_number"
                      control={control}
                      label="Serial Number"
                      placeholder="Serial number or unique identifier"
                    />
                    {isEditMode && (
                      <FormInput
                        name="asset_tag"
                        control={control}
                        label="Asset Tag"
                        placeholder="Auto-generated"
                        disabled
                      />
                    )}
                    <Controller
                      name="inventory_item"
                      control={control}
                      render={({ field, fieldState: { error } }) => (
                        <FormSelect
                          name="inventory_item"
                          control={control}
                          label="Inventory Item Type"
                          data={[{ value: '', label: 'None' }, ...inventoryItemOptions]}
                          searchable
                          value={field.value ? String(field.value) : ''}
                          onChange={(value) => field.onChange(value ? Number(value) : null)}
                          error={error?.message}
                        />
                      )}
                    />
                  </>
                ),
              },
              {
                title: 'Acquisition Information',
                children: (
                  <>
                    <Controller
                      name="date_received"
                      control={control}
                      render={({ field, fieldState: { error } }) => (
                        <FormInput
                          name="date_received"
                          control={control}
                          label="Date Received"
                          type="date"
                          value={field.value || ''}
                          onChange={(e) => field.onChange(e.target.value || null)}
                          error={error?.message}
                        />
                      )}
                    />
                    <FormNumberInput
                      name="amount_paid"
                      control={control}
                      label="Amount Paid"
                      min={0}
                      step={0.01}
                    />
                    <div>
                      <Switch
                        label="Is Donation"
                        checked={isDonation}
                        onChange={(e) => setValue('is_donation', e.currentTarget.checked)}
                      />
                    </div>
                    {isDonation && (
                      <FormInput
                        name="donor_name"
                        control={control}
                        label="Donor Name"
                        placeholder="Name of donor"
                      />
                    )}
                  </>
                ),
              },
              {
                title: 'Location & Category',
                children: (
                  <>
                    <Controller
                      name="category"
                      control={control}
                      render={({ field, fieldState: { error } }) => (
                        <FormSelect
                          name="category"
                          control={control}
                          label="Category"
                          data={categoryOptions}
                          searchable
                          value={field.value ? String(field.value) : ''}
                          onChange={(value) => {
                            if (value === '__create_new__') {
                              setShowCreateCategory(true);
                            } else {
                              field.onChange(value ? Number(value) : null);
                            }
                          }}
                          error={error?.message}
                        />
                      )}
                    />
                    <Controller
                      name="location"
                      control={control}
                      render={({ field, fieldState: { error } }) => (
                        <FormSelect
                          name="location"
                          control={control}
                          label="Location"
                          data={[{ value: '', label: 'None' }, ...locationOptions]}
                          searchable
                          value={field.value ? String(field.value) : ''}
                          onChange={(value) =>
                            field.onChange(value ? (isNaN(Number(value)) ? value : Number(value)) : null)
                          }
                          error={error?.message}
                        />
                      )}
                    />
                  </>
                ),
              },
              {
                title: 'Operational Requirements',
                children: (
                  <>
                    <FormInput
                      name="circuit"
                      control={control}
                      label="Circuit"
                      placeholder="Circuit identification"
                    />
                    <FormInput
                      name="mac_address"
                      control={control}
                      label="MAC Address"
                      placeholder="AA:BB:CC:11:22:33"
                    />
                    <div>
                      <Switch
                        label="Needs Compressed Air"
                        checked={watch('needs_compressed_air')}
                        onChange={(e) => setValue('needs_compressed_air', e.currentTarget.checked)}
                      />
                    </div>
                    <div>
                      <Switch
                        label="Needs Ventilation"
                        checked={watch('needs_ventilation')}
                        onChange={(e) => setValue('needs_ventilation', e.currentTarget.checked)}
                      />
                    </div>
                    <div>
                      <Switch
                        label="Is Chargeable"
                        checked={watch('is_chargeable')}
                        onChange={(e) => setValue('is_chargeable', e.currentTarget.checked)}
                      />
                    </div>
                  </>
                ),
              },
              {
                title: 'Media & Documentation',
                children: (
                  <>
                    <FormInput
                      name="image_url"
                      control={control}
                      label="Image URL"
                      placeholder="URL to download image from"
                    />
                    <FormImageUpload
                      name="image"
                      control={control}
                      label="Image Upload"
                      description="Upload an image file (alternative to URL)"
                    />
                    <FormFileUpload
                      name="manual_pdf"
                      control={control}
                      label="Manual PDF Upload"
                      accept={['application/pdf']}
                    />
                    <FormInput
                      name="wiki_page_url"
                      control={control}
                      label="Wiki Page URL"
                      placeholder="Link to wiki page for this asset"
                    />
                    <FormInput
                      name="product_url"
                      control={control}
                      label="Product URL"
                      placeholder="Link to product page or documentation"
                    />
                  </>
                ),
              },
              {
                title: 'Maintenance',
                children: (
                  <>
                    <FormTextarea
                      name="maintenance_plan"
                      control={control}
                      label="Maintenance Plan"
                      placeholder="Maintenance schedule and procedures"
                    />
                    <FormTextarea
                      name="condition_notes"
                      control={control}
                      label="Condition Notes"
                      placeholder="Notes about the asset's condition, maintenance history, etc."
                    />
                  </>
                ),
              },
              {
                title: 'Status & Ownership',
                children: (
                  <>
                    <FormSelect
                      name="status"
                      control={control}
                      label="Status"
                      data={statusOptions}
                      required
                    />
                    <Controller
                      name="ownership_type"
                      control={control}
                      render={({ field, fieldState: { error } }) => (
                        <FormSelect
                          name="ownership_type"
                          control={control}
                          label="Ownership Type"
                          data={ownershipTypeOptions}
                          value={field.value}
                          onChange={(value) => {
                            field.onChange(value as any);
                            // Clear owning_user/owning_group when changing ownership type
                            if (value !== 'user') setValue('owning_user', null);
                            if (value !== 'group') setValue('owning_group', null);
                          }}
                          error={error?.message}
                        />
                      )}
                    />
                    {ownershipType === 'group' && (
                      <Controller
                        name="owning_group"
                        control={control}
                        render={({ field, fieldState: { error } }) => (
                          <FormSelect
                            name="owning_group"
                            control={control}
                            label="Owning Group (SIG)"
                            data={sigOptions}
                            searchable
                            value={field.value ? String(field.value) : ''}
                            onChange={(value) => field.onChange(value ? Number(value) : null)}
                            error={error?.message}
                          />
                        )}
                      />
                    )}
                    <div>
                      <Switch
                        label="Active"
                        checked={watch('is_active')}
                        onChange={(e) => setValue('is_active', e.currentTarget.checked)}
                      />
                    </div>
                    <div>
                      <Switch
                        label="Report Only"
                        checked={watch('report_only')}
                        onChange={(e) => setValue('report_only', e.currentTarget.checked)}
                        description="If enabled, this asset only supports problem reporting (no enable/disable functionality)"
                      />
                    </div>
                  </>
                ),
              },
              {
                title: 'Additional Notes',
                children: (
                  <>
                    <FormTextarea
                      name="notes"
                      control={control}
                      label="Notes"
                      placeholder="General notes about the asset"
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
              {isEditMode ? 'Save Changes' : 'Create Asset'}
            </Button>
          </Group>
        </Paper>
      </form>

      {/* Create Category Modal */}
      <Modal
        opened={showCreateCategory}
        onClose={() => setShowCreateCategory(false)}
        title="Create New Category"
      >
        <Stack gap="md">
          <TextInput
            label="Category Name"
            value={newCategoryName}
            onChange={(e) => setNewCategoryName(e.target.value)}
            placeholder="Enter category name"
          />
          <Group justify="flex-end">
            <Button variant="subtle" onClick={() => setShowCreateCategory(false)}>
              Cancel
            </Button>
            <Button onClick={handleCreateCategory} disabled={!newCategoryName.trim()}>
              Create
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
};

export default AssetFormPage;
