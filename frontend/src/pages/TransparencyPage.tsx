/**
 * Financial Transparency Page - Shows public spending information
 * Dedicated to makerspace transparency and community trust
 */
import { Button, Paper, Text } from '@mantine/core';
import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import { analyticsAPI } from '../services/api';
import { supplierChoiceSummary } from '../utils/supplierChoice';
import { SupplierChoice } from '../types';
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
  actual_cost?: number | null;
  cost_per_unit?: number | null;
  order_number?: string;
  invoice_number?: string;
  invoice_url?: string;
  purchase_order_url?: string;
  delivery_tracking_url?: string;
  supplier_url?: string;
  public_notes: string;
  /**
   * THE ITEM'S supplier derivation, as of this response — never the order's.
   *
   * `ReorderRequest` has no supplier relationship (backend/reorder_queue/models.py),
   * so the flat `supplier_name` this row used to carry was `order.item.supplier`
   * resolved at request time: edit the item's links today and a delivered order
   * reported a vendor it could not have bought from. The key says whose answer
   * it is, and every surface below labels it the same way.
   */
  item_supplier_choice?: SupplierChoice;
  /**
   * What this quantity would cost at that supplier's CURRENT price — a live
   * quote, not what the order was estimated at. No estimate is recorded on a
   * reorder request, which is why `cost_variance` is gone rather than renamed:
   * there is no budget for a variance to be measured against.
   */
  item_estimated_cost_today?: number | null;
}

interface TransparencySummary {
  /**
   * Every qualifying order, not the number of rows below.
   *
   * The server used to compute this and `total_amount_spent` by walking the
   * capped page, so a space with more than a hundred qualifying orders
   * published less than it had spent. They are aggregates over the whole set
   * now, which is why the ledger says which slice of it the table is showing.
   */
  total_orders_with_financial_data: number;
  total_amount_spent: number;
  last_updated: string;
  transparency_note: string;
  /**
   * The gate's marker, carried HERE as well as on each row — see
   * `TransparencyOrder`. The summary is the one object this payload always has:
   * `orders` and `ledger` are built in the same loop and empty together, so a
   * reader that takes the answer off row 0 gets `false` for an empty ledger and
   * shows an anonymous visitor the claim the server stopped making.
   */
  vendor_data_withheld?: boolean;
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
  /** The ITEM's supplier today — see `TransparencyOrder.item_supplier_choice`. */
  item_supplier_choice?: SupplierChoice;
  actual_cost?: number | null;
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
   * the server publishes `actual_cost: 0.0` for a donated order — and in JSX
   * a numeric `0` does not merely fail to render the row, it RENDERS: `{0 &&
   * <div/>}` prints a bare "0" into the card and drops the figure beside it.
   *
   * EVERY money row on this card asks it. `actual_cost` and `cost_per_unit`
   * still guarded on truthiness after the rows beside them were repaired, so a
   * comped order printed a stray "0" where its cost should have been — and the
   * server had only just started sending a real `0.0` there instead of `null`.
   */
  const isReported = (amount: number | null | undefined): amount is number =>
    amount !== null && amount !== undefined;

