/**
 * Admin Dashboard
 * Manage reorder queue, view pending requests, and access supplier cart links
 */
import { Button, Group, Stack, TextInput } from '@mantine/core';
import { DateInput } from '@mantine/dates';
import { useForm } from '@mantine/form';
import { modals } from '@mantine/modals';
import dayjs from 'dayjs';
import React, { useCallback, useEffect, useState } from 'react';
import { assetsAPI, inventoryAPI, reorderAPI } from '../services/api';
import '../styles/AdminDashboard.css';
import { Asset, InventoryItem, ReorderRequest } from '../types';
import { promptInput, showError, showSuccess } from '../utils/dialogs';

interface MarkOrderedValues {
  orderNumber: string;
  estimatedDelivery: Date | null;
  actualCost: string;
}

interface MarkOrderedFormProps {
  modalId: string;
  onSubmit: (values: MarkOrderedValues) => void;
}

const MarkOrderedForm: React.FC<MarkOrderedFormProps> = ({ modalId, onSubmit }) => {
  const form = useForm<MarkOrderedValues>({
    initialValues: { orderNumber: '', estimatedDelivery: null, actualCost: '' },
  });

  const handleSubmit = form.onSubmit((values) => {
    modals.close(modalId);
    onSubmit(values);
  });

  return (
    <form onSubmit={handleSubmit}>
      <Stack>
        <TextInput
          label="Order number"
          placeholder="Optional"
          {...form.getInputProps('orderNumber')}
        />
        <DateInput
          label="Estimated delivery date"
          placeholder="Select date (optional)"
          valueFormat="YYYY-MM-DD"
          clearable
          {...form.getInputProps('estimatedDelivery')}
        />
        <TextInput
          label="Actual cost"
          placeholder="Optional"
          {...form.getInputProps('actualCost')}
        />
        <Group justify="flex-end">
          <Button variant="default" type="button" onClick={() => modals.close(modalId)}>
            Cancel
          </Button>
          <Button type="submit">Mark Ordered</Button>
        </Group>
      </Stack>
    </form>
  );
};

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

  const handleApprove = async (id: number) => {
    try {
      await reorderAPI.approveRequest(id);
      loadRequests();
      showSuccess('Request approved');
    } catch (err) {
      console.error('Error approving request:', err);
      showError('Failed to approve request');
    }
  };

  const handleMarkOrdered = (id: number) => {
    const modalId = `mark-ordered-${id}-${Date.now()}`;
    modals.open({
      modalId,
      title: 'Mark as Ordered',
      children: (
        <MarkOrderedForm
          modalId={modalId}
          onSubmit={async (values) => {
            try {
              const data: any = {};
              if (values.orderNumber) data.order_number = values.orderNumber;
              if (values.estimatedDelivery) {
                data.estimated_delivery = dayjs(values.estimatedDelivery).format('YYYY-MM-DD');
              }
              if (values.actualCost) data.actual_cost = parseFloat(values.actualCost);

              await reorderAPI.markOrdered(id, data);
              loadRequests();
              showSuccess('Marked as ordered with tracking information');
            } catch (err) {
              console.error('Error marking as ordered:', err);
              showError('Failed to mark as ordered');
            }
          }}
        />
      ),
    });
  };

  const handleMarkReceived = (id: number) => {
    promptInput(
      'Mark as Received',
      'Actual delivery date (YYYY-MM-DD, optional — defaults to today)',
      async (actualDeliveryStr) => {
        try {
          await reorderAPI.markReceived(id, actualDeliveryStr || undefined);
          loadRequests();
          showSuccess('Marked as received and inventory updated');
        } catch (err) {
          console.error('Error marking as received:', err);
          showError('Failed to mark as received');
        }
      },
    );
  };

  const handleCancel = (id: number) => {
    promptInput('Cancel Request', 'Reason for cancellation', async (notes) => {
      try {
        await reorderAPI.cancelRequest(id, notes);
        loadRequests();
        showSuccess('Request cancelled');
      } catch (err) {
        console.error('Error cancelling request:', err);
        showError('Failed to cancel request');
      }
    });
  };

  const handleUpdateTracking = (id: number) => {
    const modalId = `update-tracking-${id}-${Date.now()}`;
    modals.open({
      modalId,
      title: 'Update Tracking',
      children: (
        <UpdateTrackingForm
          modalId={modalId}
          onSubmit={async (values) => {
            if (
              !values.trackingNumber &&
              !values.carrier &&
              !values.expectedDeliveryDate &&
              !values.trackingUrl
            ) {
              return;
            }

            try {
              const data: any = {};
              if (values.trackingNumber) data.tracking_number = values.trackingNumber;
              if (values.carrier) data.carrier = values.carrier;
              if (values.expectedDeliveryDate)
                data.expected_delivery_date = values.expectedDeliveryDate;
              if (values.trackingUrl) data.delivery_tracking_url = values.trackingUrl;

              await reorderAPI.updateTracking(id, data);
              loadRequests();
              showSuccess('Tracking information updated');
            } catch (err) {
              console.error('Error updating tracking:', err);
              showError('Failed to update tracking information');
            }
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
    <div className="admin-dashboard">
      <header className="dashboard-header">
        <h1>Admin Dashboard</h1>
        <div className="header-actions">
          <button onClick={loadSupplierGroups} className="btn-secondary">
            View by Supplier
          </button>
        </div>
      </header>

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
                requests.map((request) => (
                  <tr key={request.id}>
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
                                📅 Expected: {new Date(request.estimated_delivery).toLocaleDateString()}
                              </small>
                            </div>
                          )}

                          {request.actual_delivery && (
                            <div className="delivery-info">
                              <small>
                                ✅ Delivered: {new Date(request.actual_delivery).toLocaleDateString()}
                              </small>
                            </div>
                          )}

                          {request.status === 'ordered' && request.estimated_delivery && (
                            <div className="delivery-status">
                              <small>
                                {(() => {
                                  const estimatedDate = new Date(request.estimated_delivery);
                                  const today = new Date();
                                  const diffTime = estimatedDate.getTime() - today.getTime();
                                  const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));

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
                            >
                              ✓
                            </button>
                            <button
                              onClick={() => handleCancel(request.id)}
                              className="btn-cancel"
                              title="Cancel"
                            >
                              ✗
                            </button>
                          </>
                        )}
                        {request.status === 'approved' && (
                          <button
                            onClick={() => handleMarkOrdered(request.id)}
                            className="btn-order"
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
                            >
                              📦
                            </button>
                            <button
                              onClick={() => handleMarkReceived(request.id)}
                              className="btn-receive"
                            >
                              Mark Received
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))
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
  );
};

export default AdminDashboard;
