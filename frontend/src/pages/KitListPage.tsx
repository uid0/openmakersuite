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
import { vendorColumnsDropped } from '../utils/vendorVisibility';

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

  // `/inventory/kits` carries no `RequireAuth` and `KitViewSet` serves reads to
  // anyone, so a logged-out visitor lands here and `KitSerializer` — which
  // inherits `InventoryItemSerializer.VENDOR_ONLY_FIELDS` — withholds the SKU,
  // the choice AND the unit cost. All three columns DROP together, the shape
  // its sibling `InventoryListPage` already uses: an absent column cannot be
  // misread as an empty value, whereas '—' in any of them claims the KIT has no
  // SKU, no supplier and no price on file (op-anonymous-read-posture, op-3xsp).
  //
  // Read off the ROWS, not off `isAuthenticated()`, which this used to freeze
  // at mount: the response interceptor clears the token when a refresh fails on
  // any background call, so a later refetch returned a withheld payload while
  // the flag still said operator — and the SKU cell went to '—' while the From
  // cell rendered diagnostic copy about the response.
  const vendorWithheld = vendorColumnsDropped(kits);

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
                {!vendorWithheld && <Table.Th scope="col">Supplier SKU</Table.Th>}
                {!vendorWithheld && <Table.Th scope="col">From</Table.Th>}
                {!vendorWithheld && <Table.Th scope="col">Unit cost</Table.Th>}
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
                  {!vendorWithheld && (
                    <Table.Td data-testid={`kit-supplier-sku-${kit.id}`}>
                      {kit.supplier_sku || '—'}
                    </Table.Td>
                  )}
                  {!vendorWithheld && (
                    <Table.Td data-testid={`kit-supplier-${kit.id}`}>
                      {kit.supplier_sku
                        ? (supplierChoiceSummary(kit.supplier_choice) ?? (
                            <Text c="dimmed" size="sm">
                              {supplierChoiceNote(kit.supplier_choice)}
                            </Text>
                          ))
                        : '—'}
                    </Table.Td>
                  )}
                  {!vendorWithheld && (
                    <Table.Td>
                      {kit.unit_cost != null ? `$${kit.unit_cost.toFixed(2)}` : '—'}
                    </Table.Td>
                  )}
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
