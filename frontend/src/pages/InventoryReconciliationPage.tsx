/**
 * Inventory Reconciliation Page (oms-90k)
 *
 * Per-location grid: one row per inventory item. User enters the counted
 * quantity, a reason code, and optional notes. On submit, a single batch
 * is POSTed — items at or below minimum stock auto-create a ReorderRequest
 * unless skip_reorder is checked.
 */
import {
  Alert,
  Button,
  Checkbox,
  Group,
  List,
  Modal,
  NumberInput,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import QRScanner from '../components/QRScanner';
import { useNotifications } from '../hooks/useNotifications';
import {
  inventoryAPI,
  ReconciliationGridItem,
  ReconciliationRow,
  ReconciliationUploadResponse,
} from '../services/api';

type ReasonCode = ReconciliationRow['reason'];

interface RowState {
  actual_count: number | '';
  reason: ReasonCode;
  notes: string;
  skip_reorder: boolean;
}

const REASON_OPTIONS: { value: ReasonCode; label: string }[] = [
  { value: 'miscounted', label: 'Miscounted' },
  { value: 'lost', label: 'Lost' },
  { value: 'damaged', label: 'Damaged' },
  { value: 'used_without_scan', label: 'Used without scanning' },
  { value: 'found', label: 'Found (positive delta)' },
  { value: 'other', label: 'Other' },
];

const InventoryReconciliationPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const notifications = useNotifications();

  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [locationName, setLocationName] = useState<string>('');
  const [items, setItems] = useState<ReconciliationGridItem[]>([]);
  const [rowState, setRowState] = useState<Record<string, RowState>>({});
  const [scannerOpen, setScannerOpen] = useState(false);
  const [uploadPartial, setUploadPartial] = useState(false);
  const [uploadBusy, setUploadBusy] = useState(false);
  const [uploadResult, setUploadResult] =
    useState<ReconciliationUploadResponse | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    try {
      setLoading(true);
      const response = await inventoryAPI.listLocationReconcileGrid(id);
      setLocationName(response.data.location_name);
      setItems(response.data.items);
      const initial: Record<string, RowState> = {};
      for (const it of response.data.items) {
        initial[it.item_id] = {
          actual_count: '',
          reason: 'miscounted',
          notes: '',
          skip_reorder: false,
        };
      }
      setRowState(initial);
      setError(null);
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || 'Failed to load reconciliation grid';
      setError(detail);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  const updateRow = (itemId: string, patch: Partial<RowState>) => {
    setRowState((prev) => ({
      ...prev,
      [itemId]: { ...prev[itemId], ...patch },
    }));
  };

  const rowsToSubmit: ReconciliationRow[] = useMemo(() => {
    return items
      .filter((it) => {
        const row = rowState[it.item_id];
        return row && row.actual_count !== '' && row.actual_count !== null;
      })
      .map((it) => {
        const row = rowState[it.item_id];
        return {
          item_id: it.item_id,
          actual_count: Number(row.actual_count),
          reason: row.reason,
          notes: row.notes,
          skip_reorder: row.skip_reorder,
        };
      });
  }, [items, rowState]);

  const handleSubmit = async () => {
    if (rowsToSubmit.length === 0) {
      notifications.showWarning(
        'Nothing to submit',
        'Enter at least one actual count before submitting.',
      );
      return;
    }
    try {
      setSubmitting(true);
      const response = await inventoryAPI.submitReconciliationBatch(rowsToSubmit);
      const { reconciled, reorders_created } = response.data;
      notifications.showSuccess(
        'Reconciliation submitted',
        `${reconciled} reconciled, ${reorders_created} reorder${reorders_created === 1 ? '' : 's'} created.`,
      );
      navigate(`/inventory/locations/${id}`);
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || 'Failed to submit reconciliation';
      notifications.showError('Failed to submit', detail);
    } finally {
      setSubmitting(false);
    }
  };

  const handleExport = async () => {
    if (!id) return;
    try {
      const response = await inventoryAPI.exportReconcileTemplate(id);
      const blob = new Blob([response.data], { type: 'text/csv' });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `reconcile_location_${id}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || 'Failed to download CSV template';
      notifications.showError('Export failed', detail);
    }
  };

  const handleUploadFile = async (file: File | null | undefined) => {
    if (!file) return;
    try {
      setUploadBusy(true);
      const response = await inventoryAPI.uploadReconcileCsv(file, uploadPartial);
      setUploadResult(response.data);
      if (response.data.created > 0) {
        await load();
      }
    } catch (err: unknown) {
      const data = (err as { response?: { data?: unknown } })?.response?.data;
      if (
        data &&
        typeof data === 'object' &&
        ('errors' in data || 'created' in data)
      ) {
        setUploadResult(data as ReconciliationUploadResponse);
      } else {
        const detail =
          (data as { detail?: string } | undefined)?.detail ||
          'Failed to upload CSV';
        notifications.showError('Upload failed', detail);
      }
    } finally {
      setUploadBusy(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  const handleScanSuccess = async (decodedText: string) => {
    setScannerOpen(false);
    try {
      const response = await inventoryAPI.lookupByCode(decodedText);
      const scannedId = (response.data as { id?: string })?.id;
      if (!scannedId) {
        notifications.showWarning('Unknown code', 'Could not resolve scanned code.');
        return;
      }
      const match = items.find((it) => it.item_id === scannedId);
      if (!match) {
        notifications.showWarning(
          'Item not in this location',
          'Scanned item is not part of this reconciliation grid.',
        );
        return;
      }
      const input = document.querySelector<HTMLInputElement>(
        `input[aria-label="Actual count for ${match.name}"]`,
      );
      if (input) {
        input.focus();
        input.select();
      }
    } catch {
      notifications.showError('Scan lookup failed', 'Could not look up scanned code.');
    }
  };

  if (loading) {
    return (
      <div style={{ padding: 16 }}>
        <Text>Loading reconciliation grid…</Text>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: 16 }}>
        <Alert color="red" title="Error">
          {error}
        </Alert>
      </div>
    );
  }

  return (
    <Stack p="md">
      <Group justify="space-between">
        <Title order={2}>Reconcile: {locationName}</Title>
        <Group>
          <Button
            variant="default"
            onClick={() => setScannerOpen(true)}
            data-testid="scan-to-prefill"
          >
            Scan to prefill
          </Button>
          <Button onClick={() => navigate(`/inventory/locations/${id}`)} variant="subtle">
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            loading={submitting}
            disabled={rowsToSubmit.length === 0}
            data-testid="submit-reconciliation"
          >
            Submit ({rowsToSubmit.length})
          </Button>
        </Group>
      </Group>

      <Group gap="sm" align="center" data-testid="csv-batch-controls">
        <Button
          variant="default"
          onClick={handleExport}
          data-testid="export-csv-template"
        >
          Export CSV template
        </Button>
        <Button
          variant="default"
          onClick={() => fileInputRef.current?.click()}
          loading={uploadBusy}
          data-testid="upload-filled-csv"
        >
          Upload filled CSV
        </Button>
        <Switch
          checked={uploadPartial}
          onChange={(e) => setUploadPartial(e.currentTarget.checked)}
          label="Skip bad rows (partial mode)"
          data-testid="upload-partial-toggle"
        />
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv,text/csv"
          style={{ display: 'none' }}
          onChange={(e) => handleUploadFile(e.currentTarget.files?.[0])}
          data-testid="csv-file-input"
        />
      </Group>

      {items.length === 0 ? (
        <Text>No items are stored at this location.</Text>
      ) : (
        <Table striped highlightOnHover withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Item</Table.Th>
              <Table.Th>SKU</Table.Th>
              <Table.Th>Projected</Table.Th>
              <Table.Th>Actual</Table.Th>
              <Table.Th>Delta</Table.Th>
              <Table.Th>Reason</Table.Th>
              <Table.Th>Notes</Table.Th>
              <Table.Th>Skip reorder</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {items.map((it) => {
              const row = rowState[it.item_id];
              if (!row) return null;
              const actual =
                row.actual_count === '' ? null : Number(row.actual_count);
              const delta = actual === null ? null : actual - it.projected;
              const wouldReorder =
                actual !== null && actual <= it.minimum_stock && !row.skip_reorder;
              return (
                <Table.Tr key={it.item_id} data-testid={`row-${it.item_id}`}>
                  <Table.Td>{it.name}</Table.Td>
                  <Table.Td>{it.sku}</Table.Td>
                  <Table.Td>{it.projected}</Table.Td>
                  <Table.Td>
                    <NumberInput
                      value={row.actual_count}
                      min={0}
                      onChange={(v) =>
                        updateRow(it.item_id, {
                          actual_count: v === '' ? '' : Number(v),
                        })
                      }
                      aria-label={`Actual count for ${it.name}`}
                      w={90}
                    />
                  </Table.Td>
                  <Table.Td>
                    {delta === null ? (
                      <Text c="dimmed">—</Text>
                    ) : (
                      <Text c={delta < 0 ? 'red' : delta > 0 ? 'green' : 'dimmed'}>
                        {delta > 0 ? `+${delta}` : delta}
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Select
                      data={REASON_OPTIONS}
                      value={row.reason}
                      onChange={(v) =>
                        v && updateRow(it.item_id, { reason: v as ReasonCode })
                      }
                      data-testid={`reason-${it.item_id}`}
                      w={180}
                    />
                  </Table.Td>
                  <Table.Td>
                    <TextInput
                      value={row.notes}
                      onChange={(e) =>
                        updateRow(it.item_id, { notes: e.currentTarget.value })
                      }
                      data-testid={`notes-${it.item_id}`}
                    />
                  </Table.Td>
                  <Table.Td>
                    <Checkbox
                      checked={row.skip_reorder}
                      onChange={(e) =>
                        updateRow(it.item_id, {
                          skip_reorder: e.currentTarget.checked,
                        })
                      }
                      label={wouldReorder ? 'will reorder' : ''}
                      aria-label={`Skip reorder for ${it.name}`}
                    />
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      )}

      <Modal
        opened={scannerOpen}
        onClose={() => setScannerOpen(false)}
        title="Scan item QR code"
        size="lg"
      >
        {scannerOpen && (
          <QRScanner
            onScanSuccess={handleScanSuccess}
            onClose={() => setScannerOpen(false)}
          />
        )}
      </Modal>

      <Modal
        opened={uploadResult !== null}
        onClose={() => setUploadResult(null)}
        title="CSV upload result"
        size="lg"
        data-testid="csv-upload-result-modal"
      >
        {uploadResult && (
          <Stack>
            <Text data-testid="csv-upload-summary">
              Created: <b>{uploadResult.created}</b> &nbsp;·&nbsp; Skipped:{' '}
              <b>{uploadResult.skipped}</b> &nbsp;·&nbsp; Errors:{' '}
              <b>{uploadResult.errors.length}</b>
            </Text>
            {uploadResult.errors.length > 0 && (
              <Alert color="red" title="Row errors">
                <List size="sm" data-testid="csv-upload-errors">
                  {uploadResult.errors.map((e) => (
                    <List.Item key={`${e.row}-${e.error}`}>
                      Row {e.row}: {e.error}
                    </List.Item>
                  ))}
                </List>
                {!uploadPartial && (
                  <Text size="sm" mt="xs">
                    Batch rolled back. Enable &quot;Skip bad rows&quot; and
                    re-upload to commit valid rows.
                  </Text>
                )}
              </Alert>
            )}
            <Group justify="flex-end">
              <Button onClick={() => setUploadResult(null)}>Close</Button>
            </Group>
          </Stack>
        )}
      </Modal>
    </Stack>
  );
};

export default InventoryReconciliationPage;
