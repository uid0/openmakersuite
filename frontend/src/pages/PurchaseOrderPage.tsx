/**
 * Purchase Order Management Page
 * View and manage purchase orders, including setting expected shipment dates for line items
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { purchaseOrderAPI } from '../services/api';
import '../styles/PurchaseOrderPage.css';
import { confirmAction, promptInput, showError, showSuccess } from '../utils/dialogs';

interface PurchaseOrderItem {
  id: string;
  item_type: 'inventory_item' | 'asset' | null;
  item_details: {
    name: string;
    sku: string;
  } | null;
  asset_details: {
    id: string;
    name: string;
    asset_tag: string;
    location_name: string | null;
  } | null;
  quantity_ordered: number;
  quantity_received: number;
  unit_cost_ordered: string;
  unit_cost_actual: string | null;
  estimated_cost: string;
  actual_cost: string | null;
  expected_shipment_date: string | null;
  notes: string;
  is_voided: boolean;
  voided_at: string | null;
  void_reason: string;
}

interface PurchaseOrder {
  id: string;
  po_number: string;
  supplier_details: string;
  status: string;
  status_label: string;
  order_date: string;
  expected_delivery_date: string | null;
  items: PurchaseOrderItem[];
  estimated_total: string;
  voided_at: string | null;
  voided_by_username: string | null;
  void_reason: string;
}

const PurchaseOrderPage: React.FC = () => {
  const { orderId } = useParams<{ orderId: string }>();
  const [order, setOrder] = useState<PurchaseOrder | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingItemId, setEditingItemId] = useState<string | null>(null);
  const [shipmentDate, setShipmentDate] = useState<string>('');
  const [editingCostItemId, setEditingCostItemId] = useState<string | null>(null);
  const [lineCost, setLineCost] = useState<string>('');
  const [saving, setSaving] = useState(false);
  const [voidingItemId, setVoidingItemId] = useState<string | null>(null);
  const [voidReason, setVoidReason] = useState<string>('');
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isStaff, setIsStaff] = useState(false);
  const [markingDelivered, setMarkingDelivered] = useState(false);
  const [deliveryDate, setDeliveryDate] = useState<string>('');
  const [deliveryTracking, setDeliveryTracking] = useState<string>('');
  const [deliveryCarrier, setDeliveryCarrier] = useState<string>('');

  const loadOrder = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await purchaseOrderAPI.getOrder(orderId!);
      setOrder(response.data);
    } catch (err: any) {
      console.error('Error loading purchase order:', err);
      setError(err.response?.data?.error || 'Failed to load purchase order');
    } finally {
      setLoading(false);
    }
  }, [orderId]);

  useEffect(() => {
    // Check if user is authenticated
    const token = localStorage.getItem('token');
    setIsAuthenticated(!!token);
    setIsStaff(
      localStorage.getItem('is_staff') === 'true' ||
        localStorage.getItem('is_superuser') === 'true',
    );

    if (orderId) {
      loadOrder();
    }
  }, [orderId, loadOrder]);

  const handleEditShipmentDate = (item: PurchaseOrderItem) => {
    setEditingItemId(item.id);
    setShipmentDate(item.expected_shipment_date || '');
  };

  const handleCancelEdit = () => {
    setEditingItemId(null);
    setShipmentDate('');
  };

  const handleEditLineCost = (item: PurchaseOrderItem) => {
    setEditingCostItemId(item.id);
    // Calculate line cost from unit_cost_actual if available, otherwise from estimated_cost
    if (item.unit_cost_actual && item.quantity_received > 0) {
      const calculatedLineCost = parseFloat(item.unit_cost_actual) * item.quantity_received;
      setLineCost(calculatedLineCost.toFixed(2));
    } else if (item.actual_cost) {
      setLineCost(parseFloat(item.actual_cost).toFixed(2));
    } else {
      // Use estimated cost as starting point
      setLineCost(parseFloat(item.estimated_cost).toFixed(2));
    }
  };

  const handleCancelEditCost = () => {
    setEditingCostItemId(null);
    setLineCost('');
  };

  const handleSaveLineCost = async (itemId: string, item: PurchaseOrderItem) => {
    const lineCostValue = parseFloat(lineCost);
    if (isNaN(lineCostValue) || lineCostValue < 0) {
      showError('Please enter a valid line cost (must be a positive number)');
      return;
    }

    try {
      setSaving(true);
      await purchaseOrderAPI.updateLineItem(orderId!, itemId, {
        line_cost: lineCostValue,
      });
      await loadOrder(); // Reload to get updated data
      setEditingCostItemId(null);
      setLineCost('');
    } catch (err: any) {
      showError(err.response?.data?.error || 'Failed to update line cost');
      console.error('Error updating line cost:', err);
    } finally {
      setSaving(false);
    }
  };

  const handleSaveShipmentDate = async (itemId: string) => {
    try {
      setSaving(true);
      await purchaseOrderAPI.updateLineItem(orderId!, itemId, {
        expected_shipment_date: shipmentDate || undefined,
      });
      await loadOrder(); // Reload to get updated data
      setEditingItemId(null);
      setShipmentDate('');
    } catch (err: any) {
      showError(err.response?.data?.error || 'Failed to update shipment date');
      console.error('Error updating shipment date:', err);
    } finally {
      setSaving(false);
    }
  };

  const handleVoidItem = (itemId: string) => {
    if (!voidReason.trim()) {
      showError('Please provide a reason for voiding this line item');
      return;
    }

    confirmAction(
      'Void line item?',
      'Are you sure you want to void this line item? This will also mark the item as discontinued from this supplier.',
      async () => {
        try {
          setSaving(true);
          await purchaseOrderAPI.voidLineItem(orderId!, itemId, voidReason);
          await loadOrder(); // Reload to get updated data
          setVoidingItemId(null);
          setVoidReason('');
        } catch (err: any) {
          showError(err.response?.data?.error || 'Failed to void line item');
          console.error('Error voiding line item:', err);
        } finally {
          setSaving(false);
        }
      },
      { labels: { confirm: 'Void', cancel: 'Cancel' }, color: 'red' },
    );
  };

  const handleVoidOrder = async () => {
    const reason = await promptInput(
      'Void purchase order',
      'Reason for voiding (optional)',
      undefined,
      { placeholder: 'e.g. supplier rejected all line items' },
    );
    if (reason === null) return;

    confirmAction(
      'Void this purchase order?',
      'This will void the PO and cascade to all non-voided line items. This cannot be undone.',
      async () => {
        try {
          setSaving(true);
          await purchaseOrderAPI.voidOrder(orderId!, reason);
          showSuccess('Purchase order voided');
          await loadOrder();
        } catch (err: any) {
          showError(err.response?.data?.detail || 'Failed to void purchase order');
          console.error('Error voiding purchase order:', err);
        } finally {
          setSaving(false);
        }
      },
      { labels: { confirm: 'Void PO', cancel: 'Cancel' }, color: 'red' },
    );
  };

  const canVoidOrder = (po: PurchaseOrder) =>
    isStaff && po.status !== 'voided' && po.status !== 'received';

  const handleOpenMarkDelivered = () => {
    const today = new Date().toISOString().split('T')[0];
    setDeliveryDate(today);
    setDeliveryTracking('');
    setDeliveryCarrier('');
    setMarkingDelivered(true);
  };

  const handleCancelMarkDelivered = () => {
    setMarkingDelivered(false);
    setDeliveryDate('');
    setDeliveryTracking('');
    setDeliveryCarrier('');
  };

  const handleSubmitMarkDelivered = async () => {
    if (!deliveryDate) {
      showError('Please select a delivery date');
      return;
    }

    try {
      setSaving(true);
      await purchaseOrderAPI.markDelivered(orderId!, {
        delivery_date: deliveryDate,
        tracking_number: deliveryTracking || undefined,
        carrier: deliveryCarrier || undefined,
      });
      await loadOrder();
      handleCancelMarkDelivered();
    } catch (err: any) {
      showError(err.response?.data?.error || 'Failed to mark purchase order as delivered');
      console.error('Error marking delivered:', err);
    } finally {
      setSaving(false);
    }
  };

  const canMarkDelivered = (po: PurchaseOrder) =>
    isAuthenticated && ['sent', 'confirmed', 'partially_received'].includes(po.status);

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
      <div className="purchase-order-page">
        <div className="loading">Loading purchase order...</div>
      </div>
    );
  }

  if (error || !order) {
    return (
      <div className="purchase-order-page">
        <div className="error">
          <h2>Error</h2>
          <p>{error || 'Purchase order not found'}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="purchase-order-page">
      <header className="po-header">
        <div>
          <h1>Purchase Order: {order.po_number}</h1>
          <p className="po-supplier">Supplier: {order.supplier_details}</p>
        </div>
        <div className="po-status">
          <span className={`status-badge status-${order.status}`}>{order.status_label}</span>
          {canMarkDelivered(order) && !markingDelivered && (
            <button
              type="button"
              className="btn-primary mark-delivered-button"
              onClick={handleOpenMarkDelivered}
            >
              Mark as Delivered
            </button>
          )}
          {canVoidOrder(order) && (
            <button
              type="button"
              className="btn-danger void-po-button"
              onClick={handleVoidOrder}
              disabled={saving}
            >
              Void PO
            </button>
          )}
        </div>
      </header>

      {order.status === 'voided' && (
        <section className="po-voided-banner" aria-label="Voided purchase order">
          <span className="status-badge status-voided">VOIDED</span>
          {order.void_reason && (
            <p className="void-reason">
              <strong>Reason:</strong> {order.void_reason}
            </p>
          )}
          {order.voided_by_username && (
            <p className="void-meta">
              Voided by {order.voided_by_username}
              {order.voided_at && ` on ${formatDate(order.voided_at)}`}
            </p>
          )}
        </section>
      )}

      {markingDelivered && (
        <section className="mark-delivered-panel" aria-label="Mark purchase order as delivered">
          <h2>Mark as Delivered</h2>
          <div className="mark-delivered-fields">
            <label htmlFor="mark-delivered-date">
              Delivery Date
              <input
                id="mark-delivered-date"
                type="date"
                value={deliveryDate}
                onChange={(e) => setDeliveryDate(e.target.value)}
                required
              />
            </label>
            <label htmlFor="mark-delivered-tracking">
              Tracking Number (optional)
              <input
                id="mark-delivered-tracking"
                type="text"
                value={deliveryTracking}
                onChange={(e) => setDeliveryTracking(e.target.value)}
                placeholder="e.g. 1Z999AA10123456784"
              />
            </label>
            <label htmlFor="mark-delivered-carrier">
              Carrier (optional)
              <input
                id="mark-delivered-carrier"
                type="text"
                value={deliveryCarrier}
                onChange={(e) => setDeliveryCarrier(e.target.value)}
                placeholder="e.g. UPS"
              />
            </label>
          </div>
          <div className="mark-delivered-actions">
            <button
              type="button"
              className="btn-primary"
              onClick={handleSubmitMarkDelivered}
              disabled={saving || !deliveryDate}
            >
              {saving ? 'Saving…' : 'Confirm Delivery'}
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={handleCancelMarkDelivered}
              disabled={saving}
            >
              Cancel
            </button>
          </div>
        </section>
      )}

      <div className="po-info">
        <div className="info-item">
          <span className="info-label">Order Date:</span>
          <span className="info-value">{formatDate(order.order_date)}</span>
        </div>
        <div className="info-item">
          <span className="info-label">Expected Delivery:</span>
          <span className="info-value">{formatDate(order.expected_delivery_date)}</span>
        </div>
        <div className="info-item">
          <span className="info-label">Estimated Total:</span>
          <span className="info-value">{formatCurrency(order.estimated_total)}</span>
        </div>
      </div>

      <section className="po-items">
        <h2>Line Items</h2>
        <table className="items-table">
          <thead>
            <tr>
              <th>Item</th>
              <th>SKU</th>
              <th>Quantity Ordered</th>
              <th>Quantity Received</th>
              <th>Unit Cost</th>
              <th>Line Cost</th>
              <th>Expected Shipment Date</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {order.items.length === 0 ? (
              <tr>
                <td colSpan={9} className="no-data">
                  No line items found
                </td>
              </tr>
            ) : (
              order.items.map((item) => {
                const itemName = item.item_type === 'asset' 
                  ? (item.asset_details?.name || 'Unknown Asset')
                  : (item.item_details?.name || 'Unknown Item');
                const itemSku = item.item_type === 'asset'
                  ? (item.asset_details?.asset_tag || '—')
                  : (item.item_details?.sku || '—');
                
                return (
                <tr key={item.id} className={item.is_voided ? 'voided-item' : ''}>
                  <td>
                    {itemName}
                    {item.item_type === 'asset' && item.asset_details?.location_name && (
                      <div style={{ fontSize: '0.875rem', color: '#64748b', marginTop: '0.25rem' }}>
                        Location: {item.asset_details.location_name}
                      </div>
                    )}
                  </td>
                  <td>{itemSku}</td>
                  <td>{item.quantity_ordered}</td>
                  <td>{item.quantity_received}</td>
                  <td>
                    {item.unit_cost_actual 
                      ? formatCurrency(item.unit_cost_actual) 
                      : formatCurrency(item.unit_cost_ordered)}
                    {item.unit_cost_actual && (
                      <div style={{ fontSize: '0.75rem', color: '#64748b', marginTop: '0.25rem' }}>
                        (ordered: {formatCurrency(item.unit_cost_ordered)})
                      </div>
                    )}
                  </td>
                  <td>
                    {editingCostItemId === item.id ? (
                      <div className="edit-line-cost">
                        <input
                          type="number"
                          step="0.01"
                          min="0"
                          value={lineCost}
                          onChange={(e) => setLineCost(e.target.value)}
                          disabled={saving || item.is_voided}
                          className="cost-input"
                          placeholder="Enter total line cost"
                        />
                        <div className="calculated-unit-cost" style={{ fontSize: '0.75rem', color: '#64748b', marginTop: '0.25rem' }}>
                          Unit cost: {item.quantity_ordered > 0 
                            ? formatCurrency((parseFloat(lineCost || '0') / item.quantity_ordered).toFixed(4))
                            : '—'}
                        </div>
                        <div className="edit-actions">
                          <button
                            onClick={() => handleSaveLineCost(item.id, item)}
                            disabled={saving}
                            className="btn-save"
                          >
                            {saving ? 'Saving...' : 'Save'}
                          </button>
                          <button
                            onClick={handleCancelEditCost}
                            disabled={saving}
                            className="btn-cancel"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="line-cost-display">
                        <span>
                          {item.actual_cost 
                            ? formatCurrency(item.actual_cost)
                            : formatCurrency(item.estimated_cost)}
                        </span>
                        {!item.is_voided && isAuthenticated && (
                          <button
                            onClick={() => handleEditLineCost(item)}
                            className="btn-edit"
                            title="Edit line cost"
                            style={{ marginLeft: '0.5rem' }}
                          >
                            ✏️
                          </button>
                        )}
                      </div>
                    )}
                  </td>
                  <td>
                    {editingItemId === item.id ? (
                      <div className="edit-shipment-date">
                        <input
                          type="date"
                          value={shipmentDate}
                          onChange={(e) => setShipmentDate(e.target.value)}
                          disabled={saving || item.is_voided}
                          className="date-input"
                        />
                        <div className="edit-actions">
                          <button
                            onClick={() => handleSaveShipmentDate(item.id)}
                            disabled={saving}
                            className="btn-save"
                          >
                            {saving ? 'Saving...' : 'Save'}
                          </button>
                          <button
                            onClick={handleCancelEdit}
                            disabled={saving}
                            className="btn-cancel"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="shipment-date-display">
                        <span>{formatDate(item.expected_shipment_date)}</span>
                        {!item.is_voided && isAuthenticated && (
                          <button
                            onClick={() => handleEditShipmentDate(item)}
                            className="btn-edit"
                            title="Edit shipment date"
                          >
                            ✏️
                          </button>
                        )}
                      </div>
                    )}
                  </td>
                  <td>
                    {item.is_voided ? (
                      <div className="voided-status">
                        <span className="status-badge status-voided">Voided</span>
                        {item.void_reason && (
                          <div style={{ fontSize: '0.75rem', color: '#64748b', marginTop: '0.25rem' }}>
                            {item.void_reason}
                          </div>
                        )}
                      </div>
                    ) : (
                      <span className="status-badge status-active">Active</span>
                    )}
                  </td>
                  <td>
                    {isAuthenticated ? (
                      voidingItemId === item.id ? (
                        <div className="void-item-form">
                          <textarea
                            value={voidReason}
                            onChange={(e) => setVoidReason(e.target.value)}
                            placeholder="Reason for voiding (e.g., item discontinued by supplier)"
                            disabled={saving}
                            rows={3}
                            style={{ width: '100%', marginBottom: '0.5rem' }}
                          />
                          <div className="edit-actions">
                            <button
                              onClick={() => handleVoidItem(item.id)}
                              disabled={saving || !voidReason.trim()}
                              className="btn-void"
                            >
                              {saving ? 'Voiding...' : 'Confirm Void'}
                            </button>
                            <button
                              onClick={() => {
                                setVoidingItemId(null);
                                setVoidReason('');
                              }}
                              disabled={saving}
                              className="btn-cancel"
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="item-actions">
                          {!item.is_voided && editingItemId !== item.id && (
                            <>
                              <button
                                onClick={() => handleEditShipmentDate(item)}
                                className="btn-edit-item"
                              >
                                Edit Shipment Date
                              </button>
                              {item.quantity_received === 0 && (
                                <button
                                  onClick={() => setVoidingItemId(item.id)}
                                  className="btn-void-item"
                                  style={{ marginLeft: '0.5rem' }}
                                >
                                  Void Item
                                </button>
                              )}
                            </>
                          )}
                        </div>
                      )
                    ) : (
                      <span className="view-only-note">View only</span>
                    )}
                  </td>
                </tr>
                );
              })
            )}
          </tbody>
        </table>
      </section>
    </div>
  );
};

export default PurchaseOrderPage;

