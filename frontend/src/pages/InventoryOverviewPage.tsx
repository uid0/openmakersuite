/**
 * Inventory Overview (`/inventory`).
 *
 * Curated landing for the inventory workspace: every item grouped by
 * category, with a "Needs attention" rail at the top pinning anything
 * flagged `needs_reorder` or already in a reorder pipeline. Replaces
 * the temporary `<HomePage />` placeholder that the landing-surface
 * migration (PR #60) left at this route.
 *
 * For the flat searchable / sortable / bulk-action table, see
 * `<InventoryListPage />` at `/inventory/items` — this overview links
 * out to that page via the hero "Browse all items" action.
 */
import {
  Anchor,
  Badge,
  Box,
  Button,
  Card,
  Center,
  Group,
  Loader,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { IconAlertTriangle, IconBoxSeam, IconPlus } from '@tabler/icons-react';
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import WorkspacePage from '../components/landing/WorkspacePage';
import { inventoryAPI } from '../services/api';
import { Category, InventoryItem } from '../types';
import { showError } from '../utils/dialogs';

const UNCATEGORIZED = 'Uncategorized';

type CategoryGroup = {
  name: string;
  items: InventoryItem[];
};

function flagged(item: InventoryItem): boolean {
  return Boolean(item.needs_reorder) || Boolean(item.has_pending_reorder);
}

function statusBadge(item: InventoryItem) {
  if (item.has_pending_reorder) {
    return (
      <Badge color="blue" variant="light">
        Reorder pending
      </Badge>
    );
  }
  if (item.needs_reorder) {
    return (
      <Badge color="orange" variant="light">
        Low stock
      </Badge>
    );
  }
  return null;
}

const ItemCard: React.FC<{ item: InventoryItem; onClick: () => void }> = ({ item, onClick }) => (
  <Card
    withBorder
    padding="sm"
    radius="md"
    style={{ cursor: 'pointer' }}
    onClick={onClick}
    data-testid={`inventory-overview-item-${item.id}`}
  >
    <Stack gap={4}>
      <Group justify="space-between" wrap="nowrap">
        <Text fw={600} lineClamp={1}>
          {item.name}
        </Text>
        {statusBadge(item)}
      </Group>
      <Text size="xs" c="dimmed" lineClamp={1}>
        {item.sku || 'no SKU'} · {item.category_name || UNCATEGORIZED}
      </Text>
      <Text size="sm">
        Stock: <b>{item.current_stock}</b>
        {item.minimum_stock > 0 && (
          <Text component="span" c="dimmed" size="sm">
            {' '}
            / min {item.minimum_stock}
          </Text>
        )}
      </Text>
    </Stack>
  </Card>
);

const InventoryOverviewPage: React.FC = () => {
  const navigate = useNavigate();
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        setLoading(true);
        const [itemsRes, categoriesRes] = await Promise.all([
          inventoryAPI.listItems(),
          inventoryAPI.listCategories(),
        ]);
        if (cancelled) return;
        const itemList = itemsRes.data?.results ?? [];
        setItems(itemList.filter((i) => i.is_active !== false));
        setCategories(categoriesRes.data?.results ?? []);
      } catch (err) {
        if (!cancelled) {
          showError(
            'The inventory overview failed to load. Try refreshing in a moment.',
            'Could not load inventory',
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  const flaggedItems = useMemo(
    () =>
      items
        .filter(flagged)
        .sort((a, b) => {
          // pending reorders first (in flight, watch them), then low-stock by deficit
          const aPending = a.has_pending_reorder ? 1 : 0;
          const bPending = b.has_pending_reorder ? 1 : 0;
          if (aPending !== bPending) return bPending - aPending;
          const aDeficit = (a.minimum_stock || 0) - a.current_stock;
          const bDeficit = (b.minimum_stock || 0) - b.current_stock;
          return bDeficit - aDeficit;
        }),
    [items],
  );

  const categoryGroups = useMemo<CategoryGroup[]>(() => {
    const byName = new Map<string, InventoryItem[]>();
    for (const cat of categories) {
      byName.set(cat.name, []);
    }
    for (const item of items) {
      const name = item.category_name || UNCATEGORIZED;
      if (!byName.has(name)) byName.set(name, []);
      byName.get(name)!.push(item);
    }
    return Array.from(byName.entries())
      .filter(([, list]) => list.length > 0)
      .map(([name, list]) => ({
        name,
        items: list.slice().sort((a, b) => a.name.localeCompare(b.name)),
      }))
      .sort((a, b) => {
        // Uncategorized last; everything else alphabetical.
        if (a.name === UNCATEGORIZED) return 1;
        if (b.name === UNCATEGORIZED) return -1;
        return a.name.localeCompare(b.name);
      });
  }, [items, categories]);

  const goItem = (id: string) => navigate(`/inventory/items/${id}`);

  return (
    <WorkspacePage
      testId="inventory-overview-page"
      hero={{
        eyebrow: 'Inventory',
        title: 'Overview',
        description:
          'Every item, grouped by category. Items needing attention surface at the top.',
        action: (
          <Group gap="sm">
            <Button
              variant="default"
              leftSection={<IconBoxSeam size={16} />}
              onClick={() => navigate('/inventory/items')}
            >
              Browse all items
            </Button>
            <Button
              leftSection={<IconPlus size={16} />}
              onClick={() => navigate('/inventory/items/new')}
            >
              Add new item
            </Button>
          </Group>
        ),
      }}
    >
      {loading ? (
        <Center mih={240}>
          <Loader />
        </Center>
      ) : items.length === 0 ? (
        <Center mih={240}>
          <Stack align="center" gap="xs">
            <Text c="dimmed">No inventory items yet.</Text>
            <Anchor onClick={() => navigate('/inventory/items/new')} component="button">
              Add the first one
            </Anchor>
          </Stack>
        </Center>
      ) : (
        <Stack gap="xl">
          {flaggedItems.length > 0 && (
            <Box data-testid="inventory-overview-needs-attention">
              <Group gap="xs" mb="sm">
                <IconAlertTriangle size={20} color="var(--mantine-color-orange-6)" />
                <Title order={3}>Needs attention</Title>
                <Badge color="orange" variant="filled" radius="sm">
                  {flaggedItems.length}
                </Badge>
              </Group>
              <Text c="dimmed" size="sm" mb="sm">
                Items at or below their minimum, or with a reorder already
                in flight.
              </Text>
              <SimpleGrid cols={{ base: 1, sm: 2, md: 3, lg: 4 }} spacing="sm">
                {flaggedItems.map((item) => (
                  <ItemCard key={item.id} item={item} onClick={() => goItem(item.id)} />
                ))}
              </SimpleGrid>
            </Box>
          )}

          {categoryGroups.map((group) => (
            <Box key={group.name} data-testid={`inventory-overview-category-${group.name}`}>
              <Group gap="xs" mb="sm">
                <Title order={3}>{group.name}</Title>
                <Badge variant="light" radius="sm">
                  {group.items.length}
                </Badge>
              </Group>
              <SimpleGrid cols={{ base: 1, sm: 2, md: 3, lg: 4 }} spacing="sm">
                {group.items.map((item) => (
                  <ItemCard key={item.id} item={item} onClick={() => goItem(item.id)} />
                ))}
              </SimpleGrid>
            </Box>
          ))}
        </Stack>
      )}
    </WorkspacePage>
  );
};

export default InventoryOverviewPage;
