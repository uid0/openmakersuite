/**
 * ForgeKey e-Paper Panels — staff management screen.
 *
 * Lists every e-paper PM panel with its asset binding, battery level, last
 * image fetch, and active/retired state. Staff can rebind a panel to a
 * different asset (via the existing QR bind flow) or retire / reactivate it.
 */
import { Alert, Anchor, Badge, Button, Group, Loader, Paper, Select, Table, Text } from '@mantine/core';
import React, { useCallback, useEffect, useState } from 'react';
import { Link, Navigate } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import { assetsAPI, ForgeKeyEPaperDisplay, forgekeyAPI } from '../services/api';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const formatRelative = (iso: string | null): string => {
  if (!iso) return 'never';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
};

const ForgeKeyEPaperPanelsPage: React.FC = () => {
  const isStaff = typeof window !== 'undefined' && localStorage.getItem('is_staff') === 'true';
  const isSuperuser =
    typeof window !== 'undefined' && localStorage.getItem('is_superuser') === 'true';

  const [panels, setPanels] = useState<ForgeKeyEPaperDisplay[]>([]);
  const [assets, setAssets] = useState<{ value: string; label: string }[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await forgekeyAPI.listEPaperDisplays();
      setPanels(res.data);
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to load e-paper panels.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isStaff && !isSuperuser) return;
    load();
  }, [isStaff, isSuperuser, load]);

  // Assets for the inline "bind to asset" picker. listAssets() is
  // DRF-paginated (default PAGE_SIZE=50), so without page_size we'd only
  // show the first 50 — operators couldn't bind a panel to anything past
  // that page. Pull every active asset in one shot and sort by name; the
  // Mantine Select filters client-side.
  useEffect(() => {
    if (!isStaff && !isSuperuser) return;
    let alive = true;
    assetsAPI
      .listAssets({ is_active: true, page_size: 1000, ordering: 'name' })
      .then((res) => {
        if (alive) {
          setAssets(res.data.results.map((a) => ({ value: String(a.id), label: a.name })));
        }
      })
      .catch(() => {
        /* the asset picker is optional */
      });
    return () => {
      alive = false;
    };
  }, [isStaff, isSuperuser]);

  if (!isStaff && !isSuperuser) {
    return <Navigate to="/" replace />;
  }

  const toggleActive = async (panel: ForgeKeyEPaperDisplay) => {
    setBusyId(panel.id);
    try {
      const res = await forgekeyAPI.setEPaperActive(panel.id, !panel.is_active);
      setPanels((prev) => prev.map((p) => (p.id === panel.id ? { ...p, ...res.data } : p)));
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to update the panel.'));
    } finally {
      setBusyId(null);
    }
  };

  const handleBind = async (panel: ForgeKeyEPaperDisplay, assetId: string | null) => {
    if (!assetId || assetId === panel.asset) {
      return;
    }
    setBusyId(panel.id);
    try {
      await forgekeyAPI.bindEPaper(panel.id, assetId);
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to bind the panel.'));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <WorkspacePage
      testId="forgekey-epaper-panels-page"
      hero={{
        eyebrow: 'Facilities · ForgeKey',
        title: 'e-Paper panels',
        description:
          'Every PM e-paper panel — asset binding, battery, last refresh, and status.',
      }}
    >
      {error && (
        <Alert color="red" variant="light" data-testid="panels-error">
          {error}
        </Alert>
      )}

      {loading ? (
        <Group justify="center" p="xl">
          <Loader />
        </Group>
      ) : panels.length === 0 ? (
        <Paper p="xl" withBorder>
          <Text c="dimmed" data-testid="panels-empty">
            No e-paper panels have registered yet.
          </Text>
        </Paper>
      ) : (
        <Paper withBorder>
          <Table.ScrollContainer minWidth={760}>
            <Table highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Asset</Table.Th>
                  <Table.Th>Battery</Table.Th>
                  <Table.Th>Last refresh</Table.Th>
                  <Table.Th>Device</Table.Th>
                  <Table.Th>Status</Table.Th>
                  <Table.Th>Actions</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {panels.map((p) => (
                  <Table.Tr key={p.id} data-testid={`panel-row-${p.id}`}>
                    <Table.Td>
                      {p.asset_name ? (
                        <>
                          <Text fw={500}>{p.asset_name}</Text>
                          {p.asset_tag && (
                            <Text size="xs" c="dimmed">
                              {p.asset_tag}
                            </Text>
                          )}
                        </>
                      ) : (
                        <Text c="dimmed">Unbound</Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      {p.battery_percent == null ? (
                        <Text size="sm" c="dimmed">
                          —
                        </Text>
                      ) : (
                        <Badge
                          color={p.is_low_battery ? 'orange' : 'gray'}
                          variant={p.is_low_battery ? 'filled' : 'light'}
                        >
                          {p.battery_percent}%
                        </Badge>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" c="dimmed">
                        {formatRelative(p.last_image_at)}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" c="dimmed">
                        {p.device_mac_address || '—'}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Badge color={p.is_active ? 'green' : 'gray'}>
                        {p.is_active ? 'Active' : 'Retired'}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Group gap="xs" wrap="nowrap" align="center">
                        <Select
                          size="xs"
                          placeholder="Bind asset…"
                          data={assets}
                          value={p.asset}
                          onChange={(v) => handleBind(p, v)}
                          searchable
                          disabled={busyId === p.id}
                          comboboxProps={{ withinPortal: true }}
                          style={{ minWidth: 170 }}
                          data-testid={`bind-select-${p.id}`}
                        />
                        <Button
                          size="xs"
                          variant="subtle"
                          color={p.is_active ? 'red' : 'green'}
                          loading={busyId === p.id}
                          onClick={() => toggleActive(p)}
                        >
                          {p.is_active ? 'Retire' : 'Reactivate'}
                        </Button>
                        <Anchor
                          component={Link}
                          to={`/forgekey/epaper/bind?did=${p.id}`}
                          size="xs"
                          c="dimmed"
                          title="Bind by scanning the panel QR instead"
                        >
                          QR
                        </Anchor>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Paper>
      )}
    </WorkspacePage>
  );
};

export default ForgeKeyEPaperPanelsPage;
