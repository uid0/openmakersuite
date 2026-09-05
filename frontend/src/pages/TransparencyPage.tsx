/**
 * Financial Transparency Page - Shows public spending information
 * Dedicated to makerspace transparency and community trust
 */
import { Button, Paper, Text } from '@mantine/core';
import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import { analyticsAPI } from '../services/api';
import { vendorDataWithheld } from '../utils/vendorVisibility';
import '../styles/TransparencyPage.css';

interface TransparencyOrder {
  id: number;
  item_id: string;
  item_name: string;
  item_category: string | null;
  quantity_ordered: number;
  status: string;
  requested_at: string;
  ordered_at: string | null;
  delivered_at: string | null;
  /**
   * The vendor block: ABSENT for a caller with no session
   * (op-anonymous-read-posture), with `vendor_data_withheld: true` in its place.
   * Optional here so the compiler makes a reader handle the third state —
   * `null` still means "no figure recorded", which is a claim about the ORDER.
   */
  vendor_data_withheld?: boolean;
  estimated_cost?: number | null;
  actual_cost?: number | null;
  cost_per_unit?: number | null;
  cost_variance?: number | null;
  order_number?: string;
  invoice_number?: string;
  invoice_url?: string;
  purchase_order_url?: string;
  delivery_tracking_url?: string;
  supplier_url?: string;
  public_notes: string;
  supplier_name?: string | null;
}

interface TransparencySummary {
  total_orders_with_financial_data: number;
  total_amount_spent: number;
  last_updated: string;
  transparency_note: string;
}

interface LedgerEntry {
  id: number;
  item_id: string;
  item_name: string;
  quantity: number;
  requested_at: string;
  ordered_at: string | null;
  delivered_at: string | null;
  status: string;
  /** Withheld from an anonymous caller — see `TransparencyOrder`. */
  vendor_data_withheld?: boolean;
  supplier_name?: string | null;
  actual_cost?: number | null;
  estimated_cost?: number | null;
  order_number?: string;
  invoice_number?: string;
}

interface TransparencyData {
  summary: TransparencySummary;
  orders: TransparencyOrder[];
  ledger: LedgerEntry[];
}

