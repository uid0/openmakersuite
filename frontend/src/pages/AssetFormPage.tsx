/**
 * Asset Form Page — create / edit a hard-asset record.
 *
 * Re-tuned from the older version that grew sprawling over time. The
 * new shape is:
 *
 *  1. Basics — name, description, serial, asset tag (edit-only),
 *     inventory item type, category, location.
 *  2. Acquisition — date received, amount paid, donor info if
 *     donation.
 *  3. Status & ownership — lifecycle status, who owns it.
 *  4. Supplies — opt-in toggle that asks "does this asset use
 *     consumable supplies?" then a structured list editor.
 *  5. Maintenance — opt-in toggle that asks "schedule preventive
 *     maintenance?" then a modal-driven PM task list.
 *  6. Documentation — image, manual PDF, wiki / product links.
 *  7. Advanced — needs-compressed-air / ventilation / chargeable /
 *     report-only / condition / general notes, collapsed under a
 *     "More options" affordance for the staff member who needs them.
 *
 * Dropped (stale): freeform ``circuit`` text (now ``Asset.breaker``
 * FK), ``mac_address`` (now ForgeKey device-pairing flow),
 * ``image_url`` (redundant with upload), freeform ``maintenance_plan``
 * Textarea (replaced by structured MaintenanceItem rows),
 * ``groups_can_enable`` (was hidden anyway).
 */
import { zodResolver } from '@hookform/resolvers/zod';
import {
  Alert,
  Box,
  Button,
  Collapse,
  Group,
  Modal,
  MultiSelect,
  Paper,
  Stack,
  Switch,
  Text,
  TextInput,
} from '@mantine/core';
import {
  IconAlertCircle,
  IconChevronDown,
  IconChevronUp,
} from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { Controller, useForm } from 'react-hook-form';
import { useNavigate, useParams } from 'react-router-dom';

import AssetMaintenanceSection from '../components/assets/AssetMaintenanceSection';
import { PendingMaintenanceItem } from '../components/assets/AssetMaintenanceModal';
import AssetSuppliesSection, {
  PendingAssetPart,
} from '../components/assets/AssetSuppliesSection';
import { FormFileUpload } from '../components/forms/FormFileUpload';
import { FormImageUpload } from '../components/forms/FormImageUpload';
import { FormInput } from '../components/forms/FormInput';
import { FormLayout } from '../components/forms/FormLayout';
import { FormNumberInput } from '../components/forms/FormNumberInput';
import { FormSelect } from '../components/forms/FormSelect';
import { FormTextarea } from '../components/forms/FormTextarea';
import WorkspacePage from '../components/landing/WorkspacePage';
import { useSetBreadcrumb } from '../contexts/BreadcrumbContext';
import {
  assetPartsAPI,
  assetsAPI,
  ForgeKeyCertificationOption,
  inventoryAPI,
  lockersAPI,
  maintenanceAPI,
  sigAPI,
} from '../services/api';
import { Asset, Category, InventoryItem, Location, SIG } from '../types';
import { AssetFormData, assetFormSchema } from '../utils/formSchemas';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const STATUS_OPTIONS = [
  { value: 'implementing', label: 'Implementing' },
  { value: 'testing', label: 'Testing' },
  { value: 'active', label: 'Active' },
  { value: 'maintenance', label: 'Maintenance' },
  { value: 'retired', label: 'Retired' },
  { value: 'lost', label: 'Lost' },
  { value: 'donated_out', label: 'Donated out' },
];

