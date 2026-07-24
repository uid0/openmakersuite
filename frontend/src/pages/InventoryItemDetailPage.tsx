/**
 * Inventory Item Detail Page
 * Comprehensive detail view with tabs for overview, stock history, reorder history, usage logs, and linked assets
 */
import {
    ActionIcon,
    Alert,
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
import { IconArchive, IconArchiveOff, IconClipboardCheck, IconEdit, IconPackageExport, IconQrcode } from '@tabler/icons-react';
import { QRCodeSVG } from 'qrcode.react';
import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import InventoryMetricsRow from '../components/inventory/InventoryMetricsRow';
import SerializedComponentsPanel from '../components/inventory/SerializedComponentsPanel';
import WorkspacePage from '../components/landing/WorkspacePage';
import NFPADiamond from '../components/NFPADiamond';
import StockHistoryChart from '../components/StockHistoryChart';
import { useNotifications } from '../hooks/useNotifications';
import { assetsAPI, CycleCountPayload, inventoryAPI, reorderAPI, sigAPI } from '../services/api';
import { Asset, InventoryItem, InventoryItemMetrics, ReorderRequest, SIG, StockHistory, UsageLog } from '../types';
import { showError } from '../utils/dialogs';

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
  opened: boolean;
  onClose: () => void;
  onCounted: () => void;
}

/**
 * Modal for recording a physical cycle count. Mirrors the reconciliation form
 * controls: counted quantity, reason, notes, and a skip-reorder toggle. On
 * success it reloads the parent item so days-since-last-count refreshes.
 */
const CycleCountModal: React.FC<CycleCountModalProps> = ({
  itemId,
  itemName,
  currentStock,
  opened,
  onClose,
  onCounted,
}) => {
  const notifications = useNotifications();
  const [countedQty, setCountedQty] = useState<number | ''>(currentStock);
  const [reason, setReason] = useState<CycleCountPayload['reason']>('miscounted');
  const [notes, setNotes] = useState('');
  const [skipReorder, setSkipReorder] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // Re-seed the form from the current system on-hand each time the modal opens
  // so the operator starts from the number they're reconciling against.
  useEffect(() => {
    if (opened) {
      setCountedQty(currentStock);
      setReason('miscounted');
      setNotes('');
      setSkipReorder(false);
    }
  }, [opened, currentStock]);

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
      });
      const delta = data.reconciliation.delta;
      notifications.showSuccess(
        'Cycle count recorded',
        `On-hand set to ${data.current_stock} (Δ ${delta >= 0 ? '+' : ''}${delta}).`
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
          label="Counted quantity"
          description="Physical count of units on the shelf"
          value={countedQty}
          onChange={(v) => setCountedQty(v === '' ? '' : Number(v))}
          min={0}
          allowDecimal={false}
          allowNegative={false}
          required
          data-testid="cycle-count-qty"
        />
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
  // "no unit cost" hint.
  const projectedCharge = unitCost != null ? (parseFloat(unitCost) * qty).toFixed(2) : null;

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
          label="Quantity used"
          description="Units consumed from stock"
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

  const [item, setItem] = useState<InventoryItem | null>(null);
  const [metrics, setMetrics] = useState<InventoryItemMetrics | null>(null);
  const [usageLogs, setUsageLogs] = useState<UsageLog[]>([]);
  const [stockHistory, setStockHistory] = useState<StockHistory | null>(null);
  const [reorderHistory, setReorderHistory] = useState<ReorderRequest[]>([]);
  const [linkedAssets, setLinkedAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<string | null>('overview');
  const [cycleCountOpen, setCycleCountOpen] = useState(false);
  const [logUsageOpen, setLogUsageOpen] = useState(false);
  const [retiring, setRetiring] = useState(false);

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
    const [itemRes, metricsRes, usageLogsRes, stockHistoryRes, reorderRes, assetsRes] =
      await Promise.allSettled([
        inventoryAPI.getItem(id),
        inventoryAPI.getItemMetrics(id),
        inventoryAPI.getUsageLogs(id),
        inventoryAPI.getStockHistory(id),
        reorderAPI.listRequests({ status: undefined }),
        assetsAPI.listAssets({ inventory_item: id }),
      ]);

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
          (issue-5): SKU · QOH · QOO · QA · QC · QIT · RP · Lead · Cost. */}
      {metrics && <InventoryMetricsRow sku={item.sku} metrics={metrics} />}

      {/* Tabs */}
      <Tabs value={activeTab} onChange={setActiveTab}>
        <Tabs.List>
          <Tabs.Tab value="overview">Overview</Tabs.Tab>
          <Tabs.Tab value="stock-history">Stock History</Tabs.Tab>
          <Tabs.Tab value="reorder-history">Reorder History</Tabs.Tab>
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
                    <Text size="sm" fw={600} c={item.needs_reorder ? 'red' : undefined}>
                      {item.current_stock} {item.use_case_based_reorder ? 'units' : 'units'}
                    </Text>
                  </Group>
                  {item.use_case_based_reorder && (
                    <Group justify="space-between">
                      <Text size="sm">Current Cases:</Text>
                      <Text size="sm" fw={600}>
                        {item.current_cases.toFixed(1)} cases
                      </Text>
                    </Group>
                  )}
                  <Group justify="space-between">
                    <Text size="sm">Minimum Stock:</Text>
                    <Text size="sm">
                      {item.use_case_based_reorder ? `${item.minimum_cases} cases` : `${item.minimum_stock} units`}
                    </Text>
                  </Group>
                  <Group justify="space-between">
                    <Text size="sm">Reorder Quantity:</Text>
                    <Text size="sm">
                      {item.use_case_based_reorder ? `${item.reorder_cases} cases` : `${item.reorder_quantity} units`}
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
                  {item.supplier_name && (
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

        {/* Linked Assets Tab */}
        <Tabs.Panel value="linked-assets" pt="md">
          <Card withBorder p="md">
            <Title order={4} mb="md">
              Linked Assets
            </Title>
            {linkedAssets.length === 0 ? (
              <Text c="dimmed">No assets linked to this inventory item.</Text>
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
                        <ActionIcon
                          variant="subtle"
                          onClick={() => navigate(`/inventory/scan/asset/${asset.id}`)}
                        >
                          View
                        </ActionIcon>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            )}
          </Card>
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
        opened={cycleCountOpen}
        onClose={() => setCycleCountOpen(false)}
        onCounted={loadData}
      />

      <LogUsageModal
        itemId={item.id}
        itemName={item.name}
        unitCost={item.unit_cost}
        opened={logUsageOpen}
        onClose={() => setLogUsageOpen(false)}
        onLogged={loadData}
      />
    </WorkspacePage>
  );
};

export default InventoryItemDetailPage;
