/**
 * Inventory Item Detail Page
 * Comprehensive detail view with tabs for overview, stock history, reorder history, usage logs, and linked assets
 */
import {
    Alert,
    Anchor,
    Badge,
    Button,
    Card,
    Checkbox,
    Group,
    Image,
    Modal,
    NumberInput,
    Paper,
    Select,
    Stack,
    Table,
    Tabs,
    Text,
    Textarea,
    Title,
} from '@mantine/core';
import { IconArchive, IconArchiveOff, IconBoxOff, IconBoxSeam, IconClipboardCheck, IconEdit, IconPackageExport, IconQrcode } from '@tabler/icons-react';
import { QRCodeSVG } from 'qrcode.react';
import React, { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import CommittedBreakdown from '../components/inventory/CommittedBreakdown';
import InventoryMetricsRow from '../components/inventory/InventoryMetricsRow';
import PurchaseReceiptsPanel from '../components/inventory/PurchaseReceiptsPanel';
import SerializedComponentsPanel from '../components/inventory/SerializedComponentsPanel';
import WorkspacePage from '../components/landing/WorkspacePage';
import NFPADiamond from '../components/NFPADiamond';
import { isAuthenticated } from '../components/RequireAuth';
import StockHistoryChart from '../components/StockHistoryChart';
import { useNotifications } from '../hooks/useNotifications';
import { assetsAPI, CycleCountPayload, inventoryAPI, PackTransition, reorderAPI, sigAPI } from '../services/api';
import { Asset, InventoryItem, InventoryItemMetrics, ItemPurchaseHistory, ItemSupplier, KitSummary, ReorderRequest, SIG, StockHistory, UsageLog } from '../types';
import { showError } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';
import {
  baseUnitOf,
  countLevelOf,
  countUnitOf,
  countsInPacks,
  describePackChain,
  onHandLabel,
  pluralizeUnit,
  reorderQuantityLabel,
  reorderThresholdLabel,
} from '../utils/packaging';

/**
 * Supplier-section rendering helpers (op-item-suppliers).
 *
 * A value nobody recorded is a different fact from an empty string or a zero,
 * so both get said out loud rather than rendered as a blank cell. In
 * particular: `ItemSupplier.average_lead_time` is NOT NULL with a default of 7,
 * so `0` means "arrives same day" and must never read the same as a payload
 * that carried no lead time at all.
 */
const NotRecorded: React.FC = () => (
  <Text size="sm" c="dimmed" fs="italic">
    Not recorded
  </Text>
);

/**
 * A discontinued or inactive link is dimmed exactly where it carries an
 * ACTIONABLE FIGURE — the name you would contact and the lead time you would
 * plan around — and never where it carries an IDENTIFIER. A lead time is a
 * promise about a future delivery and a link you cannot buy from makes none,
 * so its "3 days" must not read as the better option beside an orderable
 * "14 days". A SKU or UPC stays true whether or not you can order today and is
 * exactly what someone needs to look up what was bought last year, so dimming
 * it would cost legibility for no safety gain. The treatment stops there.
 *
 * Every supplier cell derives BOTH its colour and its `data-emphasis`
 * attribute from this one function, so the rendered emphasis cannot drift from
 * the attribute a test reads: reclassifying a column moves both together.
 */
type SupplierCellKind = 'actionable-figure' | 'identifier';
type SupplierCellEmphasis = 'dimmed' | 'full';

const cellEmphasis = (kind: SupplierCellKind, unorderable: boolean): SupplierCellEmphasis =>
  kind === 'actionable-figure' && unorderable ? 'dimmed' : 'full';

const dimColor = (emphasis: SupplierCellEmphasis) => (emphasis === 'dimmed' ? 'dimmed' : undefined);

const recordedValue = (value: string | null | undefined, emphasis: SupplierCellEmphasis) =>
  value ? (
    <Text size="sm" c={dimColor(emphasis)}>
      {value}
    </Text>
  ) : (
    <NotRecorded />
  );

const leadTimeValue = (days: number | null | undefined, emphasis: SupplierCellEmphasis) =>
  typeof days === 'number' && Number.isFinite(days) ? (
    <Text size="sm" c={dimColor(emphasis)}>
      {days} day{days === 1 ? '' : 's'}
    </Text>
  ) : (
    <NotRecorded />
  );

// Cycle-count reason options (op-c7y4). Mirrors the reconciliation grid's
// user-facing set — the system-only `vision_supply_check` reason is omitted.
const CYCLE_COUNT_REASONS: { value: CycleCountPayload['reason']; label: string }[] = [
  { value: 'miscounted', label: 'Miscounted' },
  { value: 'lost', label: 'Lost' },
  { value: 'damaged', label: 'Damaged' },
  { value: 'used_without_scan', label: 'Used without scanning' },
  { value: 'found', label: 'Found (positive delta)' },
  { value: 'other', label: 'Other' },
];

interface CycleCountModalProps {
  itemId: string;
  itemName: string;
  currentStock: number;
  /**
   * Packaging context (op-lkxl). When `packCounted` is true the count is
   * entered — and posted — in whole `countUnit` packs (`at_level`); otherwise
   * the modal is exactly the base-unit form it has always been. `openCount` is
   * only used by `open_closed` items, whose count is "sealed packs + open ones".
   */
  packCounted: boolean;
  countUnit: string;
  countAtLevel: number;
  isOpenClosed: boolean;
  openCount: number;
  opened: boolean;
  onClose: () => void;
  onCounted: () => void;
}

/**
 * Modal for recording a physical cycle count. Mirrors the reconciliation form
 * controls: counted quantity, reason, notes, and a skip-reorder toggle. On
 * success it reloads the parent item so days-since-last-count refreshes.
 *
 * For a pack-counting item the quantity is entered in the item's own unit
 * ("4 cases") and posted with `at_level: true`; the flag is deliberately NOT
 * sent for anything else, because the backend contract is that a quantity means
 * base units unless a caller opts in.
 */
const CycleCountModal: React.FC<CycleCountModalProps> = ({
  itemId,
  itemName,
  currentStock,
  packCounted,
  countUnit,
  countAtLevel,
  isOpenClosed,
  openCount,
  opened,
  onClose,
  onCounted,
}) => {
  const notifications = useNotifications();
  const seededQty = packCounted ? countAtLevel : currentStock;
  const [countedQty, setCountedQty] = useState<number | ''>(seededQty);
  const [openContainers, setOpenContainers] = useState<number | ''>(openCount);
  const [reason, setReason] = useState<CycleCountPayload['reason']>('miscounted');
  const [notes, setNotes] = useState('');
  const [skipReorder, setSkipReorder] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // Re-seed the form from the current system on-hand each time the modal opens
  // so the operator starts from the number they're reconciling against.
  useEffect(() => {
    if (opened) {
      setCountedQty(seededQty);
      setOpenContainers(openCount);
      setReason('miscounted');
      setNotes('');
      setSkipReorder(false);
    }
  }, [opened, seededQty, openCount]);

  const handleSubmit = async () => {
    if (countedQty === '' || countedQty < 0) {
      notifications.showWarning('Counted quantity required', 'Enter a whole number of 0 or more.');
      return;
    }
    setSubmitting(true);
    try {
      const { data } = await inventoryAPI.cycleCount(itemId, {
        counted_qty: countedQty,
        reason,
        notes: notes || undefined,
        skip_reorder: skipReorder,
        // Opt-in only: an each-mode item sends neither key and is read in base
        // units, exactly as before the packaging matrix existed.
        ...(packCounted ? { at_level: true } : {}),
        ...(packCounted && isOpenClosed && openContainers !== ''
          ? { open_count: openContainers }
          : {}),
      });
      const delta = data.reconciliation.delta;
      const onHand = data.on_hand_display?.text ?? `${data.current_stock}`;
      notifications.showSuccess(
        'Cycle count recorded',
        `On-hand set to ${onHand} (Δ ${delta >= 0 ? '+' : ''}${delta}).`
      );
      onCounted();
      onClose();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      notifications.showError('Cycle count failed', detail || 'Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal opened={opened} onClose={onClose} title={`Cycle Count — ${itemName}`} centered>
      <Stack gap="md">
        <NumberInput
          label={
            packCounted
              ? `Counted quantity (${pluralizeUnit(countUnit, 2)})`
              : 'Counted quantity'
          }
          description={
            packCounted
              ? `Whole ${pluralizeUnit(countUnit, 2)} on the shelf${
                  isOpenClosed ? ' — sealed only' : ''
                }`
              : 'Physical count of units on the shelf'
          }
          value={countedQty}
          onChange={(v) => setCountedQty(v === '' ? '' : Number(v))}
          min={0}
          allowDecimal={false}
          allowNegative={false}
          required
          data-testid="cycle-count-qty"
        />
        {packCounted && isOpenClosed && (
          <NumberInput
            label="Open containers"
            description={`Opened ${pluralizeUnit(countUnit, 2)} in use — not counted as stock`}
            value={openContainers}
            onChange={(v) => setOpenContainers(v === '' ? '' : Number(v))}
            min={0}
            allowDecimal={false}
            allowNegative={false}
            data-testid="cycle-count-open-containers"
          />
        )}
        <Select
          label="Reason"
          data={CYCLE_COUNT_REASONS}
          value={reason}
          onChange={(v) => v && setReason(v as CycleCountPayload['reason'])}
          allowDeselect={false}
          required
          data-testid="cycle-count-reason"
        />
        <Textarea
          label="Notes"
          placeholder="Optional context for this count"
          value={notes}
          onChange={(e) => setNotes(e.currentTarget.value)}
          data-testid="cycle-count-notes"
        />
        <Checkbox
          label="Skip auto-reorder if at or below minimum"
          checked={skipReorder}
          onChange={(e) => setSkipReorder(e.currentTarget.checked)}
          data-testid="cycle-count-skip-reorder"
        />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} loading={submitting} data-testid="cycle-count-submit">
            Record Count
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
};

interface LogUsageModalProps {
  itemId: string;
  itemName: string;
  unitCost: string | null;
  /** op-lkxl: enter + post usage in whole packs when the item is counted that way. */
  packCounted: boolean;
  countUnit: string;
  /** Base units in one counting pack — 1 for an each-mode item. Prices the charge. */
  packBaseUnits: number;
  opened: boolean;
  onClose: () => void;
  onLogged: () => void;
}

/**
 * Modal for recording stock consumption ("Use / Log Usage", op-27wa) with an
 * optional committee (SIG) charge. When a committee is selected and the item
 * has a unit cost the backend posts a charge to the ledger (Bead 1, #920);
 * with no unit cost the committee is recorded but nothing is charged. On
 * success it reloads the parent item so stock + the usage-logs tab refresh.
 */
const LogUsageModal: React.FC<LogUsageModalProps> = ({
  itemId,
  itemName,
  unitCost,
  packCounted,
  countUnit,
  packBaseUnits,
  opened,
  onClose,
  onLogged,
}) => {
  const notifications = useNotifications();
  const [quantity, setQuantity] = useState<number | ''>(1);
  const [chargedGroup, setChargedGroup] = useState<number | null>(null);
  const [notes, setNotes] = useState('');
  const [sigs, setSigs] = useState<SIG[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [permissionError, setPermissionError] = useState<string | null>(null);

  // Re-seed the form and (re)load the committees the user can charge each time
  // the modal opens. A failed SIG fetch degrades to "no committees" rather than
  // blocking a plain (uncharged) usage log.
  useEffect(() => {
    if (!opened) return;
    setQuantity(1);
    setChargedGroup(null);
    setNotes('');
    setPermissionError(null);
    sigAPI
      .listMySIGs()
      .then((res) => setSigs(res.data.results || []))
      .catch(() => setSigs([]));
  }, [opened]);

  const sigOptions = sigs.map((sig) => ({ value: String(sig.id), label: sig.name }));
  const qty = quantity === '' ? 0 : quantity;
  // Only meaningful when a committee is selected; mirrors the backend's
  // snapshot of unit_cost × quantity at consume time. Keyed off unitCost (not
  // qty) so clearing the quantity field shows $0.00 rather than the wrong
  // "no unit cost" hint. unit_cost is per BASE unit, so a pack entry is priced
  // through the pack's size — the same conversion the server does.
  const projectedCharge =
    unitCost != null ? (parseFloat(unitCost) * qty * packBaseUnits).toFixed(2) : null;

  const handleSubmit = async () => {
    if (quantity === '' || quantity < 1) {
      notifications.showWarning('Quantity required', 'Enter a whole number of 1 or more.');
      return;
    }
    setSubmitting(true);
    setPermissionError(null);
    try {
      const { data } = await inventoryAPI.logUsage(itemId, {
        quantity,
        notes: notes || undefined,
        charged_group: chargedGroup ?? undefined,
        // Opt-in only (op-ev14): without this the quantity is base units, which
        // is what every each-mode item must keep meaning.
        ...(packCounted ? { at_level: true } : {}),
      });
      if (data.warning) {
        // Committee recorded but no unit cost → nothing posted. Non-error tone.
        notifications.showWarning('Committee recorded', data.warning);
      } else if (data.ledger_transaction && data.total_cost) {
        const committee = sigs.find((s) => s.id === data.charged_group)?.name ?? 'the committee';
        notifications.showSuccess(
          'Usage logged',
          `Charged $${parseFloat(data.total_cost).toFixed(2)} to ${committee}.`
        );
      } else {
        notifications.showSuccess('Usage logged', `Recorded ${data.quantity_used} used.`);
      }
      onLogged();
      onClose();
    } catch (err) {
      const response = (err as { response?: { status?: number; data?: { detail?: string } } })?.response;
      if (response?.status === 403) {
        setPermissionError("You don't have permission to charge this committee for this item.");
      } else {
        const detail = response?.data?.detail;
        notifications.showError('Log usage failed', detail || 'Please try again.');
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal opened={opened} onClose={onClose} title={`Use / Log Usage — ${itemName}`} centered>
      <Stack gap="md">
        <NumberInput
          label={packCounted ? `Quantity used (${pluralizeUnit(countUnit, 2)})` : 'Quantity used'}
          description={
            packCounted
              ? `Whole ${pluralizeUnit(countUnit, 2)} consumed from stock`
              : 'Units consumed from stock'
          }
          value={quantity}
          onChange={(v) => setQuantity(v === '' ? '' : Number(v))}
          min={1}
          allowDecimal={false}
          allowNegative={false}
          required
          data-testid="log-usage-qty"
        />
        <Select
          label="Charge committee"
          description="Optional — post this consumption to a committee (SIG)"
          placeholder="Select a committee (optional)"
          data={sigOptions}
          value={chargedGroup !== null ? String(chargedGroup) : null}
          onChange={(v) => setChargedGroup(v ? Number(v) : null)}
          searchable
          clearable
          data-testid="log-usage-committee"
        />
        {chargedGroup !== null &&
          (unitCost != null ? (
            <Text size="sm" data-testid="log-usage-projected">
              Projected charge: <strong>${projectedCharge}</strong>
            </Text>
          ) : (
            <Text size="sm" c="dimmed" data-testid="log-usage-no-cost">
              No unit cost on file — the committee will be recorded but nothing is charged.
            </Text>
          ))}
        <Textarea
          label="Notes"
          placeholder="Optional context for this usage"
          value={notes}
          onChange={(e) => setNotes(e.currentTarget.value)}
          data-testid="log-usage-notes"
        />
        {permissionError && (
          <Alert color="red" data-testid="log-usage-error">
            {permissionError}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} loading={submitting} data-testid="log-usage-submit">
            Log Usage
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
};

const InventoryItemDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  // `/inventory/items/:id` is NOT behind RequireAuth (App.tsx), so a logged-out
  // visitor reaches this page. Read the app's auth signal once, as ScanPage
  // does, so it cannot change midway through a render.
  const [isLoggedIn] = useState<boolean>(isAuthenticated);

  const [item, setItem] = useState<InventoryItem | null>(null);
  const [metrics, setMetrics] = useState<InventoryItemMetrics | null>(null);
  // Kits that contain this item (op-8n0). Empty means the card is not rendered
  // at all — most items belong to no kit, and an empty card is noise.
  const [suppliedByKits, setSuppliedByKits] = useState<KitSummary[]>([]);
  const [usageLogs, setUsageLogs] = useState<UsageLog[]>([]);
  const [stockHistory, setStockHistory] = useState<StockHistory | null>(null);
  const [purchaseHistory, setPurchaseHistory] = useState<ItemPurchaseHistory | null>(null);
  const [reorderHistory, setReorderHistory] = useState<ReorderRequest[]>([]);
  // Two *different* asset relationships, both surfaced on the Linked Assets
  // tab (op-qdfr). `linkedAssets` = Asset.inventory_item, i.e. the asset IS an
  // instance of this item type. `usedByAssets` = the AssetPart through-model,
  // i.e. assets that consume this item as a part. Conflating them was the bug.
  const [linkedAssets, setLinkedAssets] = useState<Asset[]>([]);
  const [usedByAssets, setUsedByAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<string | null>('overview');
  const [cycleCountOpen, setCycleCountOpen] = useState(false);
  const [logUsageOpen, setLogUsageOpen] = useState(false);
  const [retiring, setRetiring] = useState(false);
  // Which pack transition is in flight, so both buttons disable together.
  const [packBusy, setPackBusy] = useState<PackTransition | null>(null);
  const notifications = useNotifications();

  useEffect(() => {
    if (id) {
      loadData();
    }
  }, [id]);

  const loadData = async () => {
    if (!id) return;

    setLoading(true);
    // Use allSettled so the page still renders the item when a sibling
    // call (usage logs, reorder history, linked assets) fails. Previously
    // any one of these rejecting would short-circuit the whole try-block
    // and show "Item not found" even though the item exists — uid0 hit
    // this on items with no linked-assets filter match where assetsAPI
    // returned a 400.
    const [
      itemRes,
      metricsRes,
      usageLogsRes,
      stockHistoryRes,
      purchaseHistoryRes,
      reorderRes,
      assetsRes,
      usedByRes,
      suppliedByKitsRes,
    ] = await Promise.allSettled([
      inventoryAPI.getItem(id),
      inventoryAPI.getItemMetrics(id),
      inventoryAPI.getUsageLogs(id),
      inventoryAPI.getStockHistory(id),
      inventoryAPI.getPurchaseHistory(id),
      reorderAPI.listRequests({ status: undefined }),
      assetsAPI.listAssets({ inventory_item: id }),
      assetsAPI.listAssets({ consumable_for_item: id }),
      // "Which kits would restock this?" (op-8n0). Joined into the existing
      // allSettled so a rejection just omits the card instead of blocking the
      // page — the same reasoning as every sibling call here.
      inventoryAPI.getItemKits(id),
    ]);

    if (suppliedByKitsRes.status === 'fulfilled') {
      // Optional read: AC-45 requires a failed (or absent) supplied-by lookup to
      // omit the card WITHOUT blocking the rest of the page, and allSettled
      // reports a non-promise value as fulfilled-with-undefined.
      const payload = suppliedByKitsRes.value?.data;
      setSuppliedByKits(Array.isArray(payload) ? payload : []);
    }

    if (itemRes.status === 'fulfilled') {
      setItem(itemRes.value.data);
    } else {
      console.error('Error loading item:', itemRes.reason);
    }

    if (metricsRes.status === 'fulfilled') {
      setMetrics(metricsRes.value.data);
    } else {
      console.error('Error loading item metrics:', metricsRes.reason);
    }

    if (usageLogsRes.status === 'fulfilled') {
      setUsageLogs(usageLogsRes.value.data.results || []);
    } else {
      console.error('Error loading usage logs:', usageLogsRes.reason);
    }

    if (stockHistoryRes.status === 'fulfilled' && stockHistoryRes.value) {
      setStockHistory(stockHistoryRes.value.data);
    } else if (stockHistoryRes.status === 'rejected') {
      console.error('Error loading stock history:', stockHistoryRes.reason);
    }

    // Purchase/receipt provenance is auth-required (unlike the item read), so a
    // rejection here is expected for an anonymous viewer — the tab degrades to
    // its empty state rather than taking the page down.
    if (purchaseHistoryRes.status === 'fulfilled' && purchaseHistoryRes.value) {
      setPurchaseHistory(purchaseHistoryRes.value.data);
    } else if (purchaseHistoryRes.status === 'rejected') {
      console.error('Error loading purchase history:', purchaseHistoryRes.reason);
    }

    if (reorderRes.status === 'fulfilled') {
      const allRequests = reorderRes.value.data.results || [];
      setReorderHistory(allRequests.filter((req) => req.item === id));
    } else {
      console.error('Error loading reorder requests:', reorderRes.reason);
    }

    if (assetsRes.status === 'fulfilled') {
      setLinkedAssets(assetsRes.value.data.results || []);
    } else {
      console.error('Error loading linked assets:', assetsRes.reason);
    }

    if (usedByRes.status === 'fulfilled') {
      setUsedByAssets(usedByRes.value.data.results || []);
    } else {
      console.error('Error loading assets that use this item:', usedByRes.reason);
    }

    setLoading(false);
  };

  const handleGenerateQR = async () => {
    if (!id) return;

    try {
      await inventoryAPI.generateQR(id);
      await loadData(); // Reload to get updated QR code
    } catch (err) {
      console.error('Error generating QR code:', err);
      showError('Failed to generate QR code. Please try again.');
    }
  };

  /**
   * Open a sealed pack, or finish the open one (op-ev14 `pack-container`).
   *
   * Only reachable for an `open_closed` item. Opening IS consumption under that
   * mode — the backend drops stock by the pack's base units, bumps the open
   * tally and writes a usage log — so the page reloads afterwards to pick up
   * the new sealed + open split.
   */
  const handlePackTransition = async (transition: PackTransition) => {
    if (!id) return;

    setPackBusy(transition);
    try {
      const response = await inventoryAPI.packContainer(id, transition);
      const text = response?.data?.on_hand_display?.text;
      notifications.showSuccess(
        transition === 'open' ? 'Pack opened' : 'Open pack finished',
        text ? `On hand: ${text}.` : undefined
      );
      await loadData();
    } catch (err) {
      notifications.showError(
        transition === 'open' ? 'Could not open a pack' : 'Could not finish the open pack',
        extractErrorMessage(err, 'Please try again.')
      );
    } finally {
      setPackBusy(null);
    }
  };

  // Retire / un-retire (op-jv7r). Mirrors handleGenerateQR: call the action
  // then reload so the badge + button label reflect the new state. Toggles by
  // the item's current is_retired.
  const handleToggleRetire = async () => {
    if (!id || !item) return;

    try {
      setRetiring(true);
      if (item.is_retired) {
        await inventoryAPI.unretireItem(id);
      } else {
        await inventoryAPI.retireItem(id);
      }
      await loadData();
    } catch (err) {
      console.error('Error updating retirement status:', err);
      showError('Failed to update retirement status. Please try again.');
    } finally {
      setRetiring(false);
    }
  };

  if (loading) {
    return (
      <WorkspacePage
        testId="inventory-item-detail-page"
        hero={{ eyebrow: 'Inventory · Item', title: 'Item', description: 'Loading…' }}
      >
        <Paper withBorder p="md">
          <Text c="dimmed">Loading item…</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  if (!item) {
    return (
      <WorkspacePage
        testId="inventory-item-detail-page"
        hero={{ eyebrow: 'Inventory · Item', title: 'Item', description: 'Not found.' }}
      >
        <Paper withBorder p="md">
          <Text>Item not found.</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  // Item suppliers (op-item-suppliers). `undefined` and `[]` are DIFFERENT
  // facts and the card renders them differently: a payload that carried no
  // `suppliers` key never told us anything, while an empty array is a positive
  // "this item has no supplier linked". The detail endpoint always sends the
  // key, so the undefined branch only fires for a narrowed/partial payload —
  // but it must not read as "none".
  const supplierLinks: ItemSupplier[] | undefined = item.suppliers;

  // Packaging matrix (op-lkxl). `packCounted` is the one predicate the new
  // surfaces branch on: false for an each-mode item AND for a half-configured
  // one, which keeps everything below on today's base-unit behaviour.
  const packCounted = countsInPacks(item);
  const baseUnit = baseUnitOf(item);
  const countUnit = countUnitOf(item);
  const chainLines = describePackChain(item.packaging_levels);
  const sealedCount = item.on_hand_display?.sealed ?? 0;
  const openCount = item.on_hand_display?.open ?? item.open_container_count ?? 0;

  return (
    <WorkspacePage
      testId="inventory-item-detail-page"
      hero={{
        eyebrow: `Inventory · SKU ${item.sku}`,
        title: item.name,
        description: item.description ? item.description.split('\n')[0] : undefined,
        action: (
          <Group gap="sm">
            <Button
              variant="default"
              leftSection={<IconClipboardCheck size={16} />}
              onClick={() => setCycleCountOpen(true)}
              data-testid="cycle-count-button"
            >
              Cycle Count
            </Button>
            <Button
              variant="default"
              leftSection={<IconPackageExport size={16} />}
              onClick={() => setLogUsageOpen(true)}
              data-testid="log-usage-button"
            >
              Use / Log Usage
            </Button>
            <Button
              variant="default"
              color={item.is_retired ? undefined : 'orange'}
              leftSection={
                item.is_retired ? <IconArchiveOff size={16} /> : <IconArchive size={16} />
              }
              onClick={handleToggleRetire}
              loading={retiring}
              data-testid="retire-button"
            >
              {item.is_retired ? 'Unretire' : 'Retire'}
            </Button>
            <Button
              variant="default"
              leftSection={<IconQrcode size={16} />}
              onClick={handleGenerateQR}
            >
              Generate QR
            </Button>
            <Button
              leftSection={<IconEdit size={16} />}
              onClick={() => navigate(`/inventory/items/${id}/edit`)}
            >
              Edit
            </Button>
          </Group>
        ),
      }}
    >
      {/* Status Badges */}
      <Group>
        {item.needs_reorder && <Badge color="red">Low Stock</Badge>}
        {item.has_pending_reorder && <Badge color="blue">Reorder Pending</Badge>}
        {!item.is_active && <Badge color="gray">Inactive</Badge>}
        {item.is_retired && <Badge color="orange">Retired</Badge>}
        {item.is_hazardous && <Badge color="orange">Hazardous Material</Badge>}
        {item.is_serialized && <Badge color="grape">Serialized</Badge>}
      </Group>

      {/* Prominent metrics strip — hard to get from a single screen otherwise
          (issue-5): SKU · QOH · QOO · QA · QC · QIT · RP · Lead · Cost. The
          "Committed to" strip underneath attributes QC to the work orders (and
          so the assets) holding it (op-l4i0). */}
      {metrics && (
        <>
          <InventoryMetricsRow sku={item.sku} metrics={metrics} />
          <CommittedBreakdown
            entries={metrics.committed_breakdown || []}
            totalCommitted={metrics.quantity_committed}
          />
        </>
      )}

      {/* Tabs */}
      <Tabs value={activeTab} onChange={setActiveTab}>
        <Tabs.List>
          <Tabs.Tab value="overview">Overview</Tabs.Tab>
          <Tabs.Tab value="stock-history">Stock History</Tabs.Tab>
          <Tabs.Tab value="reorder-history">Reorder History</Tabs.Tab>
          <Tabs.Tab value="purchase-receipts">Purchase / Receipts</Tabs.Tab>
          <Tabs.Tab value="usage-logs">Usage Logs</Tabs.Tab>
          <Tabs.Tab value="linked-assets">Linked Assets</Tabs.Tab>
          {/* Always present so a user looking to add a serial number can find
              it — even when the item isn't flagged serialized yet, the panel
              explains the state and links to enabling it (op-qff). */}
          <Tabs.Tab value="serialized-units">Serialized Units</Tabs.Tab>
        </Tabs.List>

        {/* Overview Tab */}
        <Tabs.Panel value="overview" pt="md">
          <Stack gap="md">
            <Group align="flex-start" grow>
              {/* Image and Basic Info */}
              <Card withBorder p="md">
                <Stack gap="md">
                  {item.thumbnail && (
                    <Image src={item.thumbnail} alt={item.name} height={200} fit="contain" />
                  )}
                  <div>
                    <Text size="sm" fw={500} mb="xs">
                      Description
                    </Text>
                    <Text size="sm">{item.description || 'No description provided'}</Text>
                  </div>
                </Stack>
              </Card>

              {/* Stock Information */}
              <Card withBorder p="md">
                <Stack gap="md">
                  <Title order={4}>Stock Information</Title>
                  <Group justify="space-between">
                    <Text size="sm">Current Stock:</Text>
                    <Text
                      size="sm"
                      fw={600}
                      c={item.needs_reorder ? 'red' : undefined}
                      data-testid="item-on-hand"
                    >
                      {onHandLabel(item)}
                    </Text>
                  </Group>
                  {/* Pack-counting items also show the canonical base-unit
                      number, because that is what every PO, usage log and
                      reorder quantity is stored in (op-lkxl). */}
                  {packCounted && (
                    <Group justify="space-between">
                      <Text size="sm">Base units:</Text>
                      <Text size="sm" c="dimmed" data-testid="item-base-units">
                        {item.current_stock} {pluralizeUnit(baseUnit, item.current_stock)}
                      </Text>
                    </Group>
                  )}
                  {chainLines.length > 0 && (
                    <div data-testid="item-pack-chain">
                      <Text size="sm" fw={500} mb="xs">
                        Packaging
                      </Text>
                      <Stack gap={2}>
                        {chainLines.map((line) => (
                          <Text key={line} size="sm" c="dimmed">
                            {line}
                          </Text>
                        ))}
                        <Text size="sm" c="dimmed">
                          Counted in {pluralizeUnit(countUnit, 2)}
                          {item.count_mode === 'open_closed' ? ' (sealed + open)' : ''}
                        </Text>
                      </Stack>
                    </div>
                  )}
                  {/* Open / finish a pack — the two container moves an
                      open_closed item makes, and the only stock path that
                      expresses them (op-ev14). Opening consumes the pack. */}
                  {item.count_mode === 'open_closed' && packCounted && (
                    <Group gap="sm" data-testid="pack-container-controls">
                      <Button
                        size="xs"
                        variant="light"
                        leftSection={<IconBoxOff size={14} />}
                        onClick={() => handlePackTransition('open')}
                        loading={packBusy === 'open'}
                        disabled={sealedCount === 0 || packBusy !== null}
                        data-testid="open-pack-button"
                      >
                        Open a {countUnit}
                      </Button>
                      <Button
                        size="xs"
                        variant="light"
                        leftSection={<IconBoxSeam size={14} />}
                        onClick={() => handlePackTransition('finish')}
                        loading={packBusy === 'finish'}
                        disabled={openCount === 0 || packBusy !== null}
                        data-testid="finish-pack-button"
                      >
                        Finish open {countUnit}
                      </Button>
                    </Group>
                  )}
                  {item.use_case_based_reorder && (
                    <Group justify="space-between">
                      <Text size="sm">Current Cases:</Text>
                      <Text size="sm" fw={600}>
                        {item.current_cases === null
                          ? '— (case size unknown)'
                          : `${item.current_cases.toFixed(1)} cases`}
                      </Text>
                    </Group>
                  )}
                  <Group justify="space-between">
                    <Text size="sm">Minimum Stock:</Text>
                    <Text size="sm" data-testid="item-minimum-stock">
                      {reorderThresholdLabel(item)}
                    </Text>
                  </Group>
                  <Group justify="space-between">
                    <Text size="sm">Reorder Quantity:</Text>
                    <Text size="sm" data-testid="item-reorder-quantity">
                      {reorderQuantityLabel(item)}
                    </Text>
                  </Group>
                  <Group justify="space-between">
                    <Text size="sm">Last Cycle Count:</Text>
                    {item.last_counted_at ? (
                      <Text size="sm" data-testid="days-since-count">
                        {item.days_since_last_count} day{item.days_since_last_count === 1 ? '' : 's'} ago (
                        {new Date(item.last_counted_at).toLocaleDateString()})
                      </Text>
                    ) : (
                      <Text size="sm" c="dimmed" data-testid="days-since-count">
                        Never counted
                      </Text>
                    )}
                  </Group>
                  {item.unit_cost && (
                    <Group justify="space-between">
                      <Text size="sm">Unit Cost:</Text>
                      <Text size="sm" fw={600}>
                        ${parseFloat(item.unit_cost).toFixed(2)}
                      </Text>
                    </Group>
                  )}
                </Stack>
              </Card>
            </Group>

            <Group align="flex-start" grow>
              {/* Category & Location */}
              <Card withBorder p="md">
                <Stack gap="md">
                  <Title order={4}>Organization</Title>
                  <div>
                    <Text size="sm" fw={500} mb="xs">
                      Category
                    </Text>
                    <Text size="sm">{item.category_name || 'Uncategorized'}</Text>
                  </div>
                  <div>
                    <Text size="sm" fw={500} mb="xs">
                      Location
                    </Text>
                    <Text size="sm">{item.location || 'No location specified'}</Text>
                  </div>
                  {/* A logged-out visitor keeps the single legacy name they saw
                      before the Suppliers card existed — no more and no less.
                      See the Suppliers card below for why the card is gated. */}
                  {!isLoggedIn && item.supplier_name && (
                    <div>
                      <Text size="sm" fw={500} mb="xs">
                        Primary Supplier
                      </Text>
                      <Text size="sm">{item.supplier_name}</Text>
                    </div>
                  )}
                </Stack>
              </Card>

              {/* Hazmat Information */}
              {item.is_hazardous && (
                <Card withBorder p="md">
                  <Stack gap="md">
                    <Title order={4}>Hazardous Materials</Title>
                    <div>
                      <Text size="sm" fw={500} mb="xs">
                        Compliance Status
                      </Text>
                      <Badge color={item.hazmat_compliance_status === 'Complete' ? 'green' : 'orange'}>
                        {item.hazmat_compliance_status}
                      </Badge>
                    </div>
                    {item.msds_url && (
                      <div>
                        <Text size="sm" fw={500} mb="xs">
                          MSDS/SDS
                        </Text>
                        <Text size="sm">
                          <a href={item.msds_url} target="_blank" rel="noopener noreferrer">
                            View MSDS
                          </a>
                        </Text>
                      </div>
                    )}
                    <div>
                      <Text size="sm" fw={500} mb="xs">
                        NFPA Fire Diamond
                      </Text>
                      <NFPADiamond
                        health={item.nfpa_health_hazard}
                        flammability={item.nfpa_fire_hazard}
                        instability={item.nfpa_instability_hazard}
                        special={item.nfpa_special_hazards}
                      />
                    </div>
                  </Stack>
                </Card>
              )}
            </Group>

            {/* Suppliers (op-item-suppliers). Every ItemSupplier link for this
                item, which is the supplier source of truth. The page used to
                render `item.supplier_name` — the READ-ONLY legacy accessor that
                `InventoryItemSerializer` documents as superseded by
                `suppliers[]` — so an item with three suppliers showed exactly
                one name, and the operator had no way to tell there were others.
                Rendered for an item with none too: "no suppliers are linked" is
                a fact worth stating on a reorder screen.

                SIGNED-IN ONLY, and this gate is DELIBERATELY PARTIAL. This
                route is not behind RequireAuth and `retrieve` is AllowAny, so
                without the gate this card would widen what an anonymous visitor
                sees from one supplier name to the whole sourcing table. Gating
                it removes that widening; it does NOT close the posture. The
                same SKUs, UPCs and lead times remain anonymously reachable
                through SupplierViewSet and ItemSupplierViewSet (both
                IsAuthenticatedOrReadOnly) and through the equally unguarded
                /inventory/suppliers/:id page, which already renders per-item
                supplier SKU and lead time. Whether that data should be
                anonymously readable at all is filed as separate work: a real
                fix spans views.py, App.tsx and ScanTTY's contract, and is
                outside this change's no-API-change constraint. */}
            {isLoggedIn && (
              <Card withBorder p="md" data-testid="item-suppliers-card">
                <Stack gap="md">
                  <Title order={4}>Suppliers</Title>
                  {supplierLinks === undefined ? (
                    <Text size="sm" c="dimmed" data-testid="suppliers-unknown-note">
                      Supplier information was not included in this response.
                    </Text>
                  ) : supplierLinks.length === 0 ? (
                    <Text size="sm" c="dimmed" data-testid="no-suppliers-note">
                      No suppliers are linked to this item.
                    </Text>
                  ) : (
                    <>
                      {/* Two notes, and which one shows is decided by whether
                          anything on this table can actually be bought — not by
                          whether a Primary badge is on screen. "Nobody flagged
                          one" and "nothing here is orderable" are different
                          facts needing different actions from the operator, and
                          collapsing them into one line is the mistake this
                          replaces.

                          The selection sentence names its mechanism, and must
                          keep matching it. EVERY backend path now resolves the
                          supplier through
                          `inventory.services.supplier_selection`: skip the
                          links you cannot order through, honour a flagged
                          primary outright, and otherwise score the rest on
                          price AND lead time. "Cheapest" would be wrong — the
                          fallback weighs speed too, so the chosen row is not
                          always the one with the lowest number in a column on
                          this very table, and a note claiming otherwise would
                          be contradicted on screen.

                          It also names a remedy: `is_primary` became writable
                          from the item form in #1034, and it is a GATE rather
                          than a bonus, so flagging one really does decide the
                          answer rather than nudging it. */}
                      {!supplierLinks.some((link) => link.is_active && !link.is_discontinued) ? (
                        <Text size="sm" c="orange" data-testid="no-orderable-supplier-note">
                          No supplier here can be ordered from — every link is inactive or
                          discontinued. Reactivate one, or add a supplier that still carries this
                          item, before it can go on a purchase order.
                        </Text>
                      ) : (
                        !supplierLinks.some(
                          (link) => link.is_primary && link.is_active && !link.is_discontinued
                        ) && (
                          <Text size="sm" c="dimmed" data-testid="no-primary-supplier-note">
                            No supplier you can order from is flagged primary, so the system picks
                            one on price and lead time. Flag one on the item form to decide for
                            yourself instead.
                          </Text>
                        )
                      )}
                      <Table>
                        <Table.Thead>
                          <Table.Tr>
                            <Table.Th>Supplier</Table.Th>
                            <Table.Th>Their SKU</Table.Th>
                            <Table.Th>Package UPC</Table.Th>
                            <Table.Th>Unit UPC</Table.Th>
                            <Table.Th>Lead Time</Table.Th>
                          </Table.Tr>
                        </Table.Thead>
                        <Table.Tbody>
                          {supplierLinks.map((link) => {
                            // Emphasis per column follows `cellEmphasis`'s
                            // actionable-figure-vs-identifier rule; see it for why
                            // the name and the lead time dim while the SKU and
                            // UPCs stay fully legible.
                            const unorderable = link.is_discontinued || !link.is_active;
                            const nameEmphasis = cellEmphasis('actionable-figure', unorderable);
                            const skuEmphasis = cellEmphasis('identifier', unorderable);
                            const packageUpcEmphasis = cellEmphasis('identifier', unorderable);
                            const unitUpcEmphasis = cellEmphasis('identifier', unorderable);
                            const leadTimeEmphasis = cellEmphasis('actionable-figure', unorderable);
                            return (
                              <Table.Tr key={link.id} data-testid={`item-supplier-${link.id}`}>
                                <Table.Td data-testid={`supplier-name-${link.id}`} data-emphasis={nameEmphasis}>
                                  <Group gap="xs" wrap="wrap">
                                    <Text size="sm" fw={500} c={dimColor(nameEmphasis)}>
                                      {link.supplier_name}
                                    </Text>
                                    {link.is_primary && (
                                      <Badge size="sm" color="blue">
                                        Primary
                                      </Badge>
                                    )}
                                    {link.is_discontinued && (
                                      <Badge size="sm" color="red" variant="light">
                                        Discontinued
                                      </Badge>
                                    )}
                                    {!link.is_active && (
                                      <Badge size="sm" color="gray" variant="light">
                                        Inactive
                                      </Badge>
                                    )}
                                  </Group>
                                </Table.Td>
                                <Table.Td data-testid={`supplier-sku-${link.id}`} data-emphasis={skuEmphasis}>
                                  {recordedValue(link.supplier_sku, skuEmphasis)}
                                </Table.Td>
                                <Table.Td
                                  data-testid={`supplier-package-upc-${link.id}`}
                                  data-emphasis={packageUpcEmphasis}
                                >
                                  {recordedValue(link.package_upc, packageUpcEmphasis)}
                                </Table.Td>
                                <Table.Td data-testid={`supplier-unit-upc-${link.id}`} data-emphasis={unitUpcEmphasis}>
                                  {recordedValue(link.unit_upc, unitUpcEmphasis)}
                                </Table.Td>
                                <Table.Td data-testid={`supplier-lead-time-${link.id}`} data-emphasis={leadTimeEmphasis}>
                                  {leadTimeValue(link.average_lead_time, leadTimeEmphasis)}
                                </Table.Td>
                              </Table.Tr>
                            );
                          })}
                        </Table.Tbody>
                      </Table>
                    </>
                  )}
                </Stack>
              </Card>
            )}

            {/* QR Code */}
            {item.qr_code && (
              <Card withBorder p="md">
                <Stack gap="md" align="center">
                  <Title order={4}>QR Code</Title>
                  <QRCodeSVG value={`${window.location.origin}/inventory/scan/${item.id}`} size={200} />
                  <Text size="xs" c="dimmed">
                    Scan to view item details
                  </Text>
                </Stack>
              </Card>
            )}

            {/* Supplied by kits (op-8n0). A CARD on Overview rather than a new
                tab: the tab bar already carries 6-7 tabs, and this is a 0-2 row
                fact that is most useful while looking at stock. Rendered only
                when the item actually belongs to a kit. */}
            {suppliedByKits.length > 0 && (
              <Card withBorder p="md" data-testid="supplied-by-kits-card">
                <Stack gap="md">
                  <Title order={4}>Supplied by kits</Title>
                  <Text size="sm" c="dimmed">
                    This item also arrives inside these kits. Ordering one is a single
                    purchase-order line.
                  </Text>
                  <Stack gap="xs">
                    {suppliedByKits.map((kit) => (
                      <Group key={kit.id} justify="space-between" wrap="nowrap">
                        <Anchor
                          component={Link}
                          to={`/inventory/kits/${kit.id}`}
                          data-testid={`supplied-by-kit-${kit.id}`}
                        >
                          {kit.name}
                        </Anchor>
                        <Group gap="sm" wrap="nowrap">
                          {kit.quantity_in_kit !== null && (
                            <Text size="sm" c="dimmed">
                              {kit.quantity_in_kit} per kit
                            </Text>
                          )}
                          {kit.unit_cost === null ? (
                            <Text size="sm" c="dimmed">
                              no price on file
                            </Text>
                          ) : (
                            <Text size="sm" fw={600}>
                              ${kit.unit_cost.toFixed(2)}
                            </Text>
                          )}
                        </Group>
                      </Group>
                    ))}
                  </Stack>
                </Stack>
              </Card>
            )}

            {/* Notes */}
            {item.notes && (
              <Card withBorder p="md">
                <Stack gap="md">
                  <Title order={4}>Notes</Title>
                  <Text size="sm">{item.notes}</Text>
                </Stack>
              </Card>
            )}
          </Stack>
        </Tabs.Panel>

        {/* Stock History Tab */}
        <Tabs.Panel value="stock-history" pt="md">
          <StockHistoryChart data={stockHistory} />
        </Tabs.Panel>

        {/* Reorder History Tab */}
        <Tabs.Panel value="reorder-history" pt="md">
          <Card withBorder p="md">
            <Title order={4} mb="md">
              Reorder History
            </Title>
            {reorderHistory.length === 0 ? (
              <Text c="dimmed">No reorder history available.</Text>
            ) : (
              <Table>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Date</Table.Th>
                    <Table.Th>Quantity</Table.Th>
                    <Table.Th>Status</Table.Th>
                    <Table.Th>Requested By</Table.Th>
                    <Table.Th>Notes</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {reorderHistory.map((req) => (
                    <Table.Tr key={req.id}>
                      <Table.Td>{new Date(req.requested_at).toLocaleDateString()}</Table.Td>
                      <Table.Td>{req.quantity}</Table.Td>
                      <Table.Td>
                        <Badge color={req.status === 'received' ? 'green' : req.status === 'ordered' ? 'blue' : 'yellow'}>
                          {req.status}
                        </Badge>
                      </Table.Td>
                      <Table.Td>{req.requested_by}</Table.Td>
                      <Table.Td>{req.request_notes || '-'}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            )}
          </Card>
        </Tabs.Panel>

        {/* Purchase / Receipts Tab — per-order cost history plus every
            delivery of this item, grouped by order so one order's several
            tracking numbers read as one shipment set (op-96uo). */}
        <Tabs.Panel value="purchase-receipts" pt="md">
          <PurchaseReceiptsPanel history={purchaseHistory} />
        </Tabs.Panel>

        {/* Usage Logs Tab */}
        <Tabs.Panel value="usage-logs" pt="md">
          <Card withBorder p="md">
            <Title order={4} mb="md">
              Usage Logs
            </Title>
            {usageLogs.length === 0 ? (
              <Text c="dimmed">No usage logs available.</Text>
            ) : (
              <Table>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Date</Table.Th>
                    <Table.Th>Quantity Used</Table.Th>
                    <Table.Th>Notes</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {usageLogs.map((log) => (
                    <Table.Tr key={log.id}>
                      <Table.Td>{new Date(log.usage_date).toLocaleDateString()}</Table.Td>
                      <Table.Td>{log.quantity_used}</Table.Td>
                      <Table.Td>{log.notes || '-'}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            )}
          </Card>
        </Tabs.Panel>

        {/* Linked Assets Tab — two distinct relationships, never conflated:
            (1) assets that CONSUME this item as a part (AssetPart), which is
            what people mean by "what uses this?", and (2) assets that ARE an
            instance of this item type (Asset.inventory_item). (op-qdfr) */}
        <Tabs.Panel value="linked-assets" pt="md">
          <Stack gap="md">
            <Card withBorder p="md" data-testid="assets-using-item">
              <Title order={4} mb="xs">
                Assets that use this item
              </Title>
              <Text size="sm" c="dimmed" mb="md">
                Equipment that lists this item as a part or consumable.
              </Text>
              {usedByAssets.length === 0 ? (
                <Text c="dimmed">No assets use this item as a part.</Text>
              ) : (
                <Table>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Asset Name</Table.Th>
                      <Table.Th>Asset Tag</Table.Th>
                      <Table.Th>Qty Needed</Table.Th>
                      <Table.Th>Required</Table.Th>
                      <Table.Th>Status</Table.Th>
                      <Table.Th>Location</Table.Th>
                      <Table.Th>Actions</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {usedByAssets.map((asset) => {
                      // The asset payload already nests its AssetPart rows, so
                      // the through-model detail for *this* item is free — no
                      // extra round-trip and no new endpoint.
                      const partLink = (asset.parts || []).find((p) => p.part === id);
                      return (
                        <Table.Tr key={asset.id}>
                          <Table.Td>{asset.name}</Table.Td>
                          <Table.Td>{asset.asset_tag || '-'}</Table.Td>
                          <Table.Td>{partLink ? partLink.quantity_needed : '-'}</Table.Td>
                          <Table.Td>
                            {partLink ? (
                              <Badge color={partLink.is_required ? 'red' : 'gray'} variant="light">
                                {partLink.is_required ? 'Required' : 'Optional'}
                              </Badge>
                            ) : (
                              '-'
                            )}
                          </Table.Td>
                          <Table.Td>
                            <Badge color={asset.status === 'active' ? 'green' : 'gray'}>
                              {asset.status}
                            </Badge>
                          </Table.Td>
                          <Table.Td>{asset.location_name || '-'}</Table.Td>
                          <Table.Td>
                            <Button
                              size="xs"
                              variant="subtle"
                              onClick={() => navigate(`/inventory/scan/asset/${asset.id}`)}
                            >
                              View
                            </Button>
                          </Table.Td>
                        </Table.Tr>
                      );
                    })}
                  </Table.Tbody>
                </Table>
              )}
            </Card>

            <Card withBorder p="md" data-testid="assets-of-this-type">
              <Title order={4} mb="xs">
                Assets of this type
              </Title>
              <Text size="sm" c="dimmed" mb="md">
                Tracked assets that are an instance of this inventory item.
              </Text>
              {linkedAssets.length === 0 ? (
                <Text c="dimmed">No assets are an instance of this inventory item.</Text>
              ) : (
                <Table>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Asset Name</Table.Th>
                      <Table.Th>Asset Tag</Table.Th>
                      <Table.Th>Status</Table.Th>
                      <Table.Th>Location</Table.Th>
                      <Table.Th>Actions</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {linkedAssets.map((asset) => (
                      <Table.Tr key={asset.id}>
                        <Table.Td>{asset.name}</Table.Td>
                        <Table.Td>{asset.asset_tag || '-'}</Table.Td>
                        <Table.Td>
                          <Badge color={asset.status === 'active' ? 'green' : 'gray'}>{asset.status}</Badge>
                        </Table.Td>
                        <Table.Td>{asset.location_name || '-'}</Table.Td>
                        <Table.Td>
                          <Button
                            size="xs"
                            variant="subtle"
                            onClick={() => navigate(`/inventory/scan/asset/${asset.id}`)}
                          >
                            View
                          </Button>
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              )}
            </Card>
          </Stack>
        </Tabs.Panel>

        {/* Serialized Units Tab — per-unit lifecycle for serialized items.
            Shown for every item; when the item isn't serialized the panel
            renders a CTA to enable tracking rather than a dead view (op-qff). */}
        <Tabs.Panel value="serialized-units" pt="md">
          <SerializedComponentsPanel
            itemId={item.id}
            itemName={item.name}
            trackingMode={item.serial_tracking_mode}
            serializedStock={item.serialized_stock}
            onStockChanged={loadData}
            isSerialized={item.is_serialized ?? false}
            onEnableTracking={() => navigate(`/inventory/items/${item.id}/edit`)}
          />
        </Tabs.Panel>
      </Tabs>

      <CycleCountModal
        itemId={item.id}
        itemName={item.name}
        currentStock={item.current_stock}
        packCounted={packCounted}
        countUnit={countUnit}
        countAtLevel={
          item.on_hand_display?.level_count ?? item.on_hand_display?.sealed ?? item.current_stock
        }
        isOpenClosed={item.count_mode === 'open_closed'}
        openCount={openCount}
        opened={cycleCountOpen}
        onClose={() => setCycleCountOpen(false)}
        onCounted={loadData}
      />

      <LogUsageModal
        itemId={item.id}
        itemName={item.name}
        unitCost={item.unit_cost}
        packCounted={packCounted}
        countUnit={countUnit}
        packBaseUnits={countLevelOf(item)?.base_units ?? 1}
        opened={logUsageOpen}
        onClose={() => setLogUsageOpen(false)}
        onLogged={loadData}
      />
    </WorkspacePage>
  );
};

export default InventoryItemDetailPage;
