/**
 * Public Purchase Order List Page
 * Shows all active and settled purchase orders for transparency
 */
import { Button, Group, Paper, Text } from '@mantine/core';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import { purchaseOrderAPI } from '../services/api';
import '../styles/PurchaseOrderListPage.css';
import { formatDateOnly } from '../utils/dates';
import { extractErrorMessage } from '../utils/extractErrorMessage';

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

type SortDirection = 'asc' | 'desc';
type SortType = 'string' | 'number' | 'date';

interface SortableColumn {
  key: string;
  label: string;
  type: SortType;
  getValue: (order: PurchaseOrder) => string | number | null;
}

// Sortable header cells, in display order. The non-sortable "Actions" column is
// rendered separately after this list. The list loads every order up front, so
// sorting happens entirely client-side over the in-memory array.
const SORTABLE_COLUMNS: SortableColumn[] = [
  { key: 'po_number', label: 'PO Number', type: 'string', getValue: (order) => order.po_number },
  { key: 'supplier', label: 'Supplier', type: 'string', getValue: (order) => order.supplier_details },
  { key: 'status', label: 'Status', type: 'string', getValue: (order) => order.status_label },
  { key: 'order_date', label: 'Order Date', type: 'date', getValue: (order) => order.order_date },
  {
    key: 'expected_delivery',
    label: 'Expected Delivery',
    type: 'date',
    getValue: (order) => order.expected_delivery_date,
  },
  { key: 'items', label: 'Items', type: 'number', getValue: (order) => order.total_items },
  {
    key: 'estimated_total',
    label: 'Estimated Total',
    type: 'number',
    getValue: (order) => order.estimated_total,
  },
  { key: 'actual_total', label: 'Actual Total', type: 'number', getValue: (order) => order.actual_total },
];

// Normalise a column's raw value for comparison. Missing values (null/empty
// string) and unparseable numbers/dates collapse to null so they can always be
// ordered last, regardless of sort direction.
const toComparable = (order: PurchaseOrder, column: SortableColumn): number | string | null => {
  const raw = column.getValue(order);
  if (raw === null || raw === undefined || raw === '') return null;
  if (column.type === 'number') {
    const num = parseFloat(String(raw));
    return Number.isNaN(num) ? null : num;
  }
  if (column.type === 'date') {
    const time = new Date(String(raw)).getTime();
    return Number.isNaN(time) ? null : time;
  }
  return String(raw);
};

// Compare two orders by a single column + direction. Empty values always sort
// last so blank rows never jump to the top when the direction is reversed.
const compareByColumn = (
  a: PurchaseOrder,
  b: PurchaseOrder,
  column: SortableColumn,
  direction: SortDirection
): number => {
  const va = toComparable(a, column);
  const vb = toComparable(b, column);
  if (va === null && vb === null) return 0;
  if (va === null) return 1;
  if (vb === null) return -1;
  let result: number;
  if (typeof va === 'number' && typeof vb === 'number') {
    result = va - vb;
  } else {
    result = String(va).localeCompare(String(vb));
  }
  return direction === 'asc' ? result : -result;
};

const PurchaseOrderListPage: React.FC = () => {
  const [orders, setOrders] = useState<PurchaseOrder[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>('');
  // Default to newest-first by order date; click a header to re-sort.
  const [sortColumn, setSortColumn] = useState<string>('order_date');
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc');

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
      setError(err.response?.data?.error || extractErrorMessage(err, '') || err.message || 'Failed to load purchase orders');
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    loadOrders();
  }, [loadOrders]);

  const formatDate = (dateString: string | null) => {
    return formatDateOnly(dateString, {
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

  // Clicking the active column toggles direction; a new column starts ascending.
  const handleSort = (key: string) => {
    if (sortColumn === key) {
      setSortDirection((prev) => (prev === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortColumn(key);
      setSortDirection('asc');
    }
  };

  // Sort a copy of the loaded orders; never mutate state or refetch.
  const sortedOrders = useMemo(() => {
    const column = SORTABLE_COLUMNS.find((c) => c.key === sortColumn);
    if (!column) return orders;
    return [...orders].sort((a, b) => compareByColumn(a, b, column, sortDirection));
  }, [orders, sortColumn, sortDirection]);

  return (
    <WorkspacePage
      testId="purchase-order-list-page"
      hero={{
        eyebrow: 'Purchasing',
        title: 'Purchase orders',
        description: 'Active and settled purchase orders for makerspace transparency.',
        action: (
          <Group gap="sm">
            <Button component={Link} to="/inventory/transparency" variant="default">
              Financial transparency
            </Button>
            <Button component={Link} to="/purchasing/orders/new">
              Create purchase order
            </Button>
          </Group>
        ),
      }}
    >
      {error && (
        <Paper withBorder p="md" radius="md" bg="red.0" c="red.9">
          <Text>{error}</Text>
        </Paper>
      )}

      {loading && (
        <Paper withBorder p="md">
          <Text c="dimmed">Loading purchase orders…</Text>
        </Paper>
      )}

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
                {SORTABLE_COLUMNS.map((column) => {
                  const active = sortColumn === column.key;
                  const ariaSort: 'ascending' | 'descending' | 'none' = active
                    ? sortDirection === 'asc'
                      ? 'ascending'
                      : 'descending'
                    : 'none';
                  return (
                    <th key={column.key} scope="col" aria-sort={ariaSort}>
                      <button
                        type="button"
                        className="po-sort-header"
                        onClick={() => handleSort(column.key)}
                      >
                        <span>{column.label}</span>
                        <span
                          className={`po-sort-indicator${
                            active ? '' : ' po-sort-indicator-inactive'
                          }`}
                          aria-hidden="true"
                        >
                          {active ? (sortDirection === 'asc' ? '▲' : '▼') : '↕'}
                        </span>
                      </button>
                    </th>
                  );
                })}
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {sortedOrders.map((order) => (
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
    </WorkspacePage>
  );
};

export default PurchaseOrderListPage;

