/**
 * Purchase Order Management Page
 * View and manage purchase orders, including setting expected shipment dates for line items
 */
import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { purchaseOrderAPI } from '../services/api';
import '../styles/PurchaseOrderPage.css';

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
  expected_shipment_date: string | null;
  notes: string;
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
}

const PurchaseOrderPage: React.FC = () => {
  const { orderId } = useParams<{ orderId: string }>();
  const [order, setOrder] = useState<PurchaseOrder | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingItemId, setEditingItemId] = useState<string | null>(null);
  const [shipmentDate, setShipmentDate] = useState<string>('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (orderId) {
      loadOrder();
    }
  }, [orderId]);

  const loadOrder = async () => {
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
  };

  const handleEditShipmentDate = (item: PurchaseOrderItem) => {
    setEditingItemId(item.id);
    setShipmentDate(item.expected_shipment_date || '');
  };

  const handleCancelEdit = () => {
    setEditingItemId(null);
    setShipmentDate('');
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
      alert(err.response?.data?.error || 'Failed to update shipment date');
      console.error('Error updating shipment date:', err);
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
        </div>
      </header>

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
              <th>Expected Shipment Date</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {order.items.length === 0 ? (
              <tr>
                <td colSpan={7} className="no-data">
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
                <tr key={item.id}>
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
                  <td>{formatCurrency(item.unit_cost_ordered)}</td>
                  <td>
                    {editingItemId === item.id ? (
                      <div className="edit-shipment-date">
                        <input
                          type="date"
                          value={shipmentDate}
                          onChange={(e) => setShipmentDate(e.target.value)}
                          disabled={saving}
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
                        <button
                          onClick={() => handleEditShipmentDate(item)}
                          className="btn-edit"
                          title="Edit shipment date"
                        >
                          ✏️
                        </button>
                      </div>
                    )}
                  </td>
                  <td>
                    {editingItemId !== item.id && (
                      <button
                        onClick={() => handleEditShipmentDate(item)}
                        className="btn-edit-item"
                      >
                        Edit Shipment Date
                      </button>
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

