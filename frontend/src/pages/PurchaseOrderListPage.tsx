/**
 * Public Purchase Order List Page
 * Shows all active and settled purchase orders for transparency
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { purchaseOrderAPI } from '../services/api';
import '../styles/PurchaseOrderListPage.css';

interface PurchaseOrder {
  id: string;
  po_number: string;
  supplier_details: string;
  status: string;
  status_label: string;
  order_date: string;
  expected_delivery_date: string | null;
  estimated_total: string;
  actual_total: string | null;
  total_items: number;
  total_quantity: number;
  is_fully_received: boolean;
}

const PurchaseOrderListPage: React.FC = () => {
  const [orders, setOrders] = useState<PurchaseOrder[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>('');

  const loadOrders = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const params = statusFilter ? { status: statusFilter } : undefined;
      const response = await purchaseOrderAPI.listOrders(params);
      setOrders(response.data.results || []);
    } catch (err: any) {
      console.error('Error loading purchase orders:', err);
      console.error('Error details:', {
        status: err?.response?.status,
        statusText: err?.response?.statusText,
        data: err?.response?.data,
        message: err?.message,
      });
      setError(err.response?.data?.error || err.response?.data?.detail || err.message || 'Failed to load purchase orders');
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    loadOrders();
  }, [loadOrders]);

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

  const getStatusClass = (status: string) => {
    const statusMap: { [key: string]: string } = {
      sent: 'status-sent',
      confirmed: 'status-confirmed',
      partially_received: 'status-partially-received',
      received: 'status-received',
    };
    return statusMap[status] || 'status-default';
  };

  if (loading) {
    return (
      <div className="purchase-order-list-page">
        <div className="loading">Loading purchase orders...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="purchase-order-list-page">
        <div className="error">
          <h2>Error</h2>
          <p>{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="purchase-order-list-page">
      <header className="po-list-header">
        <div>
          <h1>Purchase Orders</h1>
          <p className="po-list-subtitle">
            Active and settled purchase orders for makerspace transparency
          </p>
        </div>
        <div className="po-list-actions">
          <Link to="/inventory/transparency" className="transparency-link">
            View Financial Transparency →
          </Link>
        </div>
      </header>

      <div className="po-list-filters">
        <label htmlFor="status-filter">Filter by Status:</label>
        <select
          id="status-filter"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="status-filter-select"
        >
          <option value="">All Active & Settled</option>
          <option value="sent">Sent to Supplier</option>
          <option value="confirmed">Confirmed by Supplier</option>
          <option value="partially_received">Partially Received</option>
          <option value="received">Fully Received</option>
        </select>
      </div>

      <div className="po-list-summary">
        <div className="summary-item">
          <span className="summary-label">Total Orders:</span>
          <span className="summary-value">{orders.length}</span>
        </div>
        <div className="summary-item">
          <span className="summary-label">Total Estimated:</span>
          <span className="summary-value">
            {formatCurrency(
              orders
                .reduce((sum, order) => sum + parseFloat(order.estimated_total || '0'), 0)
                .toString()
            )}
          </span>
        </div>
        <div className="summary-item">
          <span className="summary-label">Total Actual:</span>
          <span className="summary-value">
            {formatCurrency(
              orders
                .reduce(
                  (sum, order) => sum + parseFloat(order.actual_total || '0'),
                  0
                )
                .toString()
            )}
          </span>
        </div>
      </div>

      <section className="po-list-table">
        {orders.length === 0 ? (
          <div className="no-orders">
            <p>No purchase orders found matching the selected criteria.</p>
          </div>
        ) : (
          <table className="orders-table">
            <thead>
              <tr>
                <th>PO Number</th>
                <th>Supplier</th>
                <th>Status</th>
                <th>Order Date</th>
                <th>Expected Delivery</th>
                <th>Items</th>
                <th>Estimated Total</th>
                <th>Actual Total</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((order) => (
                <tr key={order.id}>
                  <td className="po-number">{order.po_number}</td>
                  <td>{order.supplier_details}</td>
                  <td>
                    <span className={`status-badge ${getStatusClass(order.status)}`}>
                      {order.status_label}
                    </span>
                  </td>
                  <td>{formatDate(order.order_date)}</td>
                  <td>{formatDate(order.expected_delivery_date)}</td>
                  <td>
                    {order.total_items} item{order.total_items !== 1 ? 's' : ''} (
                    {order.total_quantity} units)
                  </td>
                  <td>{formatCurrency(order.estimated_total)}</td>
                  <td>
                    {order.actual_total ? (
                      formatCurrency(order.actual_total)
                    ) : (
                      <span className="no-data">—</span>
                    )}
                  </td>
                  <td>
                    <Link
                      to={`/purchasing/orders/${order.id}`}
                      className="view-order-link"
                    >
                      View Details →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
};

export default PurchaseOrderListPage;

