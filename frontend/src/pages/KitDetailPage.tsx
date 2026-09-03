/**
 * KitDetailPage — create or edit one kit SKU (op-8n0).
 *
 * Serves both `/inventory/kits/new` and `/inventory/kits/:kitId`; there is no
 * separate form page, because a kit is small enough that "create" and "edit"
 * are the same screen with a different save verb.
 *
 * Mutations follow docs/REACTIVE_MUTATIONS.md: every save patches visible state
 * from the response (the kit API returns the FULL refreshed kit, not a 204),
 * pending/error UI is scoped to the save control rather than the page, a second
 * submit is blocked while one is in flight, and a save never drops the page
 * back to its initial loading placeholder.
 */
import { Alert, Button, Card, Grid, Group, Loader, NumberInput, Stack, Switch, Text, TextInput, Textarea, Title } from '@mantine/core';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import KitComponentEditor, { KitComponentDraft } from '../components/inventory/KitComponentEditor';
import WorkspacePage from '../components/landing/WorkspacePage';
import { kitAPI, inventoryAPI } from '../services/api';
import { Kit, Supplier } from '../types';

/** Pull a field-addressed message out of the API error envelope. */
const readError = (err: unknown, fallback: string): string => {
  const details = (err as { response?: { data?: { error?: { details?: Record<string, unknown> } } } })
    ?.response?.data?.error?.details;
  if (details) {
    const first = Object.values(details)[0];
    if (Array.isArray(first) && typeof first[0] === 'string') return first[0];
    if (typeof first === 'string') return first;
  }
  return fallback;
};

