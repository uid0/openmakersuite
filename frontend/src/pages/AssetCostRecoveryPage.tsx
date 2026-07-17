/**
 * Asset Cost-Recovery Report generator.
 *
 * Pick one or more assets and/or asset categories plus a reporting period,
 * then Generate a per-asset service statement with Estimated (internal) and
 * Actual (vendor-invoice / recorded) columns. The Actual total is the amount
 * recoverable from the landlord. CSV and the landlord-ready PDF are produced
 * server-side and downloaded as blobs.
 *
 * Consumes reports/assets/cost_recovery/ (PR1, backend) — see
 * inventory/views.py AssetReportViewSet.cost_recovery.
 */
import { Alert, Button, Group, Loader, MultiSelect, Paper, SegmentedControl, Stack, Table, Text, Title } from '@mantine/core';
import { DatePickerInput, DatesRangeValue } from '@mantine/dates';
import { IconAlertTriangle, IconDownload } from '@tabler/icons-react';
import dayjs from 'dayjs';
import React, { useCallback, useEffect, useMemo, useState } from 'react';

import { assetsAPI, CostRecoveryParams, CostRecoveryPeriod, inventoryAPI, reportsAPI } from '../services/api';
import { Asset, AssetCostRecoveryReport, Category } from '../types';
import { extractErrorMessage } from '../utils/extractErrorMessage';

type PeriodMode = CostRecoveryPeriod | 'custom';

const PERIOD_OPTIONS: Array<{ label: string; value: PeriodMode }> = [
  { label: 'Past week', value: 'past_week' },
  { label: 'Past month', value: 'past_month' },
  { label: 'Past year', value: 'past_year' },
  { label: 'These dates', value: 'custom' },
];

const PERIOD_LABELS: Record<CostRecoveryPeriod, string> = {
  past_week: 'Past week',
  past_month: 'Past month',
  past_year: 'Past year',
};

// Human labels for a service line's origin.
const SOURCE_LABELS: Record<string, string> = {
  pm: 'Internal PM',
  vendor: 'Vendor',
  manual: 'Recorded',
};

const formatMoney = (value: string | number | null): string => {
  const num = typeof value === 'number' ? value : parseFloat(value || '0');
  return Number.isFinite(num) ? num.toFixed(2) : '0.00';
};

const describeWindow = (report: AssetCostRecoveryReport): string =>
  report.period ? PERIOD_LABELS[report.period] : `${report.start_date} → ${report.end_date}`;

// Pre-fill the custom range with the trailing 30 days so switching to "These
// dates" starts from a sensible, immediately-runnable window the user adjusts.
const getDefaultDateRange = (): DatesRangeValue => [
  dayjs().subtract(30, 'day').toDate(),
  new Date(),
];

