/**
 * KitListPage — browse kit SKUs (op-8n0).
 *
 * A kit is a purchasable bundle that decomposes: buying one is a single
 * purchase-order line, and receiving it credits the components rather than the
 * kit. Kits are hidden from `/inventory/items` for that reason, so this is
 * where they are managed.
 */
import { Alert, Anchor, Badge, Button, Group, Loader, Stack, Table, Text, TextInput } from '@mantine/core';
import { IconPlus, IconSearch } from '@tabler/icons-react';
import React, { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import WorkspacePage from '../components/landing/WorkspacePage';
import { kitAPI } from '../services/api';
import { Kit } from '../types';
import { supplierChoiceNote, supplierChoiceSummary } from '../utils/supplierChoice';

const KitListPage: React.FC = () => {
  const navigate = useNavigate();
  const [kits, setKits] = useState<Kit[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');

  const load = useCallback(async (term: string) => {
    try {
      const res = await kitAPI.listKits({ search: term || undefined, page_size: 100 });
      const data = res?.data;
      setKits(Array.isArray(data) ? data : (data?.results ?? []));
      setError(null);
    } catch {
      setError('Could not load kits.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const handle = setTimeout(() => load(search), 250);
    return () => clearTimeout(handle);
  }, [search, load]);

  return (
    <WorkspacePage
      testId="kit-list-page"
      hero={{
        eyebrow: 'Inventory',
        title: 'Kits',
        description:
          'Bundles you buy as one SKU. Ordering a kit is a single purchase-order line; receiving it adds stock to each component inside.',
        action: (
          <Button
            leftSection={<IconPlus size={16} />}
            onClick={() => navigate('/inventory/kits/new')}
            data-testid="kit-list-new"
          >
            New kit
          </Button>
        ),
      }}
    >
      <Stack gap="md">
        <TextInput
          leftSection={<IconSearch size={16} />}
          placeholder="Search kits…"
          value={search}
          onChange={(event) => setSearch(event.currentTarget.value)}
          data-testid="kit-list-search"
        />

        {error && (
          <Alert color="red" data-testid="kit-list-error">
            {error}
          </Alert>
        )}

        {loading ? (
          <Group justify="center" py="xl">
            <Loader data-testid="kit-list-loading" />
          </Group>
        ) : kits.length === 0 ? (
          <Text c="dimmed" data-testid="kit-list-empty">
            No kits yet. Create one to buy a bundle as a single line.
          </Text>
        ) : (
          <Table striped highlightOnHover withTableBorder data-testid="kit-list-table">
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Kit</Table.Th>
                <Table.Th scope="col">Supplier SKU</Table.Th>
                <Table.Th scope="col">From</Table.Th>
                <Table.Th scope="col">Unit cost</Table.Th>
                <Table.Th scope="col">Components</Table.Th>
                <Table.Th scope="col">Status</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {kits.map((kit) => (
                <Table.Tr key={kit.id} data-testid={`kit-row-${kit.id}`}>
                  <Table.Td>
                    <Anchor component={Link} to={`/inventory/kits/${kit.id}`}>
                      {kit.name}
                    </Anchor>
                  </Table.Td>
                  {/* A SKU gets pasted into a vendor's order form, so an
                      UNATTRIBUTED one is actionable-wrong: this column is the
                      flat legacy `supplier_sku`, which is one particular
                      vendor's part number for the kit, and the table named no
                      vendor at all. Paste it at the wrong supplier and you have
                      ordered the wrong thing. The SKU is unchanged; what is new
                      is saying whose it is (op-3xsp). */}
                  <Table.Td data-testid={`kit-supplier-sku-${kit.id}`}>
                    {kit.supplier_sku || '—'}
                  </Table.Td>
                  <Table.Td data-testid={`kit-supplier-${kit.id}`}>
                    {kit.supplier_sku
                      ? (supplierChoiceSummary(kit.supplier_choice) ?? (
                          <Text c="dimmed" size="sm">
                            {supplierChoiceNote(kit.supplier_choice)}
                          </Text>
                        ))
                      : '—'}
                  </Table.Td>
                  <Table.Td>
                    {kit.unit_cost != null ? `$${kit.unit_cost.toFixed(2)}` : '—'}
                  </Table.Td>
                  <Table.Td>{kit.component_count ?? kit.components?.length ?? 0}</Table.Td>
                  <Table.Td>
                    <Badge color={kit.is_active ? 'green' : 'gray'} variant="light">
                      {kit.is_active ? 'Active' : 'Inactive'}
                    </Badge>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Stack>
    </WorkspacePage>
  );
};

export default KitListPage;
