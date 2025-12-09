/**
 * Purchase Order Creation Page
 * Create new purchase orders with supplier selection, reorder queue integration, and line item management
 */
import { Button, Group, Paper, Select, Stack, Table, Text, Textarea, TextInput, Title } from '@mantine/core';
import { DatePickerInput } from '@mantine/dates';
import { IconPlus, IconTrash } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { inventoryAPI, purchaseOrderAPI, reorderAPI } from '../services/api';
import '../styles/PurchaseOrderCreatePage.css';
import { CreatePurchaseOrder, ItemSupplier, ReorderRequest, Supplier } from '../types';

interface LineItem {
  id: string;
  item_supplier_id?: number;
  asset_id?: string;
  item_name: string;
  item_sku: string;
  supplier_sku: string;
  quantity: number;
  unit_cost: number;
  expected_shipment_date?: string;
  notes: string;
  from_reorder_request?: number; // ReorderRequest ID if added from queue
}

interface ReorderRequestGroup {
  supplier: string;
  supplier_type: string;
  requests: ReorderRequest[];
  total_estimated_cost: number;
  item_count: number;
}

const PurchaseOrderCreatePage: React.FC = () => {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Data
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [selectedSupplier, setSelectedSupplier] = useState<number | null>(null);
  const [reorderGroups, setReorderGroups] = useState<ReorderRequestGroup[]>([]);
  const [itemSuppliers, setItemSuppliers] = useState<ItemSupplier[]>([]);
  const [lineItems, setLineItems] = useState<LineItem[]>([]);

  // Form state
  const [expectedDeliveryDate, setExpectedDeliveryDate] = useState<Date | null>(null);
  const [notes, setNotes] = useState('');

  // UI state
  const [showReorderQueue, setShowReorderQueue] = useState(false);
  const [showManualAdd, setShowManualAdd] = useState(false);
  const [selectedItemSupplier, setSelectedItemSupplier] = useState<string>('');
  const [manualQuantity, setManualQuantity] = useState<number>(1);
  const [manualUnitCost, setManualUnitCost] = useState<string>('');

  useEffect(() => {
    loadInitialData();
  }, []);

  useEffect(() => {
    if (selectedSupplier) {
      loadReorderQueue();
      loadItemSuppliers();
    } else {
      setReorderGroups([]);
      setItemSuppliers([]);
    }
  }, [selectedSupplier]);

  const loadInitialData = async () => {
    try {
      setLoading(true);
      const suppliersRes = await inventoryAPI.listSuppliers();
      setSuppliers(suppliersRes.data.results || []);
    } catch (err: any) {
      console.error('Error loading suppliers:', err);
      setError(err.response?.data?.error || 'Failed to load suppliers');
    } finally {
      setLoading(false);
    }
  };

  const loadReorderQueue = async () => {
    try {
      const response = await reorderAPI.getBySupplier();
      // Transform the response to match our interface
      const groups: ReorderRequestGroup[] = response.data.map((group: any) => ({
        supplier: group.supplier,
        supplier_type: group.supplier_type,
        requests: group.requests || [],
        total_estimated_cost: group.total_estimated_cost || 0,
        item_count: group.item_count || 0,
      }));
      setReorderGroups(groups);
    } catch (err: any) {
      console.error('Error loading reorder queue:', err);
      // Don't show error, just log it - reorder queue is optional
    }
  };

  const loadItemSuppliers = async () => {
    if (!selectedSupplier) return;
    try {
      // Get all items and their suppliers for the selected supplier
      const itemsRes = await inventoryAPI.listItems();
      const allItems = itemsRes.data.results || [];
      
      // Filter items that have the selected supplier
      const relevantItemSuppliers: ItemSupplier[] = [];
      for (const item of allItems) {
        if (item.item_suppliers) {
          for (const itemSupplier of item.item_suppliers) {
            if (itemSupplier.supplier === selectedSupplier) {
              relevantItemSuppliers.push(itemSupplier);
            }
          }
        }
      }
      setItemSuppliers(relevantItemSuppliers);
    } catch (err: any) {
      console.error('Error loading item suppliers:', err);
    }
  };

  const addItemFromReorderQueue = (reorderRequest: ReorderRequest) => {
    if (!reorderRequest.item_details) return;

    const item = reorderRequest.item_details;
    const itemSupplier = item.item_suppliers?.find(
      (is: ItemSupplier) => is.supplier === selectedSupplier
    );

    if (!itemSupplier) {
      alert('This item does not have a supplier relationship with the selected supplier');
      return;
    }

    const newItem: LineItem = {
      id: `temp-${Date.now()}-${Math.random()}`,
      item_supplier_id: itemSupplier.id,
      item_name: item.name,
      item_sku: item.sku,
      supplier_sku: itemSupplier.supplier_sku,
      quantity: reorderRequest.quantity,
      unit_cost: parseFloat(itemSupplier.unit_cost || '0'),
      notes: reorderRequest.request_notes || '',
      from_reorder_request: reorderRequest.id,
    };

    setLineItems([...lineItems, newItem]);
  };

  const addManualItem = () => {
    if (!selectedItemSupplier) {
      alert('Please select an item');
      return;
    }

    const itemSupplier = itemSuppliers.find(
      (is) => is.id.toString() === selectedItemSupplier
    );

    if (!itemSupplier) {
      alert('Selected item supplier not found');
      return;
    }

    const unitCost = manualUnitCost ? parseFloat(manualUnitCost) : parseFloat(itemSupplier.unit_cost || '0');

    const newItem: LineItem = {
      id: `temp-${Date.now()}-${Math.random()}`,
      item_supplier_id: itemSupplier.id,
      item_name: itemSupplier.item_name,
      item_sku: '', // Will be filled from item details if needed
      supplier_sku: itemSupplier.supplier_sku,
      quantity: manualQuantity,
      unit_cost: unitCost,
      notes: '',
    };

    setLineItems([...lineItems, newItem]);
    setSelectedItemSupplier('');
    setManualQuantity(1);
    setManualUnitCost('');
    setShowManualAdd(false);
  };

  const removeLineItem = (itemId: string) => {
    setLineItems(lineItems.filter((item) => item.id !== itemId));
  };

  const updateLineItem = (itemId: string, updates: Partial<LineItem>) => {
    setLineItems(
      lineItems.map((item) => (item.id === itemId ? { ...item, ...updates } : item))
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!selectedSupplier) {
      setError('Please select a supplier');
      return;
    }

    if (lineItems.length === 0) {
      setError('Please add at least one line item');
      return;
    }

    setError(null);
    setSaving(true);

    try {
      const orderData: CreatePurchaseOrder = {
        supplier: selectedSupplier,
        expected_delivery_date: expectedDeliveryDate
          ? expectedDeliveryDate.toISOString().split('T')[0]
          : undefined,
        notes: notes.trim() || undefined,
        items: lineItems.map((item) => ({
          item_supplier_id: item.item_supplier_id,
          asset_id: item.asset_id,
          quantity: item.quantity,
          unit_cost: item.unit_cost > 0 ? item.unit_cost : undefined,
          expected_shipment_date: item.expected_shipment_date,
          notes: item.notes || undefined,
        })),
      };

      const response = await purchaseOrderAPI.createOrder(orderData);
      navigate(`/purchasing/orders/${response.data.id}`);
    } catch (err: any) {
      console.error('Error creating purchase order:', err);
      setError(err.response?.data?.error || err.response?.data?.detail || 'Failed to create purchase order');
    } finally {
      setSaving(false);
    }
  };

  const calculateTotal = () => {
    return lineItems.reduce((sum, item) => sum + item.quantity * item.unit_cost, 0);
  };

  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 2,
    }).format(value);
  };

  if (loading) {
    return (
      <div className="purchase-order-create-page">
        <div className="loading">Loading...</div>
      </div>
    );
  }

  return (
    <div className="purchase-order-create-page">
      <Title order={1} mb="lg">Create Purchase Order</Title>

      {error && (
        <div className="error-message" style={{ marginBottom: '1rem', padding: '1rem', backgroundColor: '#fee', border: '1px solid #fcc', borderRadius: '4px' }}>
          {error}
        </div>
      )}

      <form onSubmit={handleSubmit}>
        <Stack gap="md">
          <Paper p="md" withBorder>
            <Title order={3} mb="md">Order Details</Title>
            <Stack gap="md">
              <Select
                label="Supplier"
                required
                data={suppliers.map((s) => ({ value: s.id.toString(), label: s.name }))}
                searchable
                value={selectedSupplier?.toString() || ''}
                onChange={(value) => {
                  setSelectedSupplier(value ? parseInt(value) : null);
                  setLineItems([]); // Clear line items when supplier changes
                }}
              />

              <DatePickerInput
                label="Expected Delivery Date"
                value={expectedDeliveryDate}
                onChange={setExpectedDeliveryDate}
                valueFormat="YYYY-MM-DD"
              />

              <Textarea
                label="Notes"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                rows={3}
              />
            </Stack>
          </Paper>

          {selectedSupplier && (
            <Paper p="md" withBorder>
              <Group justify="space-between" mb="md">
                <Title order={3}>Line Items</Title>
                <Group>
                  <Button
                    variant="light"
                    onClick={() => setShowReorderQueue(!showReorderQueue)}
                    disabled={!reorderGroups.length}
                  >
                    Add from Reorder Queue
                  </Button>
                  <Button
                    variant="light"
                    onClick={() => setShowManualAdd(!showManualAdd)}
                    leftSection={<IconPlus size={16} />}
                  >
                    Add Item Manually
                  </Button>
                </Group>
              </Group>

              {showReorderQueue && (
                <Paper p="md" withBorder mb="md" style={{ backgroundColor: '#f9f9f9' }}>
                  <Title order={4} mb="md">Reorder Queue Items</Title>
                  {reorderGroups.length === 0 ? (
                    <Text c="dimmed">No pending reorder requests for this supplier</Text>
                  ) : (
                    reorderGroups.map((group) => (
                      <div key={group.supplier} style={{ marginBottom: '1rem' }}>
                        <Text fw={600} mb="xs">{group.supplier}</Text>
                        <Table>
                          <thead>
                            <tr>
                              <th>Item</th>
                              <th>SKU</th>
                              <th>Requested Qty</th>
                              <th>Priority</th>
                              <th>Action</th>
                            </tr>
                          </thead>
                          <tbody>
                            {group.requests.map((req) => (
                              <tr key={req.id}>
                                <td>{req.item_details?.name || 'Unknown'}</td>
                                <td>{req.item_details?.sku || '—'}</td>
                                <td>{req.quantity}</td>
                                <td>{req.priority}</td>
                                <td>
                                  <Button
                                    size="xs"
                                    onClick={() => addItemFromReorderQueue(req)}
                                    disabled={lineItems.some(
                                      (li) => li.from_reorder_request === req.id
                                    )}
                                  >
                                    Add
                                  </Button>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </Table>
                      </div>
                    ))
                  )}
                </Paper>
              )}

              {showManualAdd && (
                <Paper p="md" withBorder mb="md" style={{ backgroundColor: '#f9f9f9' }}>
                  <Title order={4} mb="md">Add Item Manually</Title>
                  <Group align="flex-end">
                    <Select
                      label="Item"
                      required
                      data={itemSuppliers.map((is) => ({
                        value: is.id.toString(),
                        label: `${is.item_name} (${is.supplier_sku})`,
                      }))}
                      searchable
                      value={selectedItemSupplier}
                      onChange={(value) => {
                        setSelectedItemSupplier(value || '');
                        const itemSupplier = itemSuppliers.find(
                          (is) => is.id.toString() === value
                        );
                        if (itemSupplier && itemSupplier.unit_cost) {
                          setManualUnitCost(itemSupplier.unit_cost);
                        }
                      }}
                      style={{ flex: 1 }}
                    />
                    <TextInput
                      label="Quantity"
                      type="number"
                      min={1}
                      value={manualQuantity}
                      onChange={(e) => setManualQuantity(parseInt(e.target.value) || 1)}
                      style={{ width: '120px' }}
                    />
                    <TextInput
                      label="Unit Cost"
                      type="number"
                      step="0.01"
                      min={0}
                      value={manualUnitCost}
                      onChange={(e) => setManualUnitCost(e.target.value)}
                      style={{ width: '120px' }}
                    />
                    <Button onClick={addManualItem}>Add</Button>
                  </Group>
                </Paper>
              )}

              {lineItems.length === 0 ? (
                <Text c="dimmed" ta="center" py="xl">
                  No line items added yet. Add items from the reorder queue or manually.
                </Text>
              ) : (
                <>
                  <Table>
                    <thead>
                      <tr>
                        <th>Item</th>
                        <th>Supplier SKU</th>
                        <th>Quantity</th>
                        <th>Unit Cost</th>
                        <th>Line Total</th>
                        <th>Expected Shipment</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {lineItems.map((item) => (
                        <tr key={item.id}>
                          <td>{item.item_name}</td>
                          <td>{item.supplier_sku}</td>
                          <td>
                            <TextInput
                              type="number"
                              min={1}
                              value={item.quantity}
                              onChange={(e) =>
                                updateLineItem(item.id, {
                                  quantity: parseInt(e.target.value) || 1,
                                })
                              }
                              style={{ width: '80px' }}
                            />
                          </td>
                          <td>
                            <TextInput
                              type="number"
                              step="0.01"
                              min={0}
                              value={item.unit_cost}
                              onChange={(e) =>
                                updateLineItem(item.id, {
                                  unit_cost: parseFloat(e.target.value) || 0,
                                })
                              }
                              style={{ width: '100px' }}
                            />
                          </td>
                          <td>{formatCurrency(item.quantity * item.unit_cost)}</td>
                          <td>
                            <input
                              type="date"
                              value={item.expected_shipment_date || ''}
                              onChange={(e) =>
                                updateLineItem(item.id, {
                                  expected_shipment_date: e.target.value || undefined,
                                })
                              }
                              style={{ padding: '4px', border: '1px solid #ccc', borderRadius: '4px' }}
                            />
                          </td>
                          <td>
                            <Button
                              variant="subtle"
                              color="red"
                              size="xs"
                              onClick={() => removeLineItem(item.id)}
                              leftSection={<IconTrash size={14} />}
                            >
                              Remove
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                  <Group justify="flex-end" mt="md">
                    <Text size="lg" fw={600}>
                      Total: {formatCurrency(calculateTotal())}
                    </Text>
                  </Group>
                </>
              )}
            </Paper>
          )}

          <Group justify="flex-end" mt="md">
            <Button variant="subtle" onClick={() => navigate('/purchasing/orders')}>
              Cancel
            </Button>
            <Button type="submit" loading={saving} disabled={!selectedSupplier || lineItems.length === 0}>
              Create Purchase Order
            </Button>
          </Group>
        </Stack>
      </form>
    </div>
  );
};

export default PurchaseOrderCreatePage;