const AssetCostRecoveryPage: React.FC = () => {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [loadingOptions, setLoadingOptions] = useState(true);

  const [selectedAssetIds, setSelectedAssetIds] = useState<string[]>([]);
  const [selectedCategoryIds, setSelectedCategoryIds] = useState<string[]>([]);
  const [periodMode, setPeriodMode] = useState<PeriodMode>('past_month');
  const [dateRange, setDateRange] = useState<DatesRangeValue>(getDefaultDateRange);

  const [report, setReport] = useState<AssetCostRecoveryReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState<'csv' | 'pdf' | null>(null);

  // Load the asset + category pickers once. Guard the responses so an
  // undefined payload (e.g. a fully mocked API in tests) can't throw.
  useEffect(() => {
    let cancelled = false;
    setLoadingOptions(true);
    Promise.all([assetsAPI.listAssets({ page_size: 500 }), inventoryAPI.listCategories()])
      .then(([assetRes, catRes]) => {
        if (cancelled) return;
        setAssets(assetRes?.data?.results ?? []);
        setCategories(catRes?.data?.results ?? []);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(extractErrorMessage(err, 'Failed to load assets and categories.'));
      })
      .finally(() => {
        if (!cancelled) setLoadingOptions(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const assetOptions = useMemo(
    () =>
      assets.map((a) => ({
        value: a.id,
        label: a.asset_tag ? `${a.asset_tag} — ${a.name}` : a.name,
      })),
    [assets],
  );

  const categoryOptions = useMemo(
    () => categories.map((c) => ({ value: String(c.id), label: c.name })),
    [categories],
  );

  const hasSelection = selectedAssetIds.length > 0 || selectedCategoryIds.length > 0;
  const periodComplete = periodMode !== 'custom' || Boolean(dateRange[0] && dateRange[1]);
  const canRun = hasSelection && periodComplete;

  // Assemble the request params, or null if the selection/window is incomplete.
  const buildParams = useCallback((): CostRecoveryParams | null => {
    if (!hasSelection) return null;
    const base: CostRecoveryParams = {
      asset_ids: selectedAssetIds,
      category_ids: selectedCategoryIds.map(Number),
    };
    if (periodMode === 'custom') {
      if (!dateRange[0] || !dateRange[1]) return null;
      return {
        ...base,
        start_date: dayjs(dateRange[0]).format('YYYY-MM-DD'),
        end_date: dayjs(dateRange[1]).format('YYYY-MM-DD'),
      };
    }
    return { ...base, period: periodMode };
  }, [hasSelection, selectedAssetIds, selectedCategoryIds, periodMode, dateRange]);

  const handleGenerate = async () => {
    const params = buildParams();
    if (!params) {
      setError('Select at least one asset or category and a complete period.');
      return;
    }
    try {
      setLoading(true);
      setError(null);
      const response = await reportsAPI.getAssetCostRecovery(params);
      setReport(response.data);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to generate the cost-recovery report.'));
      setReport(null);
    } finally {
      setLoading(false);
    }
  };

  const handleExport = async (format: 'csv' | 'pdf') => {
    const params = buildParams();
    if (!params) {
      setError('Select at least one asset or category and a complete period.');
      return;
    }
    try {
      setExporting(format);
      setError(null);
      const response = await reportsAPI.downloadAssetCostRecovery(params, format);
      const mime = format === 'pdf' ? 'application/pdf' : 'text/csv';
      const blob = new Blob([response.data], { type: mime });
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `asset_cost_recovery.${format}`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch (err) {
      setError(extractErrorMessage(err, `Failed to export ${format.toUpperCase()}.`));
    } finally {
      setExporting(null);
    }
  };

  return (
    <Stack gap="md" p="md">
      <Title order={2}>Asset Cost-Recovery Report</Title>
      <Text size="sm" c="dimmed">
        Build a per-asset service statement billed to the landlord. The Actual
        (vendor-invoice / recorded) total is the recoverable amount; estimates
        for internal preventive maintenance are shown as context only.
      </Text>

      <Paper withBorder p="md">
        <Stack gap="md">
          <MultiSelect
            label="Assets"
            placeholder="Search assets"
            data={assetOptions}
            value={selectedAssetIds}
            onChange={setSelectedAssetIds}
            searchable
            clearable
            disabled={loadingOptions}
            data-testid="cost-recovery-assets"
          />
          <MultiSelect
            label="Categories"
            description="A category expands to all of its assets."
            placeholder="Search categories"
            data={categoryOptions}
            value={selectedCategoryIds}
            onChange={setSelectedCategoryIds}
            searchable
            clearable
            disabled={loadingOptions}
            data-testid="cost-recovery-categories"
          />

          <div>
            <Text size="sm" fw={500} mb={4}>
              Period
            </Text>
            <SegmentedControl
              value={periodMode}
              onChange={(value) => setPeriodMode(value as PeriodMode)}
              data={PERIOD_OPTIONS}
            />
          </div>

          {periodMode === 'custom' && (
            <DatePickerInput
              type="range"
              label="Date range"
              placeholder="Select start and end dates"
              value={dateRange}
              onChange={setDateRange}
              allowSingleDateInRange
              clearable
              maxDate={new Date()}
              style={{ maxWidth: 320 }}
            />
          )}

          <Group>
            <Button onClick={handleGenerate} loading={loading} disabled={!canRun}>
              Generate
            </Button>
            <Button
              variant="light"
              leftSection={<IconDownload size={16} />}
              onClick={() => handleExport('csv')}
              loading={exporting === 'csv'}
              disabled={!canRun}
            >
              Export CSV
            </Button>
            <Button
              variant="light"
              leftSection={<IconDownload size={16} />}
              onClick={() => handleExport('pdf')}
              loading={exporting === 'pdf'}
              disabled={!canRun}
            >
              Export PDF
            </Button>
          </Group>
        </Stack>
      </Paper>

      {error && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />} title="Report error">
          {error}
        </Alert>
      )}

      {loading ? (
        <Group gap="xs">
          <Loader size="sm" />
          <Text>Generating statement…</Text>
        </Group>
      ) : report === null ? (
        <Text c="dimmed">
          Choose one or more assets or categories and a period, then Generate the statement.
        </Text>
      ) : report.assets.length === 0 ? (
        <Paper withBorder p="md">
          <Text c="dimmed">No assets matched your selection for this period.</Text>
        </Paper>
      ) : (
        <Stack gap="md" data-testid="cost-recovery-results">
          <Paper withBorder p="md">
            <Group justify="space-between" wrap="wrap">
              <div>
                <Text fw={700} size="lg">
                  Grand total recoverable: ${formatMoney(report.grand_total_actual)}
                </Text>
                <Text size="sm" c="dimmed">
                  Estimated (internal, non-recoverable) context: $
                  {formatMoney(report.grand_total_estimated)}
                </Text>
              </div>
              <Text size="sm" c="dimmed">
                {report.asset_count} asset{report.asset_count === 1 ? '' : 's'} ·{' '}
                {report.service_count} service{report.service_count === 1 ? '' : 's'} ·{' '}
                {describeWindow(report)}
              </Text>
            </Group>
          </Paper>

          {report.assets.map((asset) => (
            <Paper key={asset.asset_id} withBorder p="md">
              <Group justify="space-between" mb="xs" wrap="wrap">
                <div>
                  <Text fw={600}>
                    {asset.name}
                    {asset.asset_tag ? ` — ${asset.asset_tag}` : ''}
                  </Text>
                  <Text size="sm" c="dimmed">
                    Serial: {asset.serial_number || '—'} · Installed:{' '}
                    {asset.date_received ? dayjs(asset.date_received).format('YYYY-MM-DD') : '—'} ·
                    Status: {asset.status_display}
                    {asset.category ? ` · ${asset.category}` : ''}
                  </Text>
                </div>
                <Text fw={600}>Recoverable: ${formatMoney(asset.subtotal_actual)}</Text>
              </Group>
              <Table.ScrollContainer minWidth={640}>
                <Table highlightOnHover>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Date</Table.Th>
                      <Table.Th>Source</Table.Th>
                      <Table.Th>Description</Table.Th>
                      <Table.Th ta="right">Estimated</Table.Th>
                      <Table.Th ta="right">Actual (vendor)</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {asset.services.length === 0 ? (
                      <Table.Tr>
                        <Table.Td colSpan={5} ta="center">
                          <Text c="dimmed">No services in this period</Text>
                        </Table.Td>
                      </Table.Tr>
                    ) : (
                      asset.services.map((svc, idx) => (
                        <Table.Tr key={idx}>
                          <Table.Td>{dayjs(svc.date).format('YYYY-MM-DD')}</Table.Td>
                          <Table.Td>{SOURCE_LABELS[svc.source] ?? svc.source}</Table.Td>
                          <Table.Td>{svc.description || '—'}</Table.Td>
                          <Table.Td ta="right">
                            {svc.estimated_cost != null ? `$${formatMoney(svc.estimated_cost)}` : '—'}
                          </Table.Td>
                          <Table.Td ta="right">
                            {svc.actual_cost != null ? `$${formatMoney(svc.actual_cost)}` : '—'}
                          </Table.Td>
                        </Table.Tr>
                      ))
                    )}
                  </Table.Tbody>
                  <Table.Tfoot>
                    <Table.Tr>
                      <Table.Th colSpan={3} ta="right">
                        Subtotal
                      </Table.Th>
                      <Table.Th ta="right">${formatMoney(asset.subtotal_estimated)}</Table.Th>
                      <Table.Th ta="right">${formatMoney(asset.subtotal_actual)}</Table.Th>
                    </Table.Tr>
                  </Table.Tfoot>
                </Table>
              </Table.ScrollContainer>
            </Paper>
          ))}
        </Stack>
      )}
    </Stack>
  );
};

export default AssetCostRecoveryPage;
