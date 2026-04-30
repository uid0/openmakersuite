/**
 * Electrical Circuits Page (oms-a5f)
 *
 * Browse breakers, outlets, light switches, and network drops with filters
 * by Location. Provides entry points to create/edit forms and the breaker
 * trace view ("show me everything fed by this breaker").
 */
import {
  ActionIcon,
  Anchor,
  Badge,
  Button,
  Group,
  Paper,
  Select,
  Stack,
  Table,
  Tabs,
  Text,
  Title,
} from '@mantine/core';
import {
  IconBolt,
  IconBulb,
  IconEdit,
  IconNetwork,
  IconPlus,
  IconPlugConnected,
  IconRouteAltLeft,
  IconTrash,
} from '@tabler/icons-react';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  electricalCircuitsAPI,
  inventoryAPI,
  NETWORK_DROP_TYPE_OPTIONS,
  OUTLET_TYPE_OPTIONS,
} from '../services/api';
import {
  Breaker,
  LightSwitch,
  Location,
  NetworkDrop,
  NetworkDropType,
  Outlet,
} from '../types';
import { confirmDelete, showError } from '../utils/dialogs';

type ActiveTab = 'breakers' | 'outlets' | 'switches' | 'drops';

const dropTypeLabel = (value: string): string =>
  NETWORK_DROP_TYPE_OPTIONS.find((option) => option.value === value)?.label || value;

const outletTypeLabel = (value: string): string =>
  OUTLET_TYPE_OPTIONS.find((option) => option.value === value)?.label || value;

