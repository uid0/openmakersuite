/**
 * Admin Dashboard
 * Manage reorder queue, view pending requests, and access supplier cart links.
 *
 * Successful mutations patch the affected row from the API response — see
 * docs/REACTIVE_MUTATIONS.md. The full "Loading requests…" placeholder is
 * only shown during the initial fetch and filter switch, never after a
 * successful row mutation.
 */
import { Button, Group, Stack, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { modals } from '@mantine/modals';
import React, { useCallback, useEffect, useState } from 'react';
import WorkspacePage from '../components/landing/WorkspacePage';
import { assetsAPI, inventoryAPI, reorderAPI } from '../services/api';
import '../styles/AdminDashboard.css';
import { Asset, InventoryItem, ReorderRequest } from '../types';
import { formatDateOnly, parseYmd } from '../utils/dates';
import { promptInput, showError, showSuccess } from '../utils/dialogs';

interface UpdateTrackingValues {
  trackingNumber: string;
  carrier: string;
  expectedDeliveryDate: string;
  trackingUrl: string;
}

interface UpdateTrackingFormProps {
  modalId: string;
  onSubmit: (values: UpdateTrackingValues) => void;
}

const UpdateTrackingForm: React.FC<UpdateTrackingFormProps> = ({ modalId, onSubmit }) => {
  const form = useForm<UpdateTrackingValues>({
    initialValues: {
      trackingNumber: '',
      carrier: '',
      expectedDeliveryDate: '',
      trackingUrl: '',
    },
  });

  const handleSubmit = form.onSubmit((values) => {
    modals.close(modalId);
    onSubmit(values);
  });

  return (
    <form onSubmit={handleSubmit}>
      <Stack>
        <TextInput
          label="Tracking number"
          placeholder="Optional"
          {...form.getInputProps('trackingNumber')}
        />
        <TextInput
          label="Carrier / shipper"
          placeholder="Optional"
          {...form.getInputProps('carrier')}
        />
        <TextInput
          label="Expected delivery date"
          placeholder="YYYY-MM-DD (optional)"
          {...form.getInputProps('expectedDeliveryDate')}
        />
        <TextInput
          label="Tracking URL"
          placeholder="Optional"
          {...form.getInputProps('trackingUrl')}
        />
        <Group justify="flex-end">
          <Button variant="default" type="button" onClick={() => modals.close(modalId)}>
            Cancel
          </Button>
          <Button type="submit">Update Tracking</Button>
        </Group>
      </Stack>
    </form>
  );
};

const AdminDashboard: React.FC = () => {
  const [requests, setRequests] = useState<ReorderRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<'pending' | 'all'>('pending');
  const [supplierGroups, setSupplierGroups] = useState<any>(null);
  const [notCheckedInAssets, setNotCheckedInAssets] = useState<Asset[]>([]);
  const [loadingAssets, setLoadingAssets] = useState(false);
  const [assetStatusFilter, setAssetStatusFilter] = useState<string>('all');
  const [assetInventoryItemFilter, setAssetInventoryItemFilter] = useState<string | null>(null);
  const [inventoryItems, setInventoryItems] = useState<InventoryItem[]>([]);
  const [pendingRowIds, setPendingRowIds] = useState<Set<number>>(new Set());

  const loadRequests = useCallback(async () => {
    try {
      setLoading(true);
      if (filter === 'pending') {
        const response = await reorderAPI.getPendingRequests();
        setRequests(response.data);
      } else {
        const response = await reorderAPI.listRequests();
        setRequests(response.data.results);
      }
    } catch (err) {
      console.error('Error loading requests:', err);
      showError('Failed to load requests. Please log in.');
    } finally {
      setLoading(false);
    }
  }, [filter]);

  const loadNotCheckedInAssets = useCallback(async () => {
    try {
      setLoadingAssets(true);
      const params: { status?: string; inventory_item?: string } = {};
      if (assetStatusFilter !== 'all') {
        params.status = assetStatusFilter;
      }
      if (assetInventoryItemFilter) {
        params.inventory_item = assetInventoryItemFilter;
      }
      const response = await assetsAPI.getNotCheckedIn(params);
      setNotCheckedInAssets(response.data);
    } catch (err) {
      console.error('Error loading assets not checked in:', err);
    } finally {
      setLoadingAssets(false);
    }
  }, [assetStatusFilter, assetInventoryItemFilter]);

  useEffect(() => {
    loadRequests();
  }, [loadRequests]);

  useEffect(() => {
    loadNotCheckedInAssets();
  }, [loadNotCheckedInAssets]);

  useEffect(() => {
    // Load inventory items that have assets for the filter dropdown
    const loadInventoryItems = async () => {
      try {
        const response = await inventoryAPI.listItems();
        setInventoryItems(response.data.results);
      } catch (err) {
        console.error('Error loading inventory items:', err);
      }
    };
    loadInventoryItems();
  }, []);

  const loadSupplierGroups = async () => {
    try {
      const response = await reorderAPI.getBySupplier();
      setSupplierGroups(response.data);
    } catch (err) {
      console.error('Error loading supplier groups:', err);
    }
  };

  // Patch a single request row in place using the response from the mutation.
  // Falls back to a partial merge if the response omits fields (e.g. when a
  // test mock returns {}), so the existing row never disappears.
  const applyRequestUpdate = (id: number, updated: Partial<ReorderRequest> | undefined) => {
    if (!updated || typeof updated !== 'object') return;
    setRequests((rs) =>
      rs.map((r) =>
        r.id === id
          ? { ...r, ...(updated as Partial<ReorderRequest>), id: r.id }
          : r,
      ),
    );
  };

  // Run a row-scoped mutation: marks the row pending, prevents duplicate
  // submits for the same row, patches local state from the response on
  // success, and shows a scoped error notification on failure (the row
  // remains visible and unchanged so the user can retry).
  const runRowMutation = async <T extends Partial<ReorderRequest>>(
    id: number,
    op: () => Promise<{ data: T }>,
    successMessage: string,
    errorMessage: string,
  ): Promise<void> => {
    if (pendingRowIds.has(id)) return;
    setPendingRowIds((prev) => {
      const next = new Set(prev);
      next.add(id);
      return next;
    });
    try {
      const response = await op();
      applyRequestUpdate(id, response.data);
      showSuccess(successMessage);
    } catch (err) {
      console.error(`${errorMessage}:`, err);
      showError(errorMessage);
    } finally {
      setPendingRowIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  };

  const isRowPending = (id: number) => pendingRowIds.has(id);

  const handleApprove = (id: number) => {
    void runRowMutation(
      id,
      () => reorderAPI.approveRequest(id) as Promise<{ data: Partial<ReorderRequest> }>,
      'Request approved',
      'Failed to approve request',
    );
  };

  // Marking ordered is a one-click action. The order/PO number belongs to the
  // Purchase Order domain and its send/confirm lifecycle — it is carried onto
  // the request automatically when a PO is created/finalized, so we do not make
  // the operator re-type it here. Estimated delivery / tracking can still be
  // added afterwards via the "Update Tracking" action on ordered rows.
  const handleMarkOrdered = (id: number) => {
    if (isRowPending(id)) return;
    void runRowMutation(
      id,
      () =>
        reorderAPI.markOrdered(id) as Promise<{
          data: Partial<ReorderRequest>;
        }>,
      'Marked as ordered',
      'Failed to mark as ordered',
    );
  };

  const handleMarkReceived = (id: number) => {
    if (isRowPending(id)) return;
    promptInput(
      'Mark as Received',
      'Actual delivery date (YYYY-MM-DD, optional — defaults to today)',
      (actualDeliveryStr) => {
        void runRowMutation(
          id,
          () =>
            reorderAPI.markReceived(id, actualDeliveryStr || undefined) as Promise<{
              data: Partial<ReorderRequest>;
            }>,
          'Marked as received and inventory updated',
          'Failed to mark as received',
        );
      },
    );
  };

  const handleCancel = (id: number) => {
    if (isRowPending(id)) return;
    promptInput('Cancel Request', 'Reason for cancellation', (notes) => {
      void runRowMutation(
        id,
        () =>
          reorderAPI.cancelRequest(id, notes) as Promise<{
            data: Partial<ReorderRequest>;
          }>,
        'Request cancelled',
        'Failed to cancel request',
      );
    });
  };

  const handleUpdateTracking = (id: number) => {
    if (isRowPending(id)) return;
    const modalId = `update-tracking-${id}-${Date.now()}`;
    modals.open({
      modalId,
      title: 'Update Tracking',
      children: (
        <UpdateTrackingForm
          modalId={modalId}
          onSubmit={(values) => {
            if (
              !values.trackingNumber &&
              !values.carrier &&
              !values.expectedDeliveryDate &&
              !values.trackingUrl
            ) {
              return;
            }

            const data: any = {};
            if (values.trackingNumber) data.tracking_number = values.trackingNumber;
            if (values.carrier) data.carrier = values.carrier;
            if (values.expectedDeliveryDate)
              data.expected_delivery_date = values.expectedDeliveryDate;
            if (values.trackingUrl) data.delivery_tracking_url = values.trackingUrl;

            void runRowMutation(
              id,
              () =>
                reorderAPI.updateTracking(id, data) as Promise<{
                  data: Partial<ReorderRequest>;
                }>,
              'Tracking information updated',
              'Failed to update tracking information',
            );
          }}
        />
      ),
    });
  };

  const getPriorityClass = (priority: string) => {
    const map: Record<string, string> = {
      urgent: 'priority-urgent',
      high: 'priority-high',
      normal: 'priority-normal',
      low: 'priority-low',
    };
    return map[priority] || '';
  };

  const getStatusClass = (status: string) => {
    const map: Record<string, string> = {
      pending: 'status-pending',
      approved: 'status-approved',
      ordered: 'status-ordered',
      received: 'status-received',
      cancelled: 'status-cancelled',
    };
    return map[status] || '';
  };

  return (
    <WorkspacePage
      testId="admin-dashboard"
      hero={{
        eyebrow: 'Inventory · Admin',
        title: 'Admin dashboard',
        description: 'Reorder queue, supplier groups, and pending requests.',
        action: (
          <Button onClick={loadSupplierGroups} variant="default">
            View by supplier
          </Button>
        ),
      }}
    >
      <div className="admin-dashboard">

      <div className="filter-bar">
        <button
          className={filter === 'pending' ? 'active' : ''}
          onClick={() => setFilter('pending')}
        >
          Pending ({requests.filter(r => r.status === 'pending').length})
        </button>
        <button
          className={filter === 'all' ? 'active' : ''}
          onClick={() => setFilter('all')}
        >
          All Requests
        </button>
      </div>

      {loading ? (
        <div className="loading">Loading requests...</div>
      ) : (
        <div className="requests-table">
          <table>
            <thead>
              <tr>
                <th>Item</th>
                <th>Quantity</th>
                <th>Priority</th>
                <th>Status</th>
                <th>Requested By</th>
                <th>Requested</th>
                <th>Est. Cost</th>
                <th>Lead Time</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {requests.length === 0 ? (
                <tr>
                  <td colSpan={9} className="no-data">
                    No requests found
                  </td>
                </tr>
              ) : (
                requests.map((request) => {
                  const rowPending = isRowPending(request.id);
                  return (
                  <tr
                    key={request.id}
                    data-testid={`reorder-row-${request.id}`}
                    aria-busy={rowPending ? 'true' : undefined}
                    className={rowPending ? 'row-pending' : undefined}
                  >
                    <td>
                      <div className="item-cell">
                        {request.item_details.thumbnail && (
                          <img
                            src={request.item_details.thumbnail}
                            alt={request.item_details.name}
                            className="item-thumb"
                          />
                        )}
                        <div>
                          <div className="item-name">{request.item_details.name}</div>
                          {request.item_details.supplier_name && (
                            <div className="item-supplier">
                              {request.item_details.supplier_name}
                            </div>
                          )}

                          {/* Order tracking information */}
                          {request.order_number && (
                            <div className="tracking-info">
                              <small>📋 Order: {request.order_number}</small>
                            </div>
                          )}

                          {request.estimated_delivery && (
                            <div className="delivery-info">
                              <small>
                                📅 Expected: {formatDateOnly(request.estimated_delivery, undefined, '')}
                              </small>
                            </div>
                          )}

                          {request.actual_delivery && (
                            <div className="delivery-info">
                              <small>
                                ✅ Delivered: {formatDateOnly(request.actual_delivery, undefined, '')}
                              </small>
                            </div>
                          )}

                          {request.status === 'ordered' && request.estimated_delivery && (
                            <div className="delivery-status">
                              <small>
                                {(() => {
                                  const estimatedDate =
                                    parseYmd(request.estimated_delivery) ??
                                    new Date(request.estimated_delivery);
                                  const now = new Date();
                                  const today = new Date(
                                    now.getFullYear(),
                                    now.getMonth(),
                                    now.getDate(),
                                  );
                                  const diffTime = estimatedDate.getTime() - today.getTime();
                                  const diffDays = Math.round(diffTime / (1000 * 60 * 60 * 24));

                                  if (diffDays < 0) {
                                    return <span className="overdue">⚠️ {Math.abs(diffDays)} days overdue</span>;
                                  } else if (diffDays === 0) {
                                    return <span className="due-today">🚚 Expected today</span>;
                                  } else if (diffDays === 1) {
                                    return <span className="due-soon">📦 Expected tomorrow</span>;
                                  } else {
                                    return <span className="due-later">📅 {diffDays} days to go</span>;
                                  }
                                })()}
                              </small>
                            </div>
                          )}
                        </div>
                      </div>
                    </td>
                    <td>{request.quantity}</td>
                    <td>
                      <span className={`priority-badge ${getPriorityClass(request.priority)}`}>
                        {request.priority}
                      </span>
                    </td>
                    <td>
                      <span className={`status-badge ${getStatusClass(request.status)}`}>
                        {request.status}
                      </span>
                    </td>
                    <td>{request.requested_by || 'Anonymous'}</td>
                    <td>{new Date(request.requested_at).toLocaleDateString()}</td>
                    <td>
                      {request.estimated_cost
                        ? `$${parseFloat(request.estimated_cost).toFixed(2)}`
                        : '-'}
                    </td>
                    <td>{request.item_details.average_lead_time} days</td>
                    <td>
                      <div className="action-buttons">
                        {request.status === 'pending' && (
                          <>
                            <button
                              onClick={() => handleApprove(request.id)}
                              className="btn-approve"
                              title="Approve"
                              aria-label="Approve"
                              disabled={rowPending}
                            >
                              ✓
                            </button>
                            <button
                              onClick={() => handleCancel(request.id)}
                              className="btn-cancel"
                              title="Cancel"
                              aria-label="Cancel"
                              disabled={rowPending}
                            >
                              ✗
                            </button>
                          </>
                        )}
                        {request.status === 'approved' && (
                          <button
                            onClick={() => handleMarkOrdered(request.id)}
                            className="btn-order"
                            disabled={rowPending}
                          >
                            Mark Ordered
                          </button>
                        )}
                        {request.status === 'ordered' && (
                          <>
                            <button
                              onClick={() => handleUpdateTracking(request.id)}
                              className="btn-tracking"
                              title="Update Tracking"
                              disabled={rowPending}
                            >
                              📦
                            </button>
                            <button
                              onClick={() => handleMarkReceived(request.id)}
                              className="btn-receive"
                              disabled={rowPending}
                            >
                              Mark Received
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      )}

      {supplierGroups && (
        <div className="supplier-modal">
          <div className="modal-content">
            <div className="modal-header">
              <h2>Requests by Supplier</h2>
              <button onClick={() => setSupplierGroups(null)} className="close-btn">
                ✗
              </button>
            </div>
            <div className="modal-body">
              {supplierGroups.map((group: any) => (
                <div key={group.supplier} className="supplier-group">
                  <h3>{group.supplier}</h3>
                  <p>
                    {group.item_count} items - Est. Total: $
                    {group.total_estimated_cost.toFixed(2)}
                  </p>
                  <ul>
                    {group.requests.map((req: any) => (
                      <li key={req.id}>
                        {req.item_details.name} - Qty: {req.quantity}
                        {req.item_details.supplier_url && (
                          <a
                            href={req.item_details.supplier_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="supplier-link"
                          >
                            View on {group.supplier}
                          </a>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Assets Not Checked In Section */}
      <div className="assets-section">
        <h2>Assets Not Checked In (3+ Months)</h2>

        {/* Asset Filters */}
        <div className="asset-filters" style={{ marginBottom: '1rem', display: 'flex', gap: '1rem', flexWrap: 'wrap', alignItems: 'center' }}>
          <div className="filter-group">
            <label htmlFor="asset-status-filter" style={{ marginRight: '0.5rem' }}>Status:</label>
            <select
              id="asset-status-filter"
              value={assetStatusFilter}
              onChange={(e) => setAssetStatusFilter(e.target.value)}
              style={{ padding: '0.5rem', borderRadius: '4px', border: '1px solid #ccc' }}
            >
              <option value="all">All Statuses</option>
              <option value="implementing">Implementing</option>
              <option value="testing">Testing</option>
              <option value="active">Active</option>
              <option value="maintenance">Maintenance</option>
              <option value="retired">Retired</option>
              <option value="lost">Lost</option>
              <option value="donated_out">Donated Out</option>
            </select>
          </div>

          <div className="filter-group">
            <label htmlFor="asset-inventory-item-filter" style={{ marginRight: '0.5rem' }}>Inventory Item:</label>
            <select
              id="asset-inventory-item-filter"
              value={assetInventoryItemFilter || ''}
              onChange={(e) => setAssetInventoryItemFilter(e.target.value || null)}
              style={{ padding: '0.5rem', borderRadius: '4px', border: '1px solid #ccc', minWidth: '200px' }}
            >
              <option value="">All Items</option>
              {inventoryItems.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </div>
        </div>

        {loadingAssets ? (
          <div className="loading">Loading assets...</div>
        ) : (
          <>
            {notCheckedInAssets.length === 0 ? (
              <p className="no-data">All assets have been checked in recently.</p>
            ) : (
              <div className="assets-table">
                <table>
                  <thead>
                    <tr>
                      <th>Asset Name</th>
                      <th>Asset Tag</th>
                      <th>Location</th>
                      <th>Last Scanned</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {notCheckedInAssets.map((asset) => (
                      <tr key={asset.id}>
                        <td>{asset.name}</td>
                        <td>{asset.asset_tag || '—'}</td>
                        <td>{asset.location_name || '—'}</td>
                        <td>
                          {asset.last_scanned_at
                            ? new Date(asset.last_scanned_at).toLocaleDateString()
                            : 'Never'}
                        </td>
                        <td>
                          <span className={`status-badge status-${asset.status}`}>
                            {asset.status}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
      </div>
    </WorkspacePage>
  );
};

export default AdminDashboard;
