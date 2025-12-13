/**
 * Admin Dashboard
 * Manage reorder queue, view pending requests, and access supplier cart links
 */
import React, { useState, useEffect, useCallback } from 'react';
import { assetsAPI, reorderAPI } from '../services/api';
import { Asset, ReorderRequest } from '../types';
import '../styles/AdminDashboard.css';

const AdminDashboard: React.FC = () => {
  const [requests, setRequests] = useState<ReorderRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<'pending' | 'all'>('pending');
  const [supplierGroups, setSupplierGroups] = useState<any>(null);
  const [notCheckedInAssets, setNotCheckedInAssets] = useState<Asset[]>([]);
  const [loadingAssets, setLoadingAssets] = useState(false);

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
      alert('Failed to load requests. Please log in.');
    } finally {
      setLoading(false);
    }
  }, [filter]);

  const loadNotCheckedInAssets = useCallback(async () => {
    try {
      setLoadingAssets(true);
      const response = await assetsAPI.getNotCheckedIn();
      setNotCheckedInAssets(response.data);
    } catch (err) {
      console.error('Error loading assets not checked in:', err);
    } finally {
      setLoadingAssets(false);
    }
  }, []);

  useEffect(() => {
    loadRequests();
    loadNotCheckedInAssets();
  }, [loadRequests, loadNotCheckedInAssets]);

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
      alert('Request approved');
    } catch (err) {
      console.error('Error approving request:', err);
      alert('Failed to approve request');
    }
  };

  const handleMarkOrdered = async (id: number) => {
    const orderNumber = prompt('Enter order number (optional):');
    const estimatedDeliveryStr = prompt('Enter estimated delivery date (YYYY-MM-DD, optional):');
    const actualCostStr = prompt('Enter actual cost (optional):');

    try {
      const data: any = {};
      if (orderNumber) data.order_number = orderNumber;
      if (estimatedDeliveryStr) data.estimated_delivery = estimatedDeliveryStr;
      if (actualCostStr) data.actual_cost = parseFloat(actualCostStr);

      await reorderAPI.markOrdered(id, data);
      loadRequests();
      alert('Marked as ordered with tracking information');
    } catch (err) {
      console.error('Error marking as ordered:', err);
      alert('Failed to mark as ordered');
    }
  };

  const handleMarkReceived = async (id: number) => {
    const actualDeliveryStr = prompt('Enter actual delivery date (YYYY-MM-DD, optional - defaults to today):');
    try {
      await reorderAPI.markReceived(id, actualDeliveryStr || undefined);
      loadRequests();
      alert('Marked as received and inventory updated');
    } catch (err) {
      console.error('Error marking as received:', err);
      alert('Failed to mark as received');
    }
  };

  const handleCancel = async (id: number) => {
    const notes = prompt('Reason for cancellation:');
    if (notes === null) return;

    try {
      await reorderAPI.cancelRequest(id, notes);
      loadRequests();
      alert('Request cancelled');
    } catch (err) {
      console.error('Error cancelling request:', err);
      alert('Failed to cancel request');
    }
  };

  const handleUpdateTracking = async (id: number) => {
    const trackingNumber = prompt('Enter tracking number (optional):');
    const carrier = prompt('Enter carrier/shipper (optional):');
    const expectedDeliveryStr = prompt('Update expected delivery date (YYYY-MM-DD, optional):');
    const trackingUrl = prompt('Enter tracking URL (optional):');

    // If user cancels all prompts, don't proceed
    if (trackingNumber === null && carrier === null && expectedDeliveryStr === null && trackingUrl === null) {
      return;
    }

    try {
      const data: any = {};
      if (trackingNumber) data.tracking_number = trackingNumber;
      if (carrier) data.carrier = carrier;
      if (expectedDeliveryStr) data.expected_delivery_date = expectedDeliveryStr;
      if (trackingUrl) data.delivery_tracking_url = trackingUrl;

      await reorderAPI.updateTracking(id, data);
      loadRequests();
      alert('Tracking information updated');
    } catch (err) {
      console.error('Error updating tracking:', err);
      alert('Failed to update tracking information');
    }
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