const ElectricalCircuitsPage: React.FC = () => {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState<ActiveTab>('breakers');
  const [locations, setLocations] = useState<Location[]>([]);
  const [locationFilter, setLocationFilter] = useState<string | null>(null);
  const [dropTypeFilter, setDropTypeFilter] = useState<string | null>(null);

  const [breakers, setBreakers] = useState<Breaker[]>([]);
  const [outlets, setOutlets] = useState<Outlet[]>([]);
  const [switches, setSwitches] = useState<LightSwitch[]>([]);
  const [drops, setDrops] = useState<NetworkDrop[]>([]);
  const [loading, setLoading] = useState(false);

  const locationOptions = useMemo(
    () =>
      locations.map((loc) => ({ value: String(loc.id), label: loc.name })),
    [locations],
  );

  useEffect(() => {
    inventoryAPI
      .listLocations()
      .then((response) => setLocations(response.data.results))
      .catch((err) => {
        console.error('Failed to load locations', err);
      });
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    const locationParam = locationFilter ? Number(locationFilter) : undefined;
    try {
      if (activeTab === 'breakers') {
        const response = await electricalCircuitsAPI.listBreakers({ location: locationParam });
        setBreakers(response.data.results);
      } else if (activeTab === 'outlets') {
        const response = await electricalCircuitsAPI.listOutlets({ location: locationParam });
        setOutlets(response.data.results);
      } else if (activeTab === 'switches') {
        const response = await electricalCircuitsAPI.listLightSwitches({ location: locationParam });
        setSwitches(response.data.results);
      } else if (activeTab === 'drops') {
        const response = await electricalCircuitsAPI.listNetworkDrops({
          location: locationParam,
          drop_type: (dropTypeFilter as NetworkDropType | null) ?? undefined,
        });
        setDrops(response.data.results);
      }
    } catch (err: any) {
      showError(err.response?.data?.detail || 'Failed to load records');
    } finally {
      setLoading(false);
    }
  }, [activeTab, locationFilter, dropTypeFilter]);

  useEffect(() => {
    reload();
  }, [reload]);

  const handleDeleteBreaker = (breaker: Breaker) => {
    confirmDelete(`Delete breaker ${breaker.panel}/${breaker.breaker_number}?`, async () => {
      try {
        await electricalCircuitsAPI.deleteBreaker(breaker.id);
        await reload();
      } catch (err: any) {
        showError(err.response?.data?.detail || 'Failed to delete breaker');
      }
    });
  };

  const handleDeleteOutlet = (outlet: Outlet) => {
    confirmDelete(`Delete outlet ${outlet.identifier}?`, async () => {
      try {
        await electricalCircuitsAPI.deleteOutlet(outlet.id);
        await reload();
      } catch (err: any) {
        showError(err.response?.data?.detail || 'Failed to delete outlet');
      }
    });
  };

  const handleDeleteSwitch = (sw: LightSwitch) => {
    confirmDelete(`Delete light switch ${sw.identifier}?`, async () => {
      try {
        await electricalCircuitsAPI.deleteLightSwitch(sw.id);
        await reload();
      } catch (err: any) {
        showError(err.response?.data?.detail || 'Failed to delete light switch');
      }
    });
  };

  const handleDeleteDrop = (drop: NetworkDrop) => {
    confirmDelete(`Delete network drop ${drop.identifier}?`, async () => {
      try {
        await electricalCircuitsAPI.deleteNetworkDrop(drop.id);
        await reload();
      } catch (err: any) {
        showError(err.response?.data?.detail || 'Failed to delete network drop');
      }
    });
  };

  const filterBar = (
    <Paper p="md" withBorder>
      <Group>
        <Select
          label="Location"
          placeholder="All locations"
          data={locationOptions}
          value={locationFilter}
          onChange={setLocationFilter}
          clearable
          searchable
          style={{ minWidth: 240 }}
        />
        {activeTab === 'drops' && (
          <Select
            label="Drop type"
            placeholder="All drop types"
            data={NETWORK_DROP_TYPE_OPTIONS}
            value={dropTypeFilter}
            onChange={setDropTypeFilter}
            clearable
            style={{ minWidth: 220 }}
          />
        )}
      </Group>
    </Paper>
  );

  const breakersTable = (
    <Paper withBorder>
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Panel / #</Table.Th>
            <Table.Th>Location</Table.Th>
            <Table.Th>Amps</Table.Th>
            <Table.Th>Voltage</Table.Th>
            <Table.Th>Poles</Table.Th>
            <Table.Th>Description</Table.Th>
            <Table.Th>Status</Table.Th>
            <Table.Th>Actions</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {breakers.length === 0 ? (
            <Table.Tr>
              <Table.Td colSpan={8} style={{ textAlign: 'center', padding: '1.5rem' }}>
                <Text c="dimmed">{loading ? 'Loading…' : 'No breakers found'}</Text>
              </Table.Td>
            </Table.Tr>
          ) : (
            breakers.map((breaker) => (
              <Table.Tr key={breaker.id}>
                <Table.Td>
                  <Text fw={500}>
                    {breaker.panel} / {breaker.breaker_number}
                  </Text>
                </Table.Td>
                <Table.Td>{breaker.location_name}</Table.Td>
                <Table.Td>{breaker.amperage}A</Table.Td>
                <Table.Td>{breaker.voltage}V</Table.Td>
                <Table.Td>{breaker.poles}</Table.Td>
                <Table.Td>{breaker.description || '—'}</Table.Td>
                <Table.Td>
                  {breaker.is_active ? (
                    <Badge color="green">Active</Badge>
                  ) : (
                    <Badge color="gray" variant="light">
                      Inactive
                    </Badge>
                  )}
                </Table.Td>
                <Table.Td>
                  <Group gap="xs">
                    <ActionIcon
                      variant="subtle"
                      title="Trace fed loads"
                      onClick={() =>
                        navigate(`/facilities/electrical/breakers/${breaker.id}/trace`)
                      }
                    >
                      <IconRouteAltLeft size={16} />
                    </ActionIcon>
                    <ActionIcon
                      variant="subtle"
                      title="Edit"
                      onClick={() =>
                        navigate(`/facilities/electrical/breakers/${breaker.id}/edit`)
                      }
                    >
                      <IconEdit size={16} />
                    </ActionIcon>
                    <ActionIcon
                      variant="subtle"
                      color="red"
                      title="Delete"
                      onClick={() => handleDeleteBreaker(breaker)}
                    >
                      <IconTrash size={16} />
                    </ActionIcon>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))
          )}
        </Table.Tbody>
      </Table>
    </Paper>
  );

  const outletsTable = (
    <Paper withBorder>
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Identifier</Table.Th>
            <Table.Th>Location</Table.Th>
            <Table.Th>Type</Table.Th>
            <Table.Th>Breaker</Table.Th>
            <Table.Th>Plugged in</Table.Th>
            <Table.Th>Status</Table.Th>
            <Table.Th>Actions</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {outlets.length === 0 ? (
            <Table.Tr>
              <Table.Td colSpan={7} style={{ textAlign: 'center', padding: '1.5rem' }}>
                <Text c="dimmed">{loading ? 'Loading…' : 'No outlets found'}</Text>
              </Table.Td>
            </Table.Tr>
          ) : (
            outlets.map((outlet) => (
              <Table.Tr key={outlet.id}>
                <Table.Td>
                  <Text fw={500}>{outlet.identifier}</Text>
                </Table.Td>
                <Table.Td>{outlet.location_name}</Table.Td>
                <Table.Td>{outletTypeLabel(outlet.outlet_type)}</Table.Td>
                <Table.Td>{outlet.breaker_label || '—'}</Table.Td>
                <Table.Td>
                  <Text size="sm" lineClamp={2}>
                    {outlet.plugged_in_notes || '—'}
                  </Text>
                </Table.Td>
                <Table.Td>
                  {outlet.is_active ? (
                    <Badge color="green">Active</Badge>
                  ) : (
                    <Badge color="gray" variant="light">
                      Inactive
                    </Badge>
                  )}
                </Table.Td>
                <Table.Td>
                  <Group gap="xs">
                    <ActionIcon
                      variant="subtle"
                      title="Edit"
                      onClick={() =>
                        navigate(`/facilities/electrical/outlets/${outlet.id}/edit`)
                      }
                    >
                      <IconEdit size={16} />
                    </ActionIcon>
                    <ActionIcon
                      variant="subtle"
                      color="red"
                      title="Delete"
                      onClick={() => handleDeleteOutlet(outlet)}
                    >
                      <IconTrash size={16} />
                    </ActionIcon>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))
          )}
        </Table.Tbody>
      </Table>
    </Paper>
  );

  const switchesTable = (
    <Paper withBorder>
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Identifier</Table.Th>
            <Table.Th>Mounted at</Table.Th>
            <Table.Th>Controls</Table.Th>
            <Table.Th>Breaker</Table.Th>
            <Table.Th>Status</Table.Th>
            <Table.Th>Actions</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {switches.length === 0 ? (
            <Table.Tr>
              <Table.Td colSpan={6} style={{ textAlign: 'center', padding: '1.5rem' }}>
                <Text c="dimmed">{loading ? 'Loading…' : 'No light switches found'}</Text>
              </Table.Td>
            </Table.Tr>
          ) : (
            switches.map((sw) => (
              <Table.Tr key={sw.id}>
                <Table.Td>
                  <Text fw={500}>{sw.identifier}</Text>
                </Table.Td>
                <Table.Td>{sw.location_name}</Table.Td>
                <Table.Td>
                  {sw.controls_location_name ? (
                    <Badge color="blue" variant="light">
                      {sw.controls_location_name}
                    </Badge>
                  ) : (
                    <Text size="sm" c="dimmed">
                      same location
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>{sw.breaker_label || '—'}</Table.Td>
                <Table.Td>
                  {sw.is_active ? (
                    <Badge color="green">Active</Badge>
                  ) : (
                    <Badge color="gray" variant="light">
                      Inactive
                    </Badge>
                  )}
                </Table.Td>
                <Table.Td>
                  <Group gap="xs">
                    <ActionIcon
                      variant="subtle"
                      title="Edit"
                      onClick={() =>
                        navigate(`/facilities/electrical/light-switches/${sw.id}/edit`)
                      }
                    >
                      <IconEdit size={16} />
                    </ActionIcon>
                    <ActionIcon
                      variant="subtle"
                      color="red"
                      title="Delete"
                      onClick={() => handleDeleteSwitch(sw)}
                    >
                      <IconTrash size={16} />
                    </ActionIcon>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))
          )}
        </Table.Tbody>
      </Table>
    </Paper>
  );

  const dropsTable = (
    <Paper withBorder>
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Identifier</Table.Th>
            <Table.Th>Type</Table.Th>
            <Table.Th>Location</Table.Th>
            <Table.Th>Patch panel / port</Table.Th>
            <Table.Th>MAC / IP</Table.Th>
            <Table.Th>Status</Table.Th>
            <Table.Th>Actions</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {drops.length === 0 ? (
            <Table.Tr>
              <Table.Td colSpan={7} style={{ textAlign: 'center', padding: '1.5rem' }}>
                <Text c="dimmed">{loading ? 'Loading…' : 'No network drops found'}</Text>
              </Table.Td>
            </Table.Tr>
          ) : (
            drops.map((drop) => (
              <Table.Tr key={drop.id}>
                <Table.Td>
                  <Text fw={500}>{drop.identifier}</Text>
                </Table.Td>
                <Table.Td>
                  <Badge variant="light">{dropTypeLabel(drop.drop_type)}</Badge>
                </Table.Td>
                <Table.Td>{drop.location_name}</Table.Td>
                <Table.Td>
                  {drop.patch_panel ? `${drop.patch_panel} / ${drop.patch_port || '—'}` : '—'}
                </Table.Td>
                <Table.Td>
                  <Stack gap={0}>
                    {drop.mac_address && <Text size="xs">{drop.mac_address}</Text>}
                    {drop.ip_address && <Text size="xs">{drop.ip_address}</Text>}
                    {!drop.mac_address && !drop.ip_address && <Text size="xs" c="dimmed">—</Text>}
                  </Stack>
                </Table.Td>
                <Table.Td>
                  {drop.is_active ? (
                    <Badge color="green">Active</Badge>
                  ) : (
                    <Badge color="gray" variant="light">
                      Inactive
                    </Badge>
                  )}
                </Table.Td>
                <Table.Td>
                  <Group gap="xs">
                    <ActionIcon
                      variant="subtle"
                      title="Edit"
                      onClick={() =>
                        navigate(`/facilities/electrical/network-drops/${drop.id}/edit`)
                      }
                    >
                      <IconEdit size={16} />
                    </ActionIcon>
                    <ActionIcon
                      variant="subtle"
                      color="red"
                      title="Delete"
                      onClick={() => handleDeleteDrop(drop)}
                    >
                      <IconTrash size={16} />
                    </ActionIcon>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))
          )}
        </Table.Tbody>
      </Table>
    </Paper>
  );

  const newRoute = {
    breakers: '/facilities/electrical/breakers/new',
    outlets: '/facilities/electrical/outlets/new',
    switches: '/facilities/electrical/light-switches/new',
    drops: '/facilities/electrical/network-drops/new',
  }[activeTab];

  const newLabel = {
    breakers: 'New breaker',
    outlets: 'New outlet',
    switches: 'New light switch',
    drops: 'New network drop',
  }[activeTab];

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={2}>Electrical Circuits & Network</Title>
        <Group>
          <Anchor component={Link} to="/inventory/locations">
            Locations
          </Anchor>
          <Button leftSection={<IconPlus size={16} />} onClick={() => navigate(newRoute)}>
            {newLabel}
          </Button>
        </Group>
      </Group>

      <Tabs value={activeTab} onChange={(value) => setActiveTab((value as ActiveTab) || 'breakers')}>
        <Tabs.List>
          <Tabs.Tab value="breakers" leftSection={<IconBolt size={16} />}>
            Breakers
          </Tabs.Tab>
          <Tabs.Tab value="outlets" leftSection={<IconPlugConnected size={16} />}>
            Outlets
          </Tabs.Tab>
          <Tabs.Tab value="switches" leftSection={<IconBulb size={16} />}>
            Light switches
          </Tabs.Tab>
          <Tabs.Tab value="drops" leftSection={<IconNetwork size={16} />}>
            Network drops
          </Tabs.Tab>
        </Tabs.List>
      </Tabs>

      {filterBar}

      {activeTab === 'breakers' && breakersTable}
      {activeTab === 'outlets' && outletsTable}
      {activeTab === 'switches' && switchesTable}
      {activeTab === 'drops' && dropsTable}
    </Stack>
  );
};

export default ElectricalCircuitsPage;