const KitDetailPage: React.FC = () => {
  const { kitId } = useParams<{ kitId: string }>();
  const navigate = useNavigate();
  const isNew = !kitId || kitId === 'new';

  const [kit, setKit] = useState<Kit | null>(null);
  const [loading, setLoading] = useState(!isNew);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [isActive, setIsActive] = useState(true);
  const [components, setComponents] = useState<KitComponentDraft[]>([]);

  const [supplierId, setSupplierId] = useState<number | ''>('');
  const [supplierSku, setSupplierSku] = useState('');
  const [unitCost, setUnitCost] = useState<number | string>('');
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);

  // Scoped mutation state — never a page-level spinner.
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);

  /** Fold a server kit into the form. Shared by load and every save. */
  const applyKit = useCallback((next: Kit) => {
    setKit(next);
    setName(next.name ?? '');
    setDescription(next.description ?? '');
    setIsActive(next.is_active ?? true);
    setComponents(
      (next.components ?? []).map((row) => ({
        id: row.id,
        component: row.component,
        component_name: row.component_name,
        component_sku: row.component_sku,
        quantity: row.quantity,
      })),
    );
    // The SKU and the cost below are ONE vendor's terms — the flat legacy
    // accessors for the link the API says to buy this kit through. Seeding them
    // while leaving Supplier blank meant the operator could type a DIFFERENT
    // vendor's id and save, writing vendor A's part number and price onto
    // vendor B's relationship: a SKU that gets pasted into an order form, now
    // attached to the wrong order form. Seed the vendor from the same answer,
    // so all three fields describe one supplier and changing it is a deliberate
    // act rather than an unnoticed one (op-3xsp).
    setSupplierId(next.supplier_choice?.supplier_id ?? '');
    setSupplierSku(next.supplier_sku ?? '');
    setUnitCost(next.unit_cost ?? '');
  }, []);

  useEffect(() => {
    let cancelled = false;
    inventoryAPI
      .listSuppliers()
      .then((res) => {
        if (cancelled) return;
        const data = res?.data;
        setSuppliers(Array.isArray(data) ? data : (data?.results ?? []));
      })
      .catch(() => {
        // Supplier terms are optional on save; the rest of the form still works.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (isNew) return undefined;
    let cancelled = false;
    kitAPI
      .getKit(kitId as string)
      .then((res) => {
        if (!cancelled) applyKit(res.data);
      })
      .catch(() => {
        if (!cancelled) setLoadError('Could not load this kit.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [kitId, isNew, applyKit]);

  const canSave = useMemo(
    () => name.trim().length > 0 && components.length > 0 && !saving,
    [name, components, saving],
  );

  const handleSave = async () => {
    // Duplicate submit is impossible while one is in flight.
    if (saving || !canSave) return;
    setSaving(true);
    setSaveError(null);

    const payload = {
      name: name.trim(),
      description,
      is_active: isActive,
      components: components.map((row) => ({
        component: row.component,
        quantity: row.quantity,
      })),
      ...(supplierId && supplierSku
        ? {
            supplier_terms: {
              supplier: Number(supplierId),
              supplier_sku: supplierSku,
              unit_cost: unitCost === '' ? '0' : String(unitCost),
            },
          }
        : {}),
    };

    try {
      const res = isNew
        ? await kitAPI.createKit(payload as never)
        : await kitAPI.updateKit(kitId as string, payload as never);
      // Patch straight from the response — no refetch, no loading placeholder.
      applyKit(res.data);
      setSavedAt(new Date().toISOString());
      if (isNew) navigate(`/inventory/kits/${res.data.id}`, { replace: true });
    } catch (err) {
      setSaveError(readError(err, 'Could not save this kit.'));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <WorkspacePage
        testId="kit-detail-page"
        hero={{ eyebrow: 'Inventory', title: 'Kit', description: 'Loading…' }}
      >
        <Group justify="center" py="xl">
          <Loader data-testid="kit-detail-loading" />
        </Group>
      </WorkspacePage>
    );
  }

  return (
    <WorkspacePage
      testId="kit-detail-page"
      hero={{
        eyebrow: 'Inventory',
        title: isNew ? 'New kit' : (kit?.name ?? 'Kit'),
        description:
          'One SKU that contains several stock items. Receiving it adds stock to the components listed below, not to the kit.',
        action: (
          <Button onClick={handleSave} loading={saving} disabled={!canSave} data-testid="kit-save">
            {isNew ? 'Create kit' : 'Save changes'}
          </Button>
        ),
      }}
    >
      <Stack gap="lg">
        {loadError && (
          <Alert color="red" data-testid="kit-detail-load-error">
            {loadError}
          </Alert>
        )}
        {saveError && (
          <Alert color="red" data-testid="kit-save-error">
            {saveError}
          </Alert>
        )}
        {savedAt && !saveError && (
          <Alert color="green" data-testid="kit-save-success">
            Saved.
          </Alert>
        )}

        <Card withBorder padding="lg">
          <Stack gap="md">
            <Title order={4}>Kit details</Title>
            <TextInput
              label="Name"
              required
              value={name}
              onChange={(event) => setName(event.currentTarget.value)}
              disabled={saving}
              data-testid="kit-name"
            />
            <Textarea
              label="Description"
              value={description}
              onChange={(event) => setDescription(event.currentTarget.value)}
              disabled={saving}
              data-testid="kit-description"
            />
            <Switch
              label="Active"
              checked={isActive}
              onChange={(event) => setIsActive(event.currentTarget.checked)}
              disabled={saving}
              data-testid="kit-active"
            />
          </Stack>
        </Card>

        <Card withBorder padding="lg">
          <Stack gap="md">
            <Title order={4}>Purchase terms</Title>
            <Text size="sm" c="dimmed">
              What the supplier charges for one kit. This is the price that lands on the
              purchase-order line.
            </Text>
            {/* Whose terms are on screen. Without it the three fields below are
                a part number and a price with no vendor attached to them. */}
            {kit?.supplier_choice?.supplier_name && (
              <Text size="sm" c="dimmed" data-testid="kit-supplier-attribution">
                Showing {kit.supplier_choice.supplier_name}&rsquo;s terms
                {kit.supplier_choice.alternatives.length > 0 &&
                  ` — this kit is also stocked by ${kit.supplier_choice.alternatives
                    .map((alternative) => alternative.supplier_name)
                    .join(', ')}`}
                . Changing Supplier below saves these terms against that vendor instead.
              </Text>
            )}
            <Grid>
              <Grid.Col span={{ base: 12, sm: 4 }}>
                <TextInput
                  label="Supplier"
                  placeholder="Supplier id"
                  value={supplierId === '' ? '' : String(supplierId)}
                  onChange={(event) => {
                    const next = event.currentTarget.value;
                    setSupplierId(next === '' ? '' : Number(next));
                  }}
                  disabled={saving}
                  list="kit-supplier-options"
                  data-testid="kit-supplier"
                />
                <datalist id="kit-supplier-options">
                  {suppliers.map((supplier) => (
                    <option key={supplier.id} value={supplier.id}>
                      {supplier.name}
                    </option>
                  ))}
                </datalist>
              </Grid.Col>
              <Grid.Col span={{ base: 12, sm: 4 }}>
                <TextInput
                  label="Supplier SKU"
                  value={supplierSku}
                  onChange={(event) => setSupplierSku(event.currentTarget.value)}
                  disabled={saving}
                  data-testid="kit-supplier-sku"
                />
              </Grid.Col>
              <Grid.Col span={{ base: 12, sm: 4 }}>
                <NumberInput
                  label="Unit cost"
                  prefix="$"
                  decimalScale={2}
                  value={unitCost}
                  onChange={setUnitCost}
                  disabled={saving}
                  data-testid="kit-unit-cost"
                />
              </Grid.Col>
            </Grid>
          </Stack>
        </Card>

        <Card withBorder padding="lg">
          <Stack gap="md">
            <Title order={4}>Contents</Title>
            <Text size="sm" c="dimmed">
              Receiving one kit adds these quantities to stock.
            </Text>
            <KitComponentEditor
              value={components}
              onChange={setComponents}
              disabled={saving}
              excludeItemId={kit?.id}
            />
          </Stack>
        </Card>
      </Stack>
    </WorkspacePage>
  );
};

export default KitDetailPage;