const TransparencyPage: React.FC = () => {
  const [data, setData] = useState<TransparencyData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchTransparencyData = async () => {
      try {
        const response = await analyticsAPI.getTransparencyLedger<TransparencyData>();
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

  /**
   * Is there a figure to show at all?
   *
   * `!= null`, never truthiness (op-9m2v). A recorded `0.00` is a KNOWN cost —
   * the server publishes `estimated_cost: 0.0` for a donated order — and in JSX
   * a numeric `0` does not merely fail to render the row, it RENDERS: `{0 &&
   * <div/>}` prints a bare "0" into the card and drops the figure beside it.
   */
  const isReported = (amount: number | null | undefined): amount is number =>
    amount !== null && amount !== undefined;

  /**
   * Which of the THREE things a variance can say — over, under, or exactly on.
   *
   * Landing exactly on estimate is its own fact, not a favourable one (op-9m2v).
   * The zero case only became reachable when the truthiness guard above was
   * replaced: `{0 && <div/>}` used to drop the row, so `> 0 ? over : under`
   * never had to answer for it and called a $0.00 variance "under budget".
   * Named and rendered in words as well as colour, because colour alone is not
   * a distinction a reader can act on.
   */
  const varianceTone = (variance: number) => {
    if (variance > 0) return { className: 'over-budget', sign: '+', note: ' over budget' };
    if (variance < 0) return { className: 'under-budget', sign: '', note: ' under budget' };
    return { className: 'on-budget', sign: '', note: ' on budget' };
  };

  const formatCurrency = (amount: number | null | undefined) => {
    // `undefined` as well as `null`: the server WITHHOLDS the per-order money
    // keys from a caller with no session rather than nulling them
    // (op-anonymous-read-posture), and `amount === null` alone let `undefined`
    // through to `Intl.NumberFormat().format()`, which renders "$NaN".
    if (amount === null || amount === undefined) return 'N/A';
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

  const formatStatus = (status: string) => {
    if (!status) return 'Unknown';
    return status.charAt(0).toUpperCase() + status.slice(1);
  };

  if (loading) {
    return (
      <WorkspacePage
        testId="transparency-page"
        hero={{
          eyebrow: 'Inventory',
          title: 'Financial transparency',
          description: 'Loading…',
        }}
      >
        <Paper withBorder p="md">
          <Text c="dimmed">Loading transparency data…</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  if (error || !data) {
    return (
      <WorkspacePage
        testId="transparency-page"
        hero={{
          eyebrow: 'Inventory',
          title: 'Financial transparency',
          description: error || 'Unable to load transparency data.',
          action: (
            <Button onClick={() => window.location.reload()}>Try again</Button>
          ),
        }}
      >
        <Paper withBorder p="md" radius="md" bg="red.0" c="red.9">
          <Text>{error || 'Unable to load transparency data.'}</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  // Read off the payload, not off auth state: the server has already decided,
  // and a second client-side derivation of the same answer is how the two come
  // to disagree. Either array carrying the marker means the same gate ran.
  const vendorWithheld =
    vendorDataWithheld(data.orders[0]) || vendorDataWithheld(data.ledger[0]);

  return (
    <WorkspacePage
      testId="transparency-page"
      hero={{
        eyebrow: 'Inventory · Public ledger',
        title: 'Financial transparency',
        description: data.summary.transparency_note,
      }}
    >
      <div className="transparency-page">

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

      <div className="ledger-section">
        <div className="section-header">
          <h2>Logistics Purchase Ledger</h2>
          <p className="section-subtitle">
            Chronological record of purchases handled by the logistics team to keep our community informed.
          </p>
          {/* Said once, above the table, rather than repeated as "N/A" down a
              column. The page still publishes what the space spent in total —
              that is what it is for — and the per-order breakdown is a member
              view (op-anonymous-read-posture). */}
          {vendorWithheld && (
            <p className="section-subtitle" data-testid="ledger-vendor-withheld">
              Supplier names and per-order costs are shown to signed-in members.
              Totals, items, quantities and dates are public.
            </p>
          )}
        </div>
        {data.ledger.length === 0 ? (
          <div className="empty-ledger">
            <p>No logistics purchases with transparency data have been recorded yet.</p>
          </div>
        ) : (
          <div className="ledger-table-container">
            <table className="ledger-table">
              <thead>
                <tr>
                  <th>Requested</th>
                  <th>Ordered</th>
                  <th>Delivered</th>
                  <th>Item</th>
                  <th>Qty</th>
                  {/* Dropped, not blanked, for a caller with no session
                      (op-anonymous-read-posture). The server withholds
                      `supplier_name` and the per-order costs, so these columns
                      would read "N/A" and "$NaN" on every row — "no supplier on
                      file" and a nonsense figure, both claims about the ORDER
                      rather than about the reader. An absent column cannot be
                      misread as an empty value; the note above the table says
                      where the numbers went. */}
                  {!vendorWithheld && <th>Supplier</th>}
                  {!vendorWithheld && <th>Cost</th>}
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {data.ledger.map((entry) => (
                  <tr key={entry.id}>
                    <td>{formatDate(entry.requested_at)}</td>
                    <td>{formatDate(entry.ordered_at)}</td>
                    <td>{formatDate(entry.delivered_at)}</td>
                    <td>
                      <div className="ledger-item-name">
                        <Link 
                          to={`/inventory/items/${entry.item_id}`}
                          style={{ color: '#0066cc', textDecoration: 'none' }}
                          onMouseEnter={(e) => e.currentTarget.style.textDecoration = 'underline'}
                          onMouseLeave={(e) => e.currentTarget.style.textDecoration = 'none'}
                        >
                          {entry.item_name}
                        </Link>
                      </div>
                      {(entry.order_number || entry.invoice_number) && (
                        <div className="ledger-item-meta">
                          {entry.order_number && <span>Order #{entry.order_number}</span>}
                          {entry.invoice_number && <span>Invoice #{entry.invoice_number}</span>}
                        </div>
                      )}
                      <div style={{ marginTop: '0.25rem' }}>
                        <Link
                          to={`/inventory/assets?inventory_item=${entry.item_id}`}
                          style={{ 
                            color: '#0066cc', 
                            textDecoration: 'none', 
                            fontSize: '0.875rem' 
                          }}
                          onMouseEnter={(e) => e.currentTarget.style.textDecoration = 'underline'}
                          onMouseLeave={(e) => e.currentTarget.style.textDecoration = 'none'}
                        >
                          View Related Assets →
                        </Link>
                      </div>
                    </td>
                    <td>{entry.quantity}</td>
                    {!vendorWithheld && <td>{entry.supplier_name || 'N/A'}</td>}
                    {!vendorWithheld && (
                      <td>{formatCurrency(entry.actual_cost ?? entry.estimated_cost ?? null)}</td>
                    )}
                    <td>
                      <span className={`ledger-status status-${entry.status}`}>
                        {formatStatus(entry.status)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="orders-section">
        <h2>Order Details</h2>
        <div className="orders-grid">
          {data.orders.map((order) => (
            <div key={order.id} className="order-card">
              <div className="order-header">
                <h3>
                  <Link 
                    to={`/inventory/items/${order.item_id}`}
                    style={{ color: 'inherit', textDecoration: 'none' }}
                    onMouseEnter={(e) => e.currentTarget.style.textDecoration = 'underline'}
                    onMouseLeave={(e) => e.currentTarget.style.textDecoration = 'none'}
                  >
                    {order.item_name}
                  </Link>
                </h3>
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
                <div className="detail-row" style={{ marginTop: '0.5rem' }}>
                  <Link
                    to={`/inventory/assets?inventory_item=${order.item_id}`}
                    style={{ 
                      color: '#0066cc', 
                      textDecoration: 'none',
                      fontSize: '0.875rem'
                    }}
                    onMouseEnter={(e) => e.currentTarget.style.textDecoration = 'underline'}
                    onMouseLeave={(e) => e.currentTarget.style.textDecoration = 'none'}
                  >
                    View Related Assets →
                  </Link>
                </div>
              </div>

              <div className="financial-info">
                {isReported(order.estimated_cost) && (
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
                {isReported(order.cost_variance) && (
                  <div className="detail-row">
                    <span className="label">Cost Variance:</span>
                    <span className={`value ${varianceTone(order.cost_variance).className}`}>
                      {varianceTone(order.cost_variance).sign}
                      {formatCurrency(order.cost_variance)}
                      {varianceTone(order.cost_variance).note}
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
        {/* "ALL financial information is made available" stopped being true for
            a reader with no session (op-anonymous-read-posture). A page whose
            whole subject is accountability cannot carry a claim its own payload
            no longer honours, so it is worded for the reader it has — the same
            edit the server makes to `summary.transparency_note`. */}
        <p>
          This transparency page reflects our commitment to open operations.
          {vendorWithheld
            ? ' What the makerspace spends is published here; supplier names and ' +
              'per-order costs are shown to signed-in members.'
            : ' All financial information is made available to promote trust and ' +
              'accountability within the makerspace community.'}
        </p>
        <p>
          <a href="/tv-dashboard">← Back to Dashboard</a>
        </p>
      </footer>
      </div>
    </WorkspacePage>
  );
};

export default TransparencyPage;
