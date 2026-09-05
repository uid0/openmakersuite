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
import { labelIfWithheld, vendorDataWithheld } from '../utils/vendorVisibility';

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
  // FETCH DECISION ONLY. `inventory/suppliers/` is `IsAuthenticated`, and there
  // is no payload to read before the request is made, so this is the one thing
  // on the page auth state legitimately decides. Everything RENDERED asks
  // `vendorDataWithheld(kit)` instead — see `utils/vendorVisibility`, which
  // rejects a second client-side derivation of an answer the payload carries:
  // the response interceptor clears the token when a refresh fails on any
  // background call, so a later refetch returns a withheld payload while a
  // flag frozen at mount still says operator.
  const [signedInAtMount] = useState<boolean>(isAuthenticated);

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
  }, []);

  // NOT FETCHED WHEN LOGGED OUT (op-anonymous-read-posture). `/inventory/kits/
  // :kitId` is deliberately reachable without a session — `KitViewSet` is
  // `IsAuthenticatedOrReadOnly` — but `inventory/suppliers/` is
  // `IsAuthenticated`, so firing this unconditionally answered 401 and the
  // response interceptor, finding no refresh token, cleared storage and raised
  // the session-expired banner at a visitor who never signed in. The picker
  // this list feeds is behind the withheld check anyway.
  useEffect(() => {
    if (!signedInAtMount) return undefined;
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
  }, [signedInAtMount]);

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
  // These are the OPERATOR readings — they name vendors — and `vendorWithheld`
  // is what keeps them off an anonymous screen. Both values below are inside
  // that guard at every use: the attribution note, the Supplier input and the
  // Supplier SKU input. Rendering either one anywhere else in this card
  // publishes the vendor names to a logged-out visitor.
  //
  // The Unit cost beneath them is inside it too, which is a CHANGE: `unit_cost`
  // is in `InventoryItemSerializer.VENDOR_ONLY_FIELDS`, which `KitSerializer`
  // inherits, so the key is absent for this reader and the card said "No price
  // on file" — a claim about the KIT where the truth is about the READER.
  const vendorWithheld = vendorDataWithheld(kit);
  const kitSupplierName = chosenSupplierName(kit?.supplier_choice);
  const kitAlternativeText = alternativeSupplierNamesText(kit?.supplier_choice);

  // What the kit costs today, read-only. The Unit cost BOX no longer seeds from
  // this: it held the CHOSEN supplier's figure while the Supplier box named
  // whoever the operator typed, so a save wrote one vendor's price onto
  // another's link — filed in docs/oms-supplier-cost-write-path-record.md. A
  // blank box writes nothing, but the price still has to be READABLE, and to
  // everyone: it is a number that names no vendor, which is the same boundary
  // the card's other comment draws.
  const storedUnitCost =
    kit?.unit_cost === null || kit?.unit_cost === undefined ? null : Number(kit.unit_cost);
  const storedUnitCostText = labelIfWithheld(kit, () =>
    storedUnitCost === null || Number.isNaN(storedUnitCost)
      ? 'No price on file'
      : `$${storedUnitCost.toFixed(2)} per unit`
  );

  // Whether the Supplier box names someone other than the link these terms came
  // from. The SKU box still seeds from the chosen link, so retargeting silently
  // carries that vendor's part number across; this says so rather than moving it.
  const chosenLinkId = kit?.supplier_choice?.item_supplier_id ?? null;
  const chosenLinkSupplier =
    chosenLinkId === null
      ? null
      : ((kit?.suppliers ?? []).find((row) => row.id === chosenLinkId)?.supplier ?? null);
  const namingAnotherSupplier =
    supplierId !== '' && chosenLinkSupplier !== null && supplierId !== chosenLinkSupplier;

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
            <Text size="sm" data-testid="kit-unit-cost-current">
              {storedUnitCostText}
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
            {!vendorWithheld && kitSupplierName && (
              <Text size="sm" c="dimmed" data-testid="kit-supplier-attribution">
                Showing {kitSupplierName}&rsquo;s terms
                {kitAlternativeText !== null &&
                  ` — this kit is also stocked by ${kitAlternativeText}`}
                .
              </Text>
            )}
            <Grid>
              {/* WITHHELD-PAYLOAD ONLY, asked off the rows rather than off a
                  token. Neither kit route is behind RequireAuth and KitViewSet
                  serves reads publicly, so a logged-out visitor reaches this
                  card: the Supplier box names a vendor (its datalist lists
                  every one by name) and the SKU is that vendor's part number.
                  The Unit cost above is gated by the same answer — `unit_cost`
                  is withheld from this reader, and `KitListPage` DROPS its
                  column for the same payload, so labelling it here is what
                  keeps the two kit screens agreeing about the same kit. The
                  editable price box belongs with them: `handleSave` emits
                  `supplier_terms` only when both boxes above are filled, so
                  for this reader it is one whose contents are discarded. */}
              {!vendorWithheld && (
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
                  {namingAnotherSupplier && (
                    <Grid.Col span={12}>
                      <Text size="sm" c="dimmed" data-testid="kit-supplier-differs">
                        The price shown above is a different supplier&rsquo;s. Enter this
                        supplier&rsquo;s own SKU and price.
                      </Text>
                    </Grid.Col>
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
                </>
              )}
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
