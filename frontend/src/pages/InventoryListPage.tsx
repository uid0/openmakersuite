/**
 * Inventory List Page
 * Advanced data table with filtering, sorting, search, bulk actions, and inline stock adjustment
 */
import {
    ActionIcon,
    Badge,
    Button,
    Checkbox,
    Group,
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
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { indexCardsAPI, inventoryAPI } from '../services/api';
import { Category, InventoryItem, Location } from '../types';
import { exportInventoryItemsToCSV } from '../utils/csvExport';

type SortField = 'name' | 'sku' | 'current_stock' | 'category_name' | 'location' | 'unit_cost';
type SortDirection = 'asc' | 'desc';

const InventoryListPage: React.FC = () => {
  const navigate = useNavigate();
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [selectedLocation, setSelectedLocation] = useState<string | null>(null);
  const [lowStockFilter, setLowStockFilter] = useState<string | null>(null);
  const [sortField, setSortField] = useState<SortField>('name');
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');
  const [selectedItems, setSelectedItems] = useState<Set<string>>(new Set());
  const [editingStock, setEditingStock] = useState<{ id: string; value: number } | null>(null);
  const [generatingQR, setGeneratingQR] = useState(false);
  const [generatingTestSheet, setGeneratingTestSheet] = useState(false);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      const [itemsRes, categoriesRes, locationsRes] = await Promise.all([
        inventoryAPI.listItems(),
        inventoryAPI.listCategories(),
        inventoryAPI.listLocations(),
      ]);
      setItems(itemsRes.data.results);
      setCategories(categoriesRes.data.results);
      setLocations(locationsRes.data.results);
    } catch (err) {
      console.error('Error loading data:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('asc');
    }
  };

  const sortedAndFilteredItems = useMemo(() => {
    let filtered = [...items];

    // Search filter
    if (searchTerm) {
      const term = searchTerm.toLowerCase();
      filtered = filtered.filter(
        (item) =>
          item.name.toLowerCase().includes(term) ||
          item.sku.toLowerCase().includes(term) ||
          (item.description && item.description.toLowerCase().includes(term))
      );
    }

    // Category filter
    if (selectedCategory) {
      filtered = filtered.filter((item) => item.category === Number(selectedCategory));
    }

    // Location filter
    if (selectedLocation) {
      filtered = filtered.filter((item) => {
        const location = locations.find((l) => l.id === Number(selectedLocation));
        return location && item.location === location.name;
      });
    }

    // Low stock filter
    if (lowStockFilter === 'true') {
      filtered = filtered.filter((item) => item.needs_reorder);
    } else if (lowStockFilter === 'false') {
      filtered = filtered.filter((item) => !item.needs_reorder);
    }

    // Sort
    filtered.sort((a, b) => {
      let aVal: any;
      let bVal: any;

      switch (sortField) {
        case 'name':
          aVal = a.name.toLowerCase();
          bVal = b.name.toLowerCase();
          break;
        case 'sku':
          aVal = a.sku.toLowerCase();
          bVal = b.sku.toLowerCase();
          break;
        case 'current_stock':
          aVal = a.current_stock;
          bVal = b.current_stock;
          break;
        case 'category_name':
          aVal = a.category_name || '';
          bVal = b.category_name || '';
          break;
        case 'location':
          aVal = a.location || '';
          bVal = b.location || '';
          break;
        case 'unit_cost':
          aVal = a.unit_cost ? parseFloat(a.unit_cost) : 0;
          bVal = b.unit_cost ? parseFloat(b.unit_cost) : 0;
          break;
        default:
          return 0;
      }

      if (aVal < bVal) return sortDirection === 'asc' ? -1 : 1;
      if (aVal > bVal) return sortDirection === 'asc' ? 1 : -1;
      return 0;
    });

    return filtered;
  }, [items, searchTerm, selectedCategory, selectedLocation, lowStockFilter, sortField, sortDirection, locations]);

  const handleSelectAll = () => {
    if (selectedItems.size === sortedAndFilteredItems.length) {
      setSelectedItems(new Set());
    } else {
      setSelectedItems(new Set(sortedAndFilteredItems.map((item) => item.id)));
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
      await inventoryAPI.updateStock(id, newValue);
      await loadData();
      setEditingStock(null);
    } catch (err) {
      console.error('Error updating stock:', err);
      alert('Failed to update stock. Please try again.');
    }
  };

  const handleBulkGenerateQR = async () => {
    if (selectedItems.size === 0) {
      alert('Please select items to generate QR codes for.');
      return;
    }

    try {
      setGeneratingQR(true);
      const promises = Array.from(selectedItems).map((id) => inventoryAPI.generateQR(id));
      await Promise.all(promises);
      alert(`Successfully generated QR codes for ${selectedItems.size} items.`);
      setSelectedItems(new Set());
      await loadData();
    } catch (err) {
      console.error('Error generating QR codes:', err);
      alert('Failed to generate QR codes. Please try again.');
    } finally {
      setGeneratingQR(false);
    }
  };

  const handleExportCSV = () => {
    const itemsToExport = sortedAndFilteredItems.filter((item) => selectedItems.has(item.id));
    exportInventoryItemsToCSV(itemsToExport.length > 0 ? itemsToExport : sortedAndFilteredItems);
  };

  const handleGenerateTestSheet = async () => {
    const itemsToExport = sortedAndFilteredItems.filter((item) => selectedItems.has(item.id));
    const itemIds = itemsToExport.length > 0 ? itemsToExport.map((i) => i.id) : sortedAndFilteredItems.map((i) => i.id);

    if (itemIds.length === 0) {
      alert('No items to generate test sheet for.');
      return;
    }

    try {
      setGeneratingTestSheet(true);
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
      alert('Failed to generate test sheet. Please try again.');
    } finally {
      setGeneratingTestSheet(false);
    }
  };

  const SortIcon = ({ field }: { field: SortField }) => {
    if (sortField !== field) return null;
    return sortDirection === 'asc' ? <IconSortAscending size={16} /> : <IconSortDescending size={16} />;
  };

  if (loading) {
    return (
      <Paper p="md">
        <Text>Loading inventory...</Text>
      </Paper>
    );
  }

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <div>
          <Text size="xl" fw={500}>
            Inventory Items
          </Text>
          <Text size="sm" c="dimmed">
            {sortedAndFilteredItems.length} of {items.length} items
          </Text>
        </div>
        <Button onClick={() => navigate('/inventory/items/new')}>Add New Item</Button>
      </Group>

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

      {/* Table */}
      <Paper withBorder>
        <Table.ScrollContainer minWidth={800}>
          <Table highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th style={{ width: 40 }}>
                  <Checkbox
                    checked={selectedItems.size === sortedAndFilteredItems.length && sortedAndFilteredItems.length > 0}
                    indeterminate={selectedItems.size > 0 && selectedItems.size < sortedAndFilteredItems.length}
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
                <Table.Th>
                  <Group gap="xs" style={{ cursor: 'pointer' }} onClick={() => handleSort('unit_cost')}>
                    Unit Cost
                    <SortIcon field="unit_cost" />
                  </Group>
                </Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Actions</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {sortedAndFilteredItems.map((item) => (
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
                  <Table.Td>
                    {item.unit_cost ? `$${parseFloat(item.unit_cost).toFixed(2)}` : '-'}
                  </Table.Td>
                  <Table.Td>
                    <Group gap="xs">
                      {item.needs_reorder && <Badge color="red" size="sm">Low Stock</Badge>}
                      {item.has_pending_reorder && <Badge color="blue" size="sm">Reorder Pending</Badge>}
                      {!item.is_active && <Badge color="gray" size="sm">Inactive</Badge>}
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
        {sortedAndFilteredItems.length === 0 && (
          <Paper p="xl" ta="center">
            <Text c="dimmed">No items found matching your filters.</Text>
          </Paper>
        )}
      </Paper>
    </Stack>
  );
};

export default InventoryListPage;
