/**
 * Financial Transparency Page - Shows public spending information
 * Dedicated to makerspace transparency and community trust
 */
import React, { useEffect, useState } from 'react';
import axios from 'axios';
import '../styles/TransparencyPage.css';

interface TransparencyOrder {
  id: number;
  item_name: string;
  item_category: string | null;
  quantity_ordered: number;
  status: string;
  requested_at: string;
  ordered_at: string | null;
  delivered_at: string | null;
  estimated_cost: number | null;
  actual_cost: number | null;
  cost_per_unit: number | null;
  cost_variance: number | null;
  order_number: string;
  invoice_number: string;
  invoice_url: string;
  purchase_order_url: string;
  delivery_tracking_url: string;
  supplier_url: string;
  public_notes: string;
  supplier_name: string | null;
}

interface TransparencySummary {
  total_orders_with_financial_data: number;
  total_amount_spent: number;
  last_updated: string;
  transparency_note: string;
}

interface TransparencyData {
  summary: TransparencySummary;
  orders: TransparencyOrder[];
}

const TransparencyPage: React.FC = () => {
  const [data, setData] = useState<TransparencyData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchTransparencyData = async () => {
      try {
        const response = await axios.get<TransparencyData>('/api/reorders/transparency/');
        setData(response.data);
      } catch (err: any) {
        setError('Unable to load transparency data');
        console.error('Transparency data error:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchTransparencyData();
  }, []);

  const formatCurrency = (amount: number | null) => {
    if (amount === null) return 'N/A';
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD'
    }).format(amount);
  };

  const formatDate = (dateString: string | null) => {
    if (!dateString) return 'N/A';
    return new Date(dateString).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric'
    });
  };

  if (loading) {
    return (
      <div className="transparency-page">
        <div className="loading">
          <div className="loading-spinner">🔄</div>
          <p>Loading transparency data...</p>
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="transparency-page">
        <div className="error">
          <h2>⚠️ Unable to Load Transparency Data</h2>
          <p>{error}</p>
          <button onClick={() => window.location.reload()}>
            Try Again
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="transparency-page">
      <header className="transparency-header">
        <h1>
          <span className="icon">🔍</span>
          Financial Transparency
        </h1>
        <p className="transparency-mission">
          {data.summary.transparency_note}
        </p>
      </header>

      <div className="summary-section">
        <div className="summary-card">
          <h2>Summary Statistics</h2>
          <div className="stats-grid">
            <div className="stat-item">
              <span className="stat-label">Total Orders</span>
              <span className="stat-value">{data.summary.total_orders_with_financial_data}</span>
            </div>
            <div className="stat-item">
              <span className="stat-label">Total Spent</span>
              <span className="stat-value">{formatCurrency(data.summary.total_amount_spent)}</span>
            </div>
            <div className="stat-item">
              <span className="stat-label">Last Updated</span>
              <span className="stat-value">{formatDate(data.summary.last_updated)}</span>
            </div>
          </div>
        </div>
      </div>

      <div className="orders-section">
        <h2>Order Details</h2>
        <div className="orders-grid">
          {data.orders.map((order) => (
            <div key={order.id} className="order-card">
              <div className="order-header">
                <h3>{order.item_name}</h3>
                <span className={`status-badge status-${order.status}`}>
                  {order.status.charAt(0).toUpperCase() + order.status.slice(1)}
                </span>
              </div>

              <div className="order-details">
                <div className="detail-row">
                  <span className="label">Quantity:</span>
                  <span className="value">{order.quantity_ordered} units</span>
                </div>
                {order.item_category && (
                  <div className="detail-row">
                    <span className="label">Category:</span>
                    <span className="value">{order.item_category}</span>
                  </div>
                )}
                {order.supplier_name && (
                  <div className="detail-row">
                    <span className="label">Supplier:</span>
                    <span className="value">{order.supplier_name}</span>
                  </div>
                )}
              </div>

              <div className="financial-info">
                {order.estimated_cost && (
                  <div className="detail-row">
                    <span className="label">Estimated Cost:</span>
                    <span className="value">{formatCurrency(order.estimated_cost)}</span>
                  </div>
                )}
                {order.actual_cost && (
                  <div className="detail-row">
                    <span className="label">Actual Cost:</span>
                    <span className="value">{formatCurrency(order.actual_cost)}</span>
                  </div>
                )}
                {order.cost_per_unit && (
                  <div className="detail-row">
                    <span className="label">Cost per Unit:</span>
                    <span className="value">{formatCurrency(order.cost_per_unit)}</span>
                  </div>
                )}
                {order.cost_variance && (
                  <div className="detail-row">
                    <span className="label">Cost Variance:</span>
                    <span className={`value ${order.cost_variance > 0 ? 'over-budget' : 'under-budget'}`}>
                      {order.cost_variance > 0 ? '+' : ''}{formatCurrency(order.cost_variance)}
                    </span>
                  </div>
                )}
              </div>

              <div className="timeline">
                <div className="timeline-item">
                  <span className="timeline-label">Requested:</span>
                  <span className="timeline-date">{formatDate(order.requested_at)}</span>
                </div>
                {order.ordered_at && (
                  <div className="timeline-item">
                    <span className="timeline-label">Ordered:</span>
                    <span className="timeline-date">{formatDate(order.ordered_at)}</span>
                  </div>
                )}
                {order.delivered_at && (
                  <div className="timeline-item">
                    <span className="timeline-label">Delivered:</span>
                    <span className="timeline-date">{formatDate(order.delivered_at)}</span>
                  </div>
                )}
              </div>

              <div className="document-links">
                {order.invoice_url && (
                  <a href={order.invoice_url} target="_blank" rel="noopener noreferrer" className="doc-link">
                    📄 Invoice
                  </a>
                )}
                {order.purchase_order_url && (
                  <a href={order.purchase_order_url} target="_blank" rel="noopener noreferrer" className="doc-link">
                    📋 Purchase Order
                  </a>
                )}
                {order.delivery_tracking_url && (
                  <a href={order.delivery_tracking_url} target="_blank" rel="noopener noreferrer" className="doc-link">
                    🚚 Tracking
                  </a>
                )}
                {order.supplier_url && (
                  <a href={order.supplier_url} target="_blank" rel="noopener noreferrer" className="doc-link">
                    🏪 Supplier
                  </a>
                )}
              </div>

              {order.order_number && (
                <div className="order-number">
                  Order #: {order.order_number}
                </div>
              )}

              {order.public_notes && (
                <div className="public-notes">
                  <p>{order.public_notes}</p>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      <footer className="transparency-footer">
        <p>
          This transparency page reflects our commitment to open operations.
          All financial information is made available to promote trust and accountability
          within the makerspace community.
        </p>
        <p>
          <a href="/tv-dashboard">← Back to Dashboard</a>
        </p>
      </footer>
    </div>
  );
};

export default TransparencyPage;

