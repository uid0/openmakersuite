/**
 * Purchase Order Receiving Page
 * Receive purchase orders with barcode scanning, partial receipts, condition notes, and inventory updates
 */
import { Alert, Button, Checkbox, Group, Paper, Stack, Table, Text, TextInput, Title } from '@mantine/core';
import { IconCheck, IconX } from '@tabler/icons-react';
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { purchaseOrderAPI } from '../services/api';
import '../styles/PurchaseOrderReceivingPage.css';
import { OrderDelivery, PurchaseOrder, PurchaseOrderLineItem } from '../types';

interface ReceiptItem {
  po_item_id: string;
  item_name: string;
  sku: string;
  quantity_ordered: number;
  quantity_received: number;
  quantity_pending: number;
  quantity_to_receive: number;
  is_damaged: boolean;
  is_expired: boolean;
  condition_notes: string;
  scanned_upc: string;
}

const PurchaseOrderReceivingPage: React.FC = () => {
  const { orderId } = useParams<{ orderId: string }>();
  const navigate = useNavigate();

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [order, setOrder] = useState<PurchaseOrder | null>(null);
  const [deliveries, setDeliveries] = useState<OrderDelivery[]>([]);
  const [receiptItems, setReceiptItems] = useState<ReceiptItem[]>([]);

  // Receipt form state
  const [upcInput, setUpcInput] = useState('');
  const [quantityInput, setQuantityInput] = useState<number>(1);
  const [trackingNumber, setTrackingNumber] = useState('');
  const [carrier, setCarrier] = useState('');
  const [receiptNotes, setReceiptNotes] = useState('');

  const loadOrder = useCallback(async () => {
    if (!orderId) return;
    try {
      setLoading(true);
      setError(null);
      const response = await purchaseOrderAPI.getOrder(orderId);
      setOrder(response.data);

      // Initialize receipt items from PO line items
      const items: ReceiptItem[] = response.data.items
        .filter((item: PurchaseOrderLineItem) => !item.is_voided)
        .map((item: PurchaseOrderLineItem) => ({
          po_item_id: item.id,
          item_name: item.item_type === 'asset'
            ? (item.asset_details?.name || 'Unknown Asset')
            : (item.item_details?.name || 'Unknown Item'),
          sku: item.item_type === 'asset'
            ? (item.asset_details?.asset_tag || '—')
            : (item.item_details?.sku || '—'),
          quantity_ordered: item.quantity_ordered,
          quantity_received: item.quantity_received,
          quantity_pending: item.quantity_ordered - item.quantity_received,
          quantity_to_receive: 0,
          is_damaged: false,
          is_expired: false,
          condition_notes: '',
          scanned_upc: '',
        }));
      setReceiptItems(items);
    } catch (err: any) {
      console.error('Error loading purchase order:', err);
      setError(err.response?.data?.error || 'Failed to load purchase order');
    } finally {
      setLoading(false);
    }
  }, [orderId]);

  const loadDeliveries = useCallback(async () => {
    if (!orderId) return;
    try {
      const response = await purchaseOrderAPI.getDeliveries(orderId);
      setDeliveries(response.data.results || []);
    } catch (err: any) {
      console.error('Error loading deliveries:', err);
    }
  }, [orderId]);

  useEffect(() => {
    loadOrder();
    loadDeliveries();
  }, [loadOrder, loadDeliveries]);

  const handleScanBarcode = async () => {
    if (!orderId || !upcInput.trim()) {
      setError('Please enter a UPC/barcode');
      return;
    }

    if (quantityInput < 1) {
      setError('Quantity must be at least 1');
      return;
    }

    setError(null);
    setSaving(true);

    try {
      const response = await purchaseOrderAPI.scanBarcode({
        purchase_order_id: parseInt(orderId),
        scanned_upc: upcInput.trim(),
        quantity_received: quantityInput,
        is_damaged: false, // Can be enhanced with UI controls
        is_expired: false,
        condition_notes: '',
      });

      setSuccess(
        `Successfully received ${quantityInput} unit(s) of ${response.data.item_name}. ` +
        `Total received: ${response.data.total_received}, Remaining: ${response.data.quantity_remaining}`
      );

      // Clear inputs
      setUpcInput('');
      setQuantityInput(1);

      // Reload order to get updated quantities
      await loadOrder();
      await loadDeliveries();

      // Clear success message after 5 seconds
      setTimeout(() => setSuccess(null), 5000);
    } catch (err: any) {
      console.error('Error scanning barcode:', err);
      setError(
        err.response?.data?.error ||
        err.response?.data?.detail ||
        'Failed to process barcode scan. Please check the UPC and try again.'
      );
    } finally {
      setSaving(false);
    }
  };

  const updateReceiptItem = (poItemId: string, updates: Partial<ReceiptItem>) => {
    setReceiptItems(
      receiptItems.map((item) =>
        item.po_item_id === poItemId ? { ...item, ...updates } : item
      )
    );
  };

  const handleManualReceipt = async () => {
    if (!orderId) return;

    const itemsToReceive = receiptItems.filter((item) => item.quantity_to_receive > 0);
    if (itemsToReceive.length === 0) {
      setError('Please set quantities to receive for at least one item');
      return;
    }

    setError(null);
    setSaving(true);

    try {
      // Process each item
      for (const item of itemsToReceive) {
        if (item.quantity_to_receive > item.quantity_pending) {
          setError(
            `Cannot receive ${item.quantity_to_receive} of ${item.item_name}. Only ${item.quantity_pending} remaining.`
          );
          setSaving(false);
          return;
        }

        // For manual receipt, we'll use the scan_barcode endpoint with a placeholder UPC
        // In a real implementation, you might have a separate manual receipt endpoint
        await purchaseOrderAPI.scanBarcode({
          purchase_order_id: parseInt(orderId),
          scanned_upc: `MANUAL-${item.po_item_id}`, // Placeholder for manual entry
          quantity_received: item.quantity_to_receive,
          is_damaged: item.is_damaged,
          is_expired: item.is_expired,
          condition_notes: item.condition_notes,
        });
      }

      setSuccess(`Successfully received ${itemsToReceive.length} item(s)`);

      // Reset receipt items
      await loadOrder();
      await loadDeliveries();

      // Clear success message after 5 seconds
      setTimeout(() => setSuccess(null), 5000);
    } catch (err: any) {
      console.error('Error processing manual receipt:', err);
      setError(err.response?.data?.error || 'Failed to process receipt');
    } finally {
      setSaving(false);
    }
  };

  const formatDate = (dateString: string | null) => {
    if (!dateString) return '—';
    const date = new Date(dateString);
    if (Number.isNaN(date.getTime())) return '—';
    return date.toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  };

  const formatCurrency = (value: string | null) => {
    if (!value) return '—';
    const num = parseFloat(value);
    if (Number.isNaN(num)) return '—';
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 2,
    }).format(num);
  };

  if (loading) {
    return (
      <div className="purchase-order-receiving-page">
        <div className="loading">Loading purchase order...</div>
      </div>
    );
  }

  if (error && !order) {
    return (
      <div className="purchase-order-receiving-page">
        <div className="error">
          <h2>Error</h2>
          <p>{error}</p>
          <Button onClick={() => navigate('/purchasing/orders')} mt="md">
            Back to Purchase Orders
          </Button>
        </div>
      </div>
    );
  }

  if (!order) {
    return (
      <div className="purchase-order-receiving-page">
        <div className="error">Purchase order not found</div>
      </div>
    );
  }

  // Check if order can be received
  const canReceive = ['sent', 'confirmed', 'partially_received'].includes(order.status);

  return (
    <div className="purchase-order-receiving-page">
      <Group justify="space-between" mb="lg">
        <div>
          <Title order={1}>Receive Purchase Order</Title>
          <Text c="dimmed">PO #{order.po_number} - {order.supplier_details}</Text>
        </div>
        <Button variant="subtle" onClick={() => navigate(`/purchasing/orders/${orderId}`)}>
          View PO Details
        </Button>
      </Group>

      {!canReceive && (
        <Alert color="yellow" mb="md">
          This purchase order cannot be received. Status: {order.status_label}
        </Alert>
      )}

      {error && (
        <Alert color="red" mb="md" icon={<IconX size={16} />}>
          {error}
        </Alert>
      )}

      {success && (
        <Alert color="green" mb="md" icon={<IconCheck size={16} />}>
          {success}
        </Alert>
      )}

      <Stack gap="md">
        {/* Barcode Scanning Section */}
        {canReceive && (
          <Paper p="md" withBorder>
            <Title order={3} mb="md">Barcode Scanning</Title>
            <Group align="flex-end">
              <TextInput
                label="UPC/Barcode"
                placeholder="Enter or scan UPC code"
                value={upcInput}
                onChange={(e) => setUpcInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    handleScanBarcode();
                  }
                }}
                style={{ flex: 1 }}
                autoFocus
              />
              <TextInput
                label="Quantity"
                type="number"
                min={1}
                value={quantityInput}
                onChange={(e) => setQuantityInput(parseInt(e.target.value) || 1)}
                style={{ width: '120px' }}
              />
              <Button onClick={handleScanBarcode} loading={saving}>
                Scan & Receive
              </Button>
            </Group>
            <Text size="sm" c="dimmed" mt="xs">
              Enter the UPC/barcode and quantity, then click "Scan & Receive" or press Enter
            </Text>
          </Paper>
        )}

        {/* Manual Receipt Section */}
        {canReceive && (
          <Paper p="md" withBorder>
            <Title order={3} mb="md">Manual Receipt</Title>
            <Text size="sm" c="dimmed" mb="md">
              Set quantities to receive for each item. You can receive partial quantities.
            </Text>

            {receiptItems.length === 0 ? (
              <Text c="dimmed">No items available to receive</Text>
            ) : (
              <>
                <Table>
                  <thead>
                    <tr>
                      <th>Item</th>
                      <th>SKU</th>
                      <th>Ordered</th>
                      <th>Received</th>
                      <th>Pending</th>
                      <th>Qty to Receive</th>
                      <th>Damaged</th>
                      <th>Expired</th>
                      <th>Condition Notes</th>
                    </tr>
                  </thead>
                  <tbody>
                    {receiptItems.map((item) => (
                      <tr key={item.po_item_id}>
                        <td>{item.item_name}</td>
                        <td>{item.sku}</td>
                        <td>{item.quantity_ordered}</td>
                        <td>{item.quantity_received}</td>
                        <td>{item.quantity_pending}</td>
                        <td>
                          <TextInput
                            type="number"
                            min={0}
                            max={item.quantity_pending}
                            value={item.quantity_to_receive || ''}
                            onChange={(e) =>
                              updateReceiptItem(item.po_item_id, {
                                quantity_to_receive: parseInt(e.target.value) || 0,
                              })
                            }
                            disabled={item.quantity_pending === 0}
                            style={{ width: '80px' }}
                          />
                        </td>
                        <td>
                          <Checkbox
                            checked={item.is_damaged}
                            onChange={(e) =>
                              updateReceiptItem(item.po_item_id, {
                                is_damaged: e.currentTarget.checked,
                              })
                            }
                            disabled={item.quantity_to_receive === 0}
                          />
                        </td>
                        <td>
                          <Checkbox
                            checked={item.is_expired}
                            onChange={(e) =>
                              updateReceiptItem(item.po_item_id, {
                                is_expired: e.currentTarget.checked,
                              })
                            }
                            disabled={item.quantity_to_receive === 0}
                          />
                        </td>
                        <td>
                          <TextInput
                            value={item.condition_notes}
                            onChange={(e) =>
                              updateReceiptItem(item.po_item_id, {
                                condition_notes: e.target.value,
                              })
                            }
                            disabled={item.quantity_to_receive === 0}
                            placeholder="Optional notes"
                            style={{ width: '200px' }}
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
                <Group justify="flex-end" mt="md">
                  <Button onClick={handleManualReceipt} loading={saving}>
                    Process Receipt
                  </Button>
                </Group>
              </>
            )}
          </Paper>
        )}

        {/* Delivery History */}
        <Paper p="md" withBorder>
          <Title order={3} mb="md">Delivery History</Title>
          {deliveries.length === 0 ? (
            <Text c="dimmed">No deliveries recorded yet</Text>
          ) : (
            <Table>
              <thead>
                <tr>
                  <th>Delivery Date</th>
                  <th>Received By</th>
                  <th>Items</th>
                  <th>Quantity</th>
                  <th>Tracking</th>
                </tr>
              </thead>
              <tbody>
                {deliveries.map((delivery) => (
                  <tr key={delivery.id}>
                    <td>{formatDate(delivery.delivery_date)}</td>
                    <td>{delivery.received_by_username}</td>
                    <td>{delivery.total_items_received}</td>
                    <td>{delivery.total_quantity_received}</td>
                    <td>
                      {delivery.tracking_number && (
                        <Text size="sm">
                          {delivery.carrier && `${delivery.carrier}: `}
                          {delivery.tracking_number}
                        </Text>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Paper>

        <Group justify="flex-end" mt="md">
          <Button variant="subtle" onClick={() => navigate('/purchasing/orders')}>
            Back to Purchase Orders
          </Button>
        </Group>
      </Stack>
    </div>
  );
};

export default PurchaseOrderReceivingPage;