const OWNERSHIP_OPTIONS = [
  { value: 'space', label: 'Space (Makerspace)' },
  { value: 'group', label: 'Group (SIG)' },
  { value: 'user', label: 'User' },
];

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
  const [certifications, setCertifications] = useState<ForgeKeyCertificationOption[]>([]);
  const [assetName, setAssetName] = useState<string | undefined>(undefined);

  // Show the asset name as the trailing breadcrumb in edit mode, so the
  // header reads "Home › Assets › <Asset name>" instead of "… › Edit".
  useSetBreadcrumb(isEditMode ? assetName : undefined);

  const [showCreateCategory, setShowCreateCategory] = useState(false);
  const [newCategoryName, setNewCategoryName] = useState('');

  const [advancedOpen, setAdvancedOpen] = useState(false);

  // Supplies + maintenance live in form state until the asset is saved
  // (both server resources need the asset.id, which doesn't exist on
  // create). After save, the parent form chains the creates.
  const [usesSupplies, setUsesSupplies] = useState(false);
  const [supplies, setSupplies] = useState<PendingAssetPart[]>([]);
  const [pmEnabled, setPmEnabled] = useState(false);
  const [maintenanceItems, setMaintenanceItems] = useState<PendingMaintenanceItem[]>([]);

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
      needs_compressed_air: false,
      needs_ventilation: false,
      is_chargeable: false,
      training_required: false,
      required_certifications: [],
      image: null,
      manual_pdf: null,
      wiki_page_url: '',
      product_url: '',
      status: 'active',
      ownership_type: 'space',
      owning_user: null,
      owning_group: null,
      is_active: true,
      report_only: false,
      notes: '',
      condition_notes: '',
    },
  });

  const isDonation = watch('is_donation');
  const ownershipType = watch('ownership_type');

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      inventoryAPI.listCategories(),
      inventoryAPI.listLocations(),
      inventoryAPI.listItems(),
      sigAPI.listMySIGs(),
      // Certifications are optional — a deploy without the lockers
      // app, or a 403 from a non-staff caller, shouldn't take the
      // whole form down. Wrap in Promise.resolve so an automocked
      // (test) lockersAPI that returns undefined doesn't blow up
      // Promise.all, and resolve to an empty list on failure.
      Promise.resolve(lockersAPI.listAvailableCertifications()).catch(() => ({ data: [] })),
    ])
      .then(([cats, locs, items, sigsRes, certsRes]) => {
        if (cancelled) return;
        setCategories(cats.data.results || []);
        setLocations(locs.data.results || []);
        setInventoryItems(items.data.results || []);
        setSigs(sigsRes.data.results || []);
        setCertifications(certsRes?.data || []);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('Error loading initial data:', err);
        setError('Failed to load form data. Please refresh the page.');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!isEditMode || !id) return;
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const response = await assetsAPI.getAsset(id);
        if (cancelled) return;
        const asset = response.data;
        setAssetName(asset.name);
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
          location:
            asset.location != null
              ? typeof asset.location === 'string'
                ? asset.location
                : Number(asset.location)
              : null,
          category: asset.category ? Number(asset.category) : null,
          needs_compressed_air: asset.needs_compressed_air,
          needs_ventilation: asset.needs_ventilation,
          is_chargeable: asset.is_chargeable,
          training_required: asset.training_required,
          required_certifications: asset.required_certifications || [],
          wiki_page_url: asset.wiki_page_url || '',
          product_url: asset.product_url || '',
          status: asset.status as AssetFormData['status'],
          ownership_type: asset.ownership_type,
          owning_user: asset.owning_user ? Number(asset.owning_user) : null,
          owning_group: asset.owning_group ? Number(asset.owning_group) : null,
          is_active: asset.is_active,
          report_only: asset.report_only,
          notes: asset.notes || '',
          condition_notes: asset.condition_notes || '',
        });

        // Hydrate the supplies + maintenance sections from existing rows
        // so an edit-mode visit shows what's already there.
        try {
          const partsResponse = await assetPartsAPI.listAssetParts({ asset: asset.id });
          if (!cancelled && partsResponse.data.results.length > 0) {
            setUsesSupplies(true);
            setSupplies(
              partsResponse.data.results.map((row) => ({
                key: row.id,
                part: row.part,
                quantity_needed: row.quantity_needed,
                is_required: row.is_required,
                maintenance_interval_days: row.maintenance_interval_days,
                notes: row.notes,
              })),
            );
          }
        } catch (err) {
          console.warn('Failed to load asset parts; supplies section will start empty', err);
        }
        try {
          const miResponse = await maintenanceAPI.listItems({ asset: asset.id });
          if (!cancelled && miResponse.data.results.length > 0) {
            setPmEnabled(true);
            setMaintenanceItems(
              miResponse.data.results.map((item) => ({
                key: item.id,
                title: item.title,
                description: item.description,
                instructions: item.instructions,
                estimated_time_minutes: item.estimated_time_minutes,
                interval_days: item.interval_days,
              })),
            );
          }
        } catch (err) {
          console.warn('Failed to load maintenance items; PM section will start empty', err);
        }
      } catch (err) {
        if (cancelled) return;
        console.error('Error loading asset:', err);
        setError('Failed to load asset. Please try again.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id, isEditMode, reset]);

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
      setError('Failed to create category.');
    }
  };

  const onSubmit = async (data: AssetFormData) => {
    try {
      setSaving(true);
      setError(null);

      const formData = new FormData();
      Object.entries(data).forEach(([key, value]) => {
        if (value === null || value === undefined || value === '') return;
        if (key === 'image' && value instanceof File) {
          formData.append('image', value);
        } else if (key === 'manual_pdf' && value instanceof File) {
          formData.append('manual_pdf', value);
        } else if (Array.isArray(value)) {
          // multipart/form-data with repeated keys is how DRF
          // ingests M2M ids — `required_certifications=1` repeated
          // once per selected id.
          value.forEach((v) => formData.append(key, String(v)));
        } else if (typeof value === 'boolean') {
          formData.append(key, String(value));
        } else if (typeof value === 'number') {
          formData.append(key, String(value));
        } else if (typeof value === 'string') {
          formData.append(key, value);
        }
      });
      if (data.location) {
        formData.append(
          'location',
          typeof data.location === 'string' ? data.location : String(data.location),
        );
      }

      const savedAsset: Asset = isEditMode && id
        ? (await assetsAPI.updateAsset(id, formData)).data
        : (await assetsAPI.createAsset(formData)).data;

      // Chain the supplies + maintenance creates after the asset exists.
      // We don't bail on the asset save if a child create fails — the
      // user gets a non-fatal warning instead so they can fix the
      // problem from the asset detail page.
      const childErrors: string[] = [];

      if (usesSupplies) {
        for (const supply of supplies) {
          if (!supply.part) continue;
          try {
            // If this row already exists (came from the server with a UUID
            // key), skip — we don't update parts in this form pass yet.
            if (!supply.key.startsWith('pending-')) continue;
            await assetPartsAPI.createAssetPart({
              asset: savedAsset.id,
              part: String(supply.part),
              quantity_needed: supply.quantity_needed,
              is_required: supply.is_required,
              maintenance_interval_days: supply.maintenance_interval_days,
              notes: supply.notes,
            });
          } catch (err) {
            childErrors.push(`supply "${supply.part}"`);
          }
        }
      }

      if (pmEnabled) {
        for (const item of maintenanceItems) {
          if (!item.title.trim()) continue;
          try {
            if (!item.key.startsWith('pending-mi-')) continue;
            await maintenanceAPI.createItem({
              asset: savedAsset.id,
              title: item.title,
              description: item.description,
              instructions: item.instructions,
              estimated_time_minutes: item.estimated_time_minutes,
              interval_days: item.interval_days,
            });
          } catch (err) {
            childErrors.push(`maintenance task "${item.title}"`);
          }
        }
      }

      if (childErrors.length > 0) {
        // Asset saved, but at least one supply / PM failed to attach.
        // Surface a soft warning via query string so the detail page
        // can show it.
        navigate(
          `/assets/${savedAsset.id}?warning=${encodeURIComponent(
            `Saved, but couldn't attach: ${childErrors.join(', ')}`,
          )}`,
        );
      } else {
        navigate(`/assets/${savedAsset.id}`);
      }
    } catch (err) {
      console.error('Error saving asset:', err);
      setError(extractErrorMessage(err, 'Failed to save asset. Please try again.'));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <WorkspacePage
        testId="asset-form-page"
        hero={{ eyebrow: 'Assets', title: 'Asset', description: 'Loading…' }}
      >
        <Paper withBorder p="md">
          <Text c="dimmed">Loading asset…</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  const categoryOptions = [
    ...categories.map((c) => ({ value: String(c.id), label: c.name })),
    { value: '__create_new__', label: '+ Create new category' },
  ];
  const locationOptions = locations.map((l) => ({ value: String(l.id), label: l.name }));
  const inventoryItemOptions = inventoryItems.map((item) => ({
    value: String(item.id),
    label: item.name,
  }));
  const sigOptions = sigs.map((sig) => ({ value: String(sig.id), label: sig.name }));

  return (
    <WorkspacePage
      testId="asset-form-page"
      hero={{
        eyebrow: 'Assets',
        title: isEditMode ? 'Edit asset' : 'New asset',
        description: isEditMode
          ? 'Update lifecycle, ownership, supplies, and PM schedule.'
          : 'Register a piece of equipment. Add supplies + a maintenance schedule inline.',
        action: (
          <Button variant="default" onClick={() => navigate(-1)}>
            Cancel
          </Button>
        ),
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
                title: 'Basics',
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
                      label="Serial number"
                      placeholder="Serial number or unique identifier"
                    />
                    {isEditMode && (
                      <FormInput
                        name="asset_tag"
                        control={control}
                        label="Asset tag"
                        placeholder="Auto-generated"
                        disabled
                      />
                    )}
                    <Controller
                      name="inventory_item"
                      control={control}
                      render={({ field, fieldState: { error: fieldError } }) => (
                        <FormSelect
                          name="inventory_item"
                          control={control}
                          label="Inventory item type"
                          data={[{ value: '', label: 'None' }, ...inventoryItemOptions]}
                          searchable
                          value={field.value ? String(field.value) : ''}
                          onChange={(value) => field.onChange(value ? Number(value) : null)}
                          error={fieldError?.message}
                        />
                      )}
                    />
                    <Controller
                      name="category"
                      control={control}
                      render={({ field, fieldState: { error: fieldError } }) => (
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
                          error={fieldError?.message}
                        />
                      )}
                    />
                    <Controller
                      name="location"
                      control={control}
                      render={({ field, fieldState: { error: fieldError } }) => (
                        <FormSelect
                          name="location"
                          control={control}
                          label="Location"
                          data={[{ value: '', label: 'None' }, ...locationOptions]}
                          searchable
                          value={field.value ? String(field.value) : ''}
                          onChange={(value) =>
                            field.onChange(
                              value ? (isNaN(Number(value)) ? value : Number(value)) : null,
                            )
                          }
                          error={fieldError?.message}
                        />
                      )}
                    />
                  </>
                ),
              },
              {
                title: 'Acquisition',
                children: (
                  <>
                    <Controller
                      name="date_received"
                      control={control}
                      render={({ field, fieldState: { error: fieldError } }) => (
                        <FormInput
                          name="date_received"
                          control={control}
                          label="Date received"
                          type="date"
                          value={field.value || ''}
                          onChange={(e) => field.onChange(e.target.value || null)}
                          error={fieldError?.message}
                        />
                      )}
                    />
                    <FormNumberInput
                      name="amount_paid"
                      control={control}
                      label="Amount paid"
                      min={0}
                      step={0.01}
                    />
                    <Switch
                      label="Donation"
                      checked={isDonation}
                      onChange={(e) => setValue('is_donation', e.currentTarget.checked)}
                    />
                    {isDonation && (
                      <FormInput
                        name="donor_name"
                        control={control}
                        label="Donor name"
                        placeholder="Name of donor"
                      />
                    )}
                  </>
                ),
              },
              {
                title: 'Status & ownership',
                children: (
                  <>
                    <FormSelect
                      name="status"
                      control={control}
                      label="Status"
                      data={STATUS_OPTIONS}
                      required
                    />
                    <Controller
                      name="ownership_type"
                      control={control}
                      render={({ field, fieldState: { error: fieldError } }) => (
                        <FormSelect
                          name="ownership_type"
                          control={control}
                          label="Ownership"
                          data={OWNERSHIP_OPTIONS}
                          value={field.value}
                          onChange={(value) => {
                            field.onChange(value as AssetFormData['ownership_type']);
                            if (value !== 'user') setValue('owning_user', null);
                            if (value !== 'group') setValue('owning_group', null);
                          }}
                          error={fieldError?.message}
                        />
                      )}
                    />
                    {ownershipType === 'group' && (
                      <Controller
                        name="owning_group"
                        control={control}
                        render={({ field, fieldState: { error: fieldError } }) => (
                          <FormSelect
                            name="owning_group"
                            control={control}
                            label="Owning SIG"
                            data={sigOptions}
                            searchable
                            value={field.value ? String(field.value) : ''}
                            onChange={(value) => field.onChange(value ? Number(value) : null)}
                            error={fieldError?.message}
                          />
                        )}
                      />
                    )}
                    {isEditMode && (
                      <Switch
                        label="Active"
                        checked={watch('is_active')}
                        onChange={(e) => setValue('is_active', e.currentTarget.checked)}
                      />
                    )}
                  </>
                ),
              },
              {
                title: 'Supplies',
                children: (
                  <AssetSuppliesSection
                    enabled={usesSupplies}
                    onEnabledChange={setUsesSupplies}
                    supplies={supplies}
                    onChange={setSupplies}
                    inventoryItems={inventoryItems}
                  />
                ),
              },
              {
                title: 'Maintenance',
                children: (
                  <AssetMaintenanceSection
                    enabled={pmEnabled}
                    onEnabledChange={setPmEnabled}
                    items={maintenanceItems}
                    onChange={setMaintenanceItems}
                  />
                ),
              },
              {
                title: 'Documentation',
                children: (
                  <>
                    <FormImageUpload
                      name="image"
                      control={control}
                      label="Image"
                      description="A photo of the equipment."
                    />
                    <FormFileUpload
                      name="manual_pdf"
                      control={control}
                      label="Manual (PDF)"
                      accept={['application/pdf']}
                    />
                    <FormInput
                      name="wiki_page_url"
                      control={control}
                      label="Wiki page"
                      placeholder="https://wiki.example.com/asset"
                    />
                    <FormInput
                      name="product_url"
                      control={control}
                      label="Product page"
                      placeholder="https://manufacturer.example.com/product"
                    />
                  </>
                ),
              },
            ]}
          />

          <Box mt="lg">
            <Button
              variant="subtle"
              onClick={() => setAdvancedOpen((v) => !v)}
              rightSection={
                advancedOpen ? <IconChevronUp size={16} /> : <IconChevronDown size={16} />
              }
            >
              {advancedOpen ? 'Hide advanced options' : 'Show advanced options'}
            </Button>
            <Collapse in={advancedOpen}>
              <Paper p="md" withBorder mt="sm" radius="md">
                <Stack gap="md">
                  <Text size="sm" c="dimmed">
                    Niche operating-requirement flags + free-text notes. Most assets don't need
                    these.
                  </Text>
                  <Switch
                    label="Needs compressed air"
                    checked={watch('needs_compressed_air')}
                    onChange={(e) => setValue('needs_compressed_air', e.currentTarget.checked)}
                  />
                  <Switch
                    label="Needs ventilation"
                    checked={watch('needs_ventilation')}
                    onChange={(e) => setValue('needs_ventilation', e.currentTarget.checked)}
                  />
                  <Switch
                    label="Chargeable use"
                    checked={watch('is_chargeable')}
                    onChange={(e) => setValue('is_chargeable', e.currentTarget.checked)}
                  />
                  <Switch
                    label="Training required"
                    description="Renders a TRAINING REQUIRED badge on the e-paper PM panel."
                    checked={watch('training_required')}
                    onChange={(e) => setValue('training_required', e.currentTarget.checked)}
                    data-testid="training-required-switch"
                  />
                  <MultiSelect
                    label="Required certifications"
                    description="When set, the panel shows the specific cert name(s) instead of the generic TRAINING REQUIRED text."
                    placeholder="Pick one or more certifications…"
                    data={certifications.map((c) => ({
                      value: String(c.id),
                      label: c.name,
                    }))}
                    value={(watch('required_certifications') || []).map((id) => String(id))}
                    onChange={(values) =>
                      setValue(
                        'required_certifications',
                        values.map((v) => Number(v)),
                      )
                    }
                    searchable
                    clearable
                    data-testid="required-certifications-multiselect"
                  />
                  <Switch
                    label="Report-only"
                    description="Problem reporting only — no enable/disable flows."
                    checked={watch('report_only')}
                    onChange={(e) => setValue('report_only', e.currentTarget.checked)}
                  />
                  <FormTextarea
                    name="condition_notes"
                    control={control}
                    label="Condition notes"
                    placeholder="Notes about the asset's condition, maintenance history, etc."
                  />
                  <FormTextarea
                    name="notes"
                    control={control}
                    label="Notes"
                    placeholder="General notes about the asset"
                  />
                </Stack>
              </Paper>
            </Collapse>
          </Box>

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
        title="Create new category"
      >
        <Stack gap="md">
          <TextInput
            label="Category name"
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
    </WorkspacePage>
  );
};

export default AssetFormPage;