  /**
   * The one sentence every item-scoped figure on this page is labelled with.
   *
   * `ReorderRequest` records no supplier and no estimate, so the supplier name
   * and the price beside it are the ITEM's, resolved when this response was
   * built. A cost VARIANCE used to be rendered here too — `actual_cost` minus
   * that live quote, printed as "over budget" / "under budget" — and it is gone
   * rather than relabelled: editing a supplier link flipped a finished order
   * from one verdict to the other, because there was never a budget under it.
   */
  const ITEM_SCOPE_NOTE = "The item's supplier and price as of now — not this order's.";

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
  // to disagree. Off the SUMMARY rather than off row 0, because the two arrays
  // are built in one loop and so are empty together — an empty ledger left this
  // false and printed the "all financial information is made available" footer
  // to the very reader it is no longer true of. The rows keep the marker too;
  // they are still read when a row is what a surface has.
  const vendorWithheld = vendorDataWithheld(data.summary);

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
          {/* The table is a PAGE of the ledger, and the summary above it is
              not. Derived from the two numbers the page already holds rather
              than from a hard-coded 100, so it cannot disagree with the server
              about where the cut is. Rendered only when there IS a cut: a note
              about a truncation that did not happen is its own false claim. */}
          {data.ledger.length < data.summary.total_orders_with_financial_data && (
            <p className="section-subtitle" data-testid="ledger-window">
              Showing the {data.ledger.length} most recent of{' '}
              {data.summary.total_orders_with_financial_data}. The totals above
              cover all of them.
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
                      `item_supplier_choice` and the per-order costs, so these
                      columns would read "N/A" and "$NaN" on every row — "no
                      supplier on file" and a nonsense figure, both claims about
                      the DATA rather than about the reader. An absent column
                      cannot be misread as an empty value; the note above the
                      table says where the numbers went.

                      The supplier column is headed "Item supplier today" and
                      not "Supplier": what it names is the item's current
                      source, and this order never had one of its own. */}
                  {!vendorWithheld && <th>Item supplier today</th>}
                  {!vendorWithheld && <th>Paid</th>}
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
                    {/* The ITEM's supplier as of now, through the one shared
                        reading of that answer (`utils/supplierChoice`) so the
                        alternatives travel with the name. Never `entry.supplier_name`:
                        this order never had one. */}
                    {!vendorWithheld && (
                      <td>{supplierChoiceSummary(entry.item_supplier_choice) || 'N/A'}</td>
                    )}
                    {/* What was PAID, and only that. The column used to fall
                        through to a live re-quote when no actual was recorded,
                        which put a price nobody paid under a heading that said
                        we had. */}
                    {!vendorWithheld && <td>{formatCurrency(entry.actual_cost)}</td>}
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
                {supplierChoiceSummary(order.item_supplier_choice) && (
                  <div className="detail-row">
                    <span className="label">Item supplier today:</span>
                    <span className="value">
                      {supplierChoiceSummary(order.item_supplier_choice)}
                    </span>
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

              {/* WHAT THIS ORDER COST. Only figures the order itself records
                  belong in this block; the live re-quote sits below it, under
                  its own heading, so the two cannot be read as a budget and an
                  outturn. `isReported`, never truthiness, on BOTH rows: a
                  recorded $0.00 (a donation, a warranty replacement) is a known
                  cost, and `{0 && <div/>}` does not merely drop the row — it
                  renders a bare "0" into the card (op-9m2v). */}
              <div className="financial-info">
                {isReported(order.actual_cost) && (
                  <div className="detail-row">
                    <span className="label">Actual Cost:</span>
                    <span className="value">{formatCurrency(order.actual_cost)}</span>
                  </div>
                )}
                {isReported(order.cost_per_unit) && (
                  <div className="detail-row">
                    <span className="label">Cost per Unit:</span>
                    <span className="value">{formatCurrency(order.cost_per_unit)}</span>
                  </div>
                )}
              </div>

              {/* THE ITEM'S, AS OF NOW. Separated from the block above and
                  labelled, because the two used to sit in one list where a
                  reader could only take the second for this order's estimate —
                  which is precisely what the removed "Cost Variance" row did
                  for them, in words, with a verdict. */}
              {isReported(order.item_estimated_cost_today) && (
                <div className="item-price-today">
                  <div className="detail-row">
                    <span className="label">Same quantity at today's price:</span>
                    <span className="value">
                      {formatCurrency(order.item_estimated_cost_today)}
                    </span>
                  </div>
                  <p className="item-scope-note">{ITEM_SCOPE_NOTE}</p>
                </div>
              )}

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
        {/* "ALL financial information is made available" stopped being true
            for EVERY reader when op-anonymous-read-posture gated the vendor
            block: the signed-in branch went on making the claim about the page
            as a whole, which its own anonymous branch disproves. A page whose
            subject is accountability cannot carry a claim its payload denies,
            so both branches are worded for the reader they have — the same edit
            the server makes to `summary.transparency_note`. */}
        <p>
          This transparency page reflects our commitment to open operations.
          {vendorWithheld
            ? ' What the makerspace spends is published here; supplier names and ' +
              'per-order costs are shown to signed-in members.'
            : ' You are signed in, so supplier names and per-order costs are shown ' +
              'here as well; they are withheld from readers who are not.'}
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
