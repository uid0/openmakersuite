/**
 * Inventory List Page
 * Advanced data table with server-side filtering, sorting, and search.
 * Items are loaded lazily (infinite scroll) so the list is no longer
 * capped at the first page of results.
 */
import {
    ActionIcon,
    Badge,
    Button,
    Checkbox,
    Group,
    Loader,
    NumberInput,
    Paper,
    Select,
    Stack,
    Table,
    Text,
    TextInput,
    Tooltip,
} from '@mantine/core';
import { IconDownload, IconQrcode, IconSearch, IconSortAscending, IconSortDescending } from '@tabler/icons-react';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import { indexCardsAPI, inventoryAPI } from '../services/api';
import { Category, InventoryItem, Location } from '../types';
import { exportInventoryItemsToCSV } from '../utils/csvExport';
import { showError, showInfo, showSuccess } from '../utils/dialogs';
import { vendorDataWithheld } from '../utils/vendorVisibility';

type SortField = 'name' | 'sku' | 'current_stock' | 'category_name' | 'location';
type SortDirection = 'asc' | 'desc';

// Map the table's sortable columns onto the backend's allow-listed
// `ordering` fields (see InventoryItemViewSet.get_queryset).
const SORT_FIELD_TO_BACKEND: Record<SortField, string> = {
  name: 'name',
  sku: 'sku',
  current_stock: 'current_stock',
  category_name: 'category__name',
  location: 'location__name',
};

const SEARCH_DEBOUNCE_MS = 300;

