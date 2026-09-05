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
import { isAuthenticated } from '../components/RequireAuth';
import { kitAPI, inventoryAPI } from '../services/api';
import { Kit, Supplier } from '../types';
import { alternativeSupplierNamesText, chosenSupplierName } from '../utils/supplierChoice';

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
  // This route carries no `RequireAuth` and `KitViewSet` serves reads to
  // anyone, so a logged-out visitor lands here. What they are not shown is the
  // VENDOR and that vendor's part number: a SKU shown without the vendor it
  // belongs to gets pasted into the wrong order form, and naming the vendor is
  // the disclosure this branch is not authorised to widen (op-3xsp). The
  // kit's own unit cost is not in that set — a price is a number that came
  // from a supplier, not the naming of one, and the kit list shows it to the
  // same visitor one click earlier.
  const [showSupplierAttribution] = useState<boolean>(isAuthenticated);

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
              unit_cost: unitCost === '' ? null : String(unitCost),
            },
          }
        : {}),
    };

    try {
      const res = isNew
        ? await kitAPI.createKit(payload)
        : await kitAPI.updateKit(kitId as string, payload);
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

  // Whose terms the card below is showing, and who else stocks the kit. Read
  // through `utils/supplierChoice` so this page words it the way every other
  // supplier surface does, and so it re-derives nothing: the names, their
  // order and the emptiness test are the server's answer, not this page's
  // (op-3xsp).
  //
  // These are the OPERATOR readings — they name vendors — and `showSupplierAttribution`
  // is the only thing keeping them off an anonymous screen. That gate does NOT
  // cover the card: the Purchase terms heading and the Unit cost render for
  // everyone. It covers exactly three things, and both values below are inside
  // it at every use — the attribution note, the Supplier input and the Supplier
  // SKU input. Rendering either one anywhere else in this card publishes the
  // vendor names to a logged-out visitor.
  const kitSupplierName = chosenSupplierName(kit?.supplier_choice);
  const kitAlternativeText = alternativeSupplierNamesText(kit?.supplier_choice);

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
            {/* Whose terms are on screen. Without it the SKU below is one
                vendor's part number with no vendor attached — and a SKU gets
                pasted into an order form, so an unattributed one is
                actionable-wrong in a way an unattributed price is not
                (op-3xsp). It ATTRIBUTES and nothing more: it does not fill
                Supplier in, and it does not describe how to write. Changing
                Supplier here is not a supported way to retarget these terms —
                the save writes this vendor's SKU and price onto whichever
                vendor the field names. */}
            {showSupplierAttribution && kitSupplierName && (
              <Text size="sm" c="dimmed" data-testid="kit-supplier-attribution">
                Showing {kitSupplierName}&rsquo;s terms
                {kitAlternativeText !== null &&
                  ` — this kit is also stocked by ${kitAlternativeText}`}
                .
              </Text>
            )}
            <Grid>
              {/* SIGNED-IN ONLY, and only these two. Neither route here is
                  behind RequireAuth and KitViewSet serves reads publicly, so a
                  logged-out visitor reaches this card — and the boundary this
                  change draws is NAMING a supplier, with the kit SKU as the one
                  explicit exception because it is traceable to a vendor. The
                  Supplier box names one (its datalist lists every vendor by
                  name); the SKU is that vendor's part number. Unit cost is
                  neither: it is a number that came from a supplier, which the
                  kit LIST has always shown anonymously, so withholding it here
                  would make the two kit screens disagree about the same kit. */}
              {showSupplierAttribution && (
                <>
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
                </>
              )}
              <Grid.Col span={{ base: 12, sm: 4 }}>
                <NumberInput
                  label="Unit cost"
                  description="Per unit. The case price is derived from it."
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
