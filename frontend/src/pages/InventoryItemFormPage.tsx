/**
 * Inventory Item Form Page
 * Create/Edit form for inventory items with all fields
 */
import { zodResolver } from '@hookform/resolvers/zod';
import { Alert, Button, Group, Modal, Paper, Stack, Switch, Text, TextInput, Title } from '@mantine/core';
import { IconAlertCircle } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { Controller, Resolver, useForm } from 'react-hook-form';
import { useNavigate, useParams } from 'react-router-dom';
import NFPADiamond from '../components/NFPADiamond';
import SupplierRelationshipForm, { SupplierRelationship } from '../components/SupplierRelationshipForm';
import { FormFileUpload } from '../components/forms/FormFileUpload';
import { FormImageUpload } from '../components/forms/FormImageUpload';
import { FormInput } from '../components/forms/FormInput';
import { FormLayout } from '../components/forms/FormLayout';
import { FormNumberInput } from '../components/forms/FormNumberInput';
import { FormSelect } from '../components/forms/FormSelect';
import { FormTextarea } from '../components/forms/FormTextarea';
import WorkspacePage from '../components/landing/WorkspacePage';
import { inventoryAPI } from '../services/api';
import { Category, InventoryItem, ItemSupplier, Location, Supplier } from '../types';
import { promptInput, showError } from '../utils/dialogs';
import { InventoryItemFormData, inventoryItemSchema } from '../utils/formSchemas';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const InventoryItemFormPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const isEditMode = !!id;

  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [_itemSuppliers, setItemSuppliers] = useState<ItemSupplier[]>([]);
  const [showCreateCategory, setShowCreateCategory] = useState(false);
  const [newCategoryName, setNewCategoryName] = useState('');
  const [_newLocationName, setNewLocationName] = useState('');
  const [supplierRelationships, setSupplierRelationships] = useState<SupplierRelationship[]>([]);

  const {
    control,
    handleSubmit,
    watch,
    setValue,
    reset,
  } = useForm<InventoryItemFormData>({
    // v5 resolvers infer the schema's Zod *input* type (fields with `.default()`
    // are optional pre-parse); this form seeds all fields via defaultValues, so
    // its field values are the *output* shape — assert against InventoryItemFormData.
    resolver: zodResolver(inventoryItemSchema) as Resolver<InventoryItemFormData>,
    defaultValues: {
      name: '',
      description: '',
      sku: '',
      current_stock: 0,
      minimum_stock: 0,
      reorder_quantity: 1,
      use_case_based_reorder: false,
      minimum_cases: null,
      reorder_cases: null,
      category: null,
      location: null,
      shelf_position: '',
      is_hazardous: false,
      msds_url: '',
      nfpa_health_hazard: null,
      nfpa_fire_hazard: null,
      nfpa_instability_hazard: null,
      nfpa_special_hazards: '',
      ownership_type: 'space',
      is_serialized: false,
      serial_tracking_mode: null,
      is_active: true,
      is_retired: false,
      notes: '',
    },
  });

  const isHazardous = watch('is_hazardous');
  const useCaseBasedReorder = watch('use_case_based_reorder');
  const isSerialized = watch('is_serialized');

  useEffect(() => {
    loadInitialData();
    if (isEditMode) {
      loadItem();
    }
  }, [id, isEditMode]);

  const loadInitialData = async () => {
    try {
      const [categoriesRes, locationsRes, suppliersRes] = await Promise.all([
        inventoryAPI.listCategories(),
        inventoryAPI.listLocations(),
        inventoryAPI.listSuppliers(),
      ]);
      setCategories(categoriesRes.data.results);
      setLocations(locationsRes.data.results);
      setSuppliers(suppliersRes.data.results);
    } catch (err) {
      console.error('Error loading initial data:', err);
      setError('Failed to load form data. Please refresh the page.');
    }
  };

  const loadItem = async () => {
    if (!id) return;

    try {
      setLoading(true);
      const [itemRes, suppliersRes] = await Promise.all([
        inventoryAPI.getItem(id),
        inventoryAPI.getItemSuppliers(id),
      ]);

      const item = itemRes.data;
      const suppliers = suppliersRes.data.results;

      // Map item data to form
      reset({
        name: item.name,
        description: item.description,
        sku: item.sku,
        current_stock: item.current_stock,
        minimum_stock: item.minimum_stock,
        reorder_quantity: item.reorder_quantity,
        use_case_based_reorder: item.use_case_based_reorder,
        minimum_cases: item.minimum_cases || null,
        reorder_cases: item.reorder_cases || null,
        category: item.category,
        location: item.location ? String(item.location) : null,
        shelf_position: (item as any).shelf_position || '',
        is_hazardous: item.is_hazardous,
        msds_url: item.msds_url || '',
        nfpa_health_hazard: item.nfpa_health_hazard,
        nfpa_fire_hazard: item.nfpa_fire_hazard,
        nfpa_instability_hazard: item.nfpa_instability_hazard,
        nfpa_special_hazards: item.nfpa_special_hazards || '',
        ownership_type: item.ownership_type,
        owning_user: item.owning_user,
        owning_group: item.owning_group,
        is_serialized: item.is_serialized ?? false,
        serial_tracking_mode: item.serial_tracking_mode ?? null,
        is_active: item.is_active,
        is_retired: item.is_retired ?? false,
        notes: item.notes || '',
        image_url: item.image ? (item.image.startsWith('http') ? item.image : '') : '',
      });

      // Map supplier relationships
      if (suppliers.length > 0) {
        setSupplierRelationships(
          suppliers.map((s) => ({
            id: s.id,
            supplier: s.supplier,
            supplier_sku: s.supplier_sku,
            supplier_url: s.supplier_url,
            unit_cost: s.unit_cost,
            package_cost: s.package_cost,
            quantity_per_package: s.quantity_per_package,
            average_lead_time: s.average_lead_time,
            is_primary: s.is_primary,
          }))
        );
      }

      setItemSuppliers(suppliers);
    } catch (err) {
      console.error('Error loading item:', err);
      setError('Failed to load item. Please try again.');
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
      showError('Failed to create category. Please try again.');
    }
  };

  const onSubmit = async (data: InventoryItemFormData) => {
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
        } else if (key === 'msds_file' && value instanceof File) {
          formData.append('msds_file', value);
        } else if (key === 'image_url' && typeof value === 'string') {
          formData.append('image_url', value);
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

      // Save item
      let savedItem: InventoryItem;
      if (isEditMode && id) {
        savedItem = (await inventoryAPI.updateItem(id, formData)).data;
      } else {
        savedItem = (await inventoryAPI.createItem(formData)).data;
      }

      // Save supplier relationships
      // TODO: Implement supplier relationship saving via ItemSupplier API

      navigate(`/inventory/items/${savedItem.id}`);
    } catch (err: any) {
      console.error('Error saving item:', err);
      setError(extractErrorMessage(err, 'Failed to save item. Please try again.'));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <WorkspacePage
        testId="inventory-item-form-page"
        hero={{ eyebrow: 'Inventory · Item', title: 'Item', description: 'Loading…' }}
      >
        <Paper withBorder p="md">
          <Text c="dimmed">Loading item…</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  const categoryOptions = [
    ...categories.map((c) => ({ value: String(c.id), label: c.name })),
    { value: '__create_new__', label: '+ Create New Category' },
  ];

  const locationOptions = [
    ...locations.map((l) => ({ value: String(l.id), label: l.name })),
    { value: '__create_new__', label: '+ Create New Location' },
  ];

  return (
    <WorkspacePage
      testId="inventory-item-form-page"
      hero={{
        eyebrow: 'Inventory · Item',
        title: isEditMode ? 'Edit item' : 'New item',
        description: isEditMode
          ? 'Update stock thresholds, suppliers, hazard data, and pricing.'
          : 'Register a new inventory SKU with reorder thresholds and a primary supplier.',
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
                title: 'Basic Information',
                children: (
                  <>
                    <FormInput
                      name="name"
                      control={control}
                      label="Name"
                      required
                      placeholder="Item name"
                    />
                    <FormTextarea
                      name="description"
                      control={control}
                      label="Description"
                      placeholder="Item description"
                    />
                    <FormInput
                      name="sku"
                      control={control}
                      label="SKU"
                      placeholder="Auto-generated if not provided"
                    />
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
                  </>
                ),
              },
              {
                title: 'Stock Settings',
                children: (
                  <>
                    <FormNumberInput
                      name="current_stock"
                      control={control}
                      label="Current Stock"
                      min={0}
                      required
                    />
                    <FormNumberInput
                      name="minimum_stock"
                      control={control}
                      label="Minimum Stock"
                      min={0}
                      required
                    />
                    <FormNumberInput
                      name="reorder_quantity"
                      control={control}
                      label="Reorder Quantity"
                      min={1}
                      required
                    />
                    <div>
                      <Switch
                        label="Use Case-Based Reordering"
                        checked={useCaseBasedReorder}
                        onChange={(e) => setValue('use_case_based_reorder', e.currentTarget.checked)}
                      />
                      {useCaseBasedReorder && (
                        <>
                          <FormNumberInput
                            name="minimum_cases"
                            control={control}
                            label="Minimum Cases"
                            min={1}
                            required
                          />
                          <FormNumberInput
                            name="reorder_cases"
                            control={control}
                            label="Reorder Cases"
                            min={1}
                            required
                          />
                        </>
                      )}
                    </div>
                  </>
                ),
              },
              {
                title: 'Category & Location',
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
                          data={locationOptions}
                          searchable
                          value={field.value ? String(field.value) : ''}
                          onChange={(value) => {
                            if (value === '__create_new__') {
                              promptInput('New Location', 'Location name', (name) => {
                                const trimmed = name.trim();
                                if (trimmed) {
                                  setNewLocationName(trimmed);
                                  field.onChange(trimmed);
                                }
                              });
                            } else {
                              field.onChange(value ? (isNaN(Number(value)) ? value : Number(value)) : null);
                            }
                          }}
                          error={error?.message}
                        />
                      )}
                    />
                    <FormSelect
                      name="shelf_position"
                      control={control}
                      label="Shelf Position"
                      data={[
                        { value: '', label: 'Not specified' },
                        { value: 'top', label: 'Top Shelf' },
                        { value: 'bottom', label: 'Bottom Shelf' },
                      ]}
                    />
                  </>
                ),
              },
              {
                title: 'Hazardous Materials',
                children: (
                  <>
                    <div>
                      <Switch
                        label="Is Hazardous Material"
                        checked={isHazardous}
                        onChange={(e) => setValue('is_hazardous', e.currentTarget.checked)}
                      />
                    </div>
                    {isHazardous && (
                      <>
                        <FormInput
                          name="msds_url"
                          control={control}
                          label="MSDS/SDS URL"
                          placeholder="Link to Material Safety Data Sheet"
                        />
                        <FormFileUpload
                          name="msds_file"
                          control={control}
                          label="MSDS/SDS File Upload"
                          accept={['application/pdf', 'application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document']}
                        />
                        <FormNumberInput
                          name="nfpa_health_hazard"
                          control={control}
                          label="NFPA Health Hazard (0-4)"
                          min={0}
                          max={4}
                        />
                        <FormNumberInput
                          name="nfpa_fire_hazard"
                          control={control}
                          label="NFPA Fire Hazard (0-4)"
                          min={0}
                          max={4}
                        />
                        <FormNumberInput
                          name="nfpa_instability_hazard"
                          control={control}
                          label="NFPA Instability Hazard (0-4)"
                          min={0}
                          max={4}
                        />
                        <FormInput
                          name="nfpa_special_hazards"
                          control={control}
                          label="NFPA Special Hazards"
                          placeholder="W, OX, COR, ACID, BIO, POI, RAD, ALK"
                          description="Comma-separated special hazard symbols"
                        />
                        <div>
                          <Text size="sm" fw={500} mb="xs">
                            NFPA Fire Diamond
                          </Text>
                          <NFPADiamond
                            health={watch('nfpa_health_hazard')}
                            flammability={watch('nfpa_fire_hazard')}
                            instability={watch('nfpa_instability_hazard')}
                            special={watch('nfpa_special_hazards')}
                          />
                        </div>
                      </>
                    )}
                  </>
                ),
              },
              {
                title: 'Serial Tracking',
                children: (
                  <>
                    <div>
                      <Switch
                        label="Track individual serial numbers"
                        description="Track each physical unit of this item by serial number through a lifecycle."
                        checked={isSerialized}
                        onChange={(e) => {
                          const checked = e.currentTarget.checked;
                          setValue('is_serialized', checked);
                          if (!checked) {
                            setValue('serial_tracking_mode', null);
                          }
                        }}
                      />
                    </div>
                    {isSerialized && (
                      <FormSelect
                        name="serial_tracking_mode"
                        control={control}
                        label="Tracking mode"
                        required
                        placeholder="Select how these units are used"
                        data={[
                          { value: 'consumable', label: 'Consumable (used up)' },
                          { value: 'reusable', label: 'Reusable (installed / removed)' },
                        ]}
                      />
                    )}
                  </>
                ),
              },
            ]}
          />

          {/* Supplier Relationships */}
          <Paper p="md" withBorder mt="xl">
            <SupplierRelationshipForm
              suppliers={suppliers}
              relationships={supplierRelationships}
              onChange={setSupplierRelationships}
            />
          </Paper>

          {/* Other Fields */}
          <Paper p="md" withBorder mt="xl">
            <FormLayout
              sections={[
                {
                  title: 'Additional Information',
                  children: (
                    <>
                      <FormTextarea
                        name="notes"
                        control={control}
                        label="Notes"
                        placeholder="Additional notes about this item"
                      />
                      <div>
                        <Switch
                          label="Active"
                          checked={watch('is_active')}
                          onChange={(e) => setValue('is_active', e.currentTarget.checked)}
                        />
                      </div>
                      <div>
                        <Switch
                          label="Retired"
                          description="Phased out: never flagged for reorder; hidden from the list once stock hits 0."
                          checked={watch('is_retired')}
                          onChange={(e) => setValue('is_retired', e.currentTarget.checked)}
                        />
                      </div>
                    </>
                  ),
                },
              ]}
            />
          </Paper>

          <Group justify="flex-end" mt="xl">
            <Button variant="subtle" onClick={() => navigate(-1)}>
              Cancel
            </Button>
            <Button type="submit" loading={saving}>
              {isEditMode ? 'Save Changes' : 'Create Item'}
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
    </WorkspacePage>
  );
};

export default InventoryItemFormPage;