const InventoryListPage: React.FC = () => {
  const navigate = useNavigate();
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [selectedLocation, setSelectedLocation] = useState<string | null>(null);
  const [lowStockFilter, setLowStockFilter] = useState<string | null>(null);
  const [showRetired, setShowRetired] = useState<string | null>(null);
  const [sortField, setSortField] = useState<SortField>('name');
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');
  const [selectedItems, setSelectedItems] = useState<Set<string>>(new Set());
  const [editingStock, setEditingStock] = useState<{ id: string; value: number } | null>(null);
  const [generatingQR, setGeneratingQR] = useState(false);
  const [generatingTestSheet, setGeneratingTestSheet] = useState(false);

  // Pagination / infinite-scroll state.
  const [page, setPage] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  const [hasMore, setHasMore] = useState(false);

  // Load the filter dropdown data once.
  useEffect(() => {
    const loadFilters = async () => {
      try {
        const [categoriesRes, locationsRes] = await Promise.all([
          inventoryAPI.listCategories(),
          inventoryAPI.listLocations(),
        ]);
        setCategories(categoriesRes.data.results);
        setLocations(locationsRes.data.results);
      } catch (err) {
        console.error('Error loading filters:', err);
      }
    };
    loadFilters();
  }, []);

  // Debounce the free-text search so each keystroke doesn't hit the API.
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(searchTerm), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [searchTerm]);

  const buildListParams = (targetPage: number) => {
    const params: Parameters<typeof inventoryAPI.listItems>[0] = {
      page: targetPage,
      ordering: `${sortDirection === 'desc' ? '-' : ''}${SORT_FIELD_TO_BACKEND[sortField]}`,
    };
    const trimmedSearch = debouncedSearch.trim();
    if (trimmedSearch) {
      params.search = trimmedSearch;
    }
    if (selectedCategory) {
      params.category = Number(selectedCategory);
    }
    if (selectedLocation) {
      params.location = Number(selectedLocation);
    }
    if (lowStockFilter === 'true') {
      params.low_stock = true;
    } else if (lowStockFilter === 'false') {
      params.low_stock = false;
    }
    // Retired-and-empty items are hidden by default; opt in to see them.
    if (showRetired === 'true') {
      params.include_retired = true;
    }
    return params;
  };

  // (Re)load the first page whenever the filters, search, or sort change.
  useEffect(() => {
    const loadFirstPage = async () => {
      try {
        setLoading(true);
        const res = await inventoryAPI.listItems(buildListParams(1));
        setItems(res.data.results);
        setTotalCount(res.data.count ?? res.data.results.length);
        setHasMore(Boolean(res.data.next));
        setPage(1);
      } catch (err) {
        console.error('Error loading data:', err);
      } finally {
        setLoading(false);
      }
    };
    loadFirstPage();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch, selectedCategory, selectedLocation, lowStockFilter, showRetired, sortField, sortDirection]);

  const loadMore = async () => {
    if (loadingMore || loading || !hasMore) {
      return;
    }
    try {
      setLoadingMore(true);
      const nextPage = page + 1;
      const res = await inventoryAPI.listItems(buildListParams(nextPage));
      setItems((prev) => [...prev, ...res.data.results]);
      setTotalCount(res.data.count ?? totalCount);
      setHasMore(Boolean(res.data.next));
      setPage(nextPage);
    } catch (err) {
      console.error('Error loading more items:', err);
    } finally {
      setLoadingMore(false);
    }
  };

  // Keep the latest loadMore reachable from the (stable) observer callback,
  // so the observer never has to be torn down just to capture fresh state.
  const loadMoreRef = useRef(loadMore);
  loadMoreRef.current = loadMore;

  // Callback ref: attach an IntersectionObserver as soon as the sentinel
  // mounts (it isn't present during the initial loading screen) and tear it
  // down when the sentinel unmounts.
  const observerRef = useRef<IntersectionObserver | null>(null);
  const sentinelRef = useCallback((node: HTMLDivElement | null) => {
    if (observerRef.current) {
      observerRef.current.disconnect();
      observerRef.current = null;
    }
    if (node && typeof IntersectionObserver !== 'undefined') {
      observerRef.current = new IntersectionObserver(
        (entries) => {
          if (entries[0]?.isIntersecting) {
            loadMoreRef.current();
          }
        },
        { rootMargin: '200px' }
      );
      observerRef.current.observe(node);
    }
  }, []);

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('asc');
    }
  };

  // Fetch every item matching the current filters (used by the export
  // actions when nothing is explicitly selected).
  const fetchAllMatchingItems = async (): Promise<InventoryItem[]> => {
    const all: InventoryItem[] = [];
    let targetPage = 1;
    for (;;) {
      const res = await inventoryAPI.listItems(buildListParams(targetPage));
      all.push(...res.data.results);
      if (!res.data.next) {
        break;
      }
      targetPage += 1;
    }
    return all;
  };

  const handleSelectAll = () => {
    if (selectedItems.size === items.length) {
      setSelectedItems(new Set());
    } else {
      setSelectedItems(new Set(items.map((item) => item.id)));
    }
  };

  const handleSelectItem = (id: string) => {
    const newSelected = new Set(selectedItems);
    if (newSelected.has(id)) {
      newSelected.delete(id);
    } else {
      newSelected.add(id);
    }
    setSelectedItems(newSelected);
  };

  const handleStockEdit = (item: InventoryItem) => {
    setEditingStock({ id: item.id, value: item.current_stock });
  };

  const handleStockSave = async (id: string, newValue: number) => {
    try {
      const res = await inventoryAPI.updateStock(id, newValue);
      // Patch the row in place so we don't disturb scroll position or
      // the already-loaded pages.
      setItems((prev) => prev.map((item) => (item.id === id ? { ...item, ...res.data } : item)));
      setEditingStock(null);
    } catch (err) {
      console.error('Error updating stock:', err);
      showError('Failed to update stock. Please try again.');
    }
  };

  const handleBulkGenerateQR = async () => {
    if (selectedItems.size === 0) {
      showInfo('Please select items to generate QR codes for.');
      return;
    }

    try {
      setGeneratingQR(true);
      const promises = Array.from(selectedItems).map((id) => inventoryAPI.generateQR(id));
      await Promise.all(promises);
      showSuccess(`Successfully generated QR codes for ${selectedItems.size} items.`);
      setSelectedItems(new Set());
    } catch (err) {
      console.error('Error generating QR codes:', err);
      showError('Failed to generate QR codes. Please try again.');
    } finally {
      setGeneratingQR(false);
    }
  };

  const handleExportCSV = async () => {
    try {
      const selected = items.filter((item) => selectedItems.has(item.id));
      const itemsToExport = selected.length > 0 ? selected : await fetchAllMatchingItems();
      // The audience comes off THE ROWS BEING EXPORTED, not off auth state, so
      // the file and the table it was exported from cannot disagree — the table
      // reads the same marker. `isAuthenticated()` is only "is there a token in
      // localStorage", and the response interceptor clears that token when a
      // refresh fails on any background call; an operator still looking at real
      // prices would then have exported a file with those columns missing.
      exportInventoryItemsToCSV(
        itemsToExport,
        itemsToExport.some(vendorDataWithheld) ? 'anonymous' : 'operator'
      );
    } catch (err) {
      console.error('Error exporting items:', err);
      showError('Failed to export items. Please try again.');
    }
  };

  const handleGenerateTestSheet = async () => {
    try {
      setGeneratingTestSheet(true);
      const selected = items.filter((item) => selectedItems.has(item.id));
      const itemsToExport = selected.length > 0 ? selected : await fetchAllMatchingItems();
      const itemIds = itemsToExport.map((i) => i.id);

      if (itemIds.length === 0) {
        showInfo('No items to generate test sheet for.');
        return;
      }

      const response = await indexCardsAPI.generateTestSheet(itemIds);
      const blob = new Blob([response.data], { type: 'application/pdf' });
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'test_sheet.pdf';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Error generating test sheet:', err);
      showError('Failed to generate test sheet. Please try again.');
    } finally {
      setGeneratingTestSheet(false);
    }
  };

  const SortIcon = ({ field }: { field: SortField }) => {
    if (sortField !== field) return null;
    return sortDirection === 'asc' ? <IconSortAscending size={16} /> : <IconSortDescending size={16} />;
  };

  // `some` rather than reading row 0: every row of one response is gated the
  // same way, so this agrees with row 0 whenever there is one, and stays false
  // for an empty list — where a dropped column would say nothing to nobody.
  const vendorWithheld = items.some(vendorDataWithheld);

  if (loading && items.length === 0) {
    return (
      <WorkspacePage
        hero={{ eyebrow: 'Inventory', title: 'Items', description: 'Loading…' }}
        testId="inventory-list-page"
      >
        <Paper p="md" withBorder>
          <Text c="dimmed">Loading inventory…</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  return (
    <WorkspacePage
      testId="inventory-list-page"
      hero={{
        eyebrow: 'Inventory',
        title: 'Items',
        description: `Showing ${items.length} of ${totalCount} item${totalCount === 1 ? '' : 's'}.`,
        action: (
          <Button onClick={() => navigate('/inventory/items/new')}>Add new item</Button>
        ),
      }}
    >
      {/* Filters and Search */}
      <Paper p="md" withBorder>
        <Stack gap="md">
          <Group grow>
            <TextInput
              placeholder="Search by name, SKU, or description..."
              leftSection={<IconSearch size={16} />}
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
            <Select
              placeholder="All Categories"
              data={[
                { value: '', label: 'All Categories' },
                ...categories.map((c) => ({ value: String(c.id), label: c.name })),
              ]}
              value={selectedCategory || ''}
              onChange={(value) => setSelectedCategory(value || null)}
              clearable
            />
            <Select
              placeholder="All Locations"
              data={[
                { value: '', label: 'All Locations' },
                ...locations.map((l) => ({ value: String(l.id), label: l.name })),
              ]}
              value={selectedLocation || ''}
              onChange={(value) => setSelectedLocation(value || null)}
              clearable
            />
            <Select
              placeholder="Stock Status"
              data={[
                { value: '', label: 'All Items' },
                { value: 'true', label: 'Low Stock' },
                { value: 'false', label: 'In Stock' },
              ]}
              value={lowStockFilter || ''}
              onChange={(value) => setLowStockFilter(value || null)}
              clearable
            />
            <Select
              placeholder="Retired"
              data={[
                { value: '', label: 'Hide Retired' },
                { value: 'true', label: 'Show Retired' },
              ]}
              value={showRetired || ''}
              onChange={(value) => setShowRetired(value || null)}
              clearable
            />
          </Group>

          {/* Bulk Actions */}
          {selectedItems.size > 0 && (
            <Group>
              <Text size="sm" c="dimmed">
                {selectedItems.size} item(s) selected
              </Text>
              <Button
                size="xs"
                leftSection={<IconQrcode size={16} />}
                onClick={handleBulkGenerateQR}
                loading={generatingQR}
              >
                Generate QR Codes
              </Button>
              <Button size="xs" leftSection={<IconDownload size={16} />} onClick={handleExportCSV}>
                Export CSV
              </Button>
              <Button size="xs" onClick={handleGenerateTestSheet} loading={generatingTestSheet}>
                Print Index Cards
              </Button>
              <Button size="xs" variant="subtle" onClick={() => setSelectedItems(new Set())}>
                Clear Selection
              </Button>
            </Group>
          )}
        </Stack>
      </Paper>

      {/* The Unit Cost column is DROPPED, not blanked, for a caller the server
          withheld the vendor block from (op-anonymous-read-posture). This route
          is not behind RequireAuth and its list endpoint is AllowAny, so a
          logged-out visitor gets rows with no `unit_cost` key at all — and '-'
          in this column already means "no price recorded", a claim about the
          ITEM where the truth is a fact about the READER. `csvExport` drops the
          same column for the same reason ("an absent COLUMN cannot be misread
          as an empty VALUE"), and the screen and its own export have to agree.

          Read off the payload's marker rather than off auth state: the server
          has already decided, and a second client-side derivation of the same
          answer is how the two come to disagree. */}
      {/* Table */}
      <Paper withBorder>
        <Table.ScrollContainer minWidth={800}>
          <Table highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th style={{ width: 40 }}>
                  <Checkbox
                    checked={selectedItems.size === items.length && items.length > 0}
                    indeterminate={selectedItems.size > 0 && selectedItems.size < items.length}
                    onChange={handleSelectAll}
                  />
                </Table.Th>
                <Table.Th>
                  <Group gap="xs" style={{ cursor: 'pointer' }} onClick={() => handleSort('name')}>
                    Name
                    <SortIcon field="name" />
                  </Group>
                </Table.Th>
                <Table.Th>
                  <Group gap="xs" style={{ cursor: 'pointer' }} onClick={() => handleSort('sku')}>
                    SKU
                    <SortIcon field="sku" />
                  </Group>
                </Table.Th>
                <Table.Th>
                  <Group gap="xs" style={{ cursor: 'pointer' }} onClick={() => handleSort('category_name')}>
                    Category
                    <SortIcon field="category_name" />
                  </Group>
                </Table.Th>
                <Table.Th>
                  <Group gap="xs" style={{ cursor: 'pointer' }} onClick={() => handleSort('location')}>
                    Location
                    <SortIcon field="location" />
                  </Group>
                </Table.Th>
                <Table.Th>
                  <Group gap="xs" style={{ cursor: 'pointer' }} onClick={() => handleSort('current_stock')}>
                    Stock
                    <SortIcon field="current_stock" />
                  </Group>
                </Table.Th>
                {!vendorWithheld && <Table.Th>Unit Cost</Table.Th>}
                <Table.Th>Status</Table.Th>
                <Table.Th>Actions</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {items.map((item) => (
                <Table.Tr
                  key={item.id}
                  style={{
                    backgroundColor: selectedItems.has(item.id) ? '#e7f5ff' : undefined,
                    cursor: 'pointer',
                  }}
                  onClick={() => navigate(`/inventory/items/${item.id}`)}
                >
                  <Table.Td onClick={(e) => e.stopPropagation()}>
                    <Checkbox
                      checked={selectedItems.has(item.id)}
                      onChange={() => handleSelectItem(item.id)}
                    />
                  </Table.Td>
                  <Table.Td>
                    <Text fw={500}>{item.name}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm" c="dimmed">
                      {item.sku}
                    </Text>
                  </Table.Td>
                  <Table.Td>{item.category_name || '-'}</Table.Td>
                  <Table.Td>{item.location || '-'}</Table.Td>
                  <Table.Td onClick={(e) => e.stopPropagation()}>
                    {editingStock?.id === item.id ? (
                      <Group gap="xs">
                        <NumberInput
                          value={editingStock.value}
                          onChange={(value) =>
                            setEditingStock({ id: item.id, value: typeof value === 'number' ? value : 0 })
                          }
                          min={0}
                          size="xs"
                          style={{ width: 80 }}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                              handleStockSave(item.id, editingStock.value);
                            } else if (e.key === 'Escape') {
                              setEditingStock(null);
                            }
                          }}
                        />
                        <Button
                          size="xs"
                          onClick={() => handleStockSave(item.id, editingStock.value)}
                        >
                          Save
                        </Button>
                        <Button size="xs" variant="subtle" onClick={() => setEditingStock(null)}>
                          Cancel
                        </Button>
                      </Group>
                    ) : (
                      <Tooltip label="Click to edit">
                        <Text
                          style={{ cursor: 'pointer' }}
                          onClick={() => handleStockEdit(item)}
                          c={item.needs_reorder ? 'red' : undefined}
                          fw={item.needs_reorder ? 600 : undefined}
                        >
                          {item.current_stock}
                        </Text>
                      </Tooltip>
                    )}
                  </Table.Td>
                  {!vendorWithheld && (
                    <Table.Td>
                      {item.unit_cost != null ? `$${item.unit_cost.toFixed(2)}` : '-'}
                    </Table.Td>
                  )}
                  <Table.Td>
                    <Group gap="xs">
                      {item.needs_reorder && <Badge color="red" size="sm">Low Stock</Badge>}
                      {item.has_pending_reorder && <Badge color="blue" size="sm">Reorder Pending</Badge>}
                      {!item.is_active && <Badge color="gray" size="sm">Inactive</Badge>}
                      {item.is_retired && <Badge color="orange" size="sm">Retired</Badge>}
                      {item.is_serialized && <Badge color="grape" size="sm">Serialized</Badge>}
                    </Group>
                  </Table.Td>
                  <Table.Td onClick={(e) => e.stopPropagation()}>
                    <Group gap="xs">
                      <ActionIcon
                        variant="subtle"
                        onClick={() => navigate(`/inventory/items/${item.id}/edit`)}
                      >
                        Edit
                      </ActionIcon>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        {items.length === 0 ? (
          <Paper p="xl" ta="center">
            <Text c="dimmed">No items found matching your filters.</Text>
          </Paper>
        ) : (
          // Infinite-scroll sentinel: observed to fetch the next page.
          <div ref={sentinelRef} data-testid="inventory-scroll-sentinel">
            {loadingMore && (
              <Group justify="center" p="md">
                <Loader size="sm" />
                <Text size="sm" c="dimmed">
                  Loading more…
                </Text>
              </Group>
            )}
            {!hasMore && (
              <Text ta="center" c="dimmed" size="sm" p="md">
                All {totalCount} item{totalCount === 1 ? '' : 's'} loaded.
              </Text>
            )}
          </div>
        )}
      </Paper>
    </WorkspacePage>
  );
};

export default InventoryListPage;
