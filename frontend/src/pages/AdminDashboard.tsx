/**
 * Admin Dashboard
 * Manage reorder queue, view pending requests, and access supplier cart links
 */
import React, { useState, useEffect } from 'react';
import { reorderAPI } from '../services/api';
import { ReorderRequest } from '../types';
import '../styles/AdminDashboard.css';

const AdminDashboard: React.FC = () => {
  const [requests, setRequests] = useState<ReorderRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<'pending' | 'all'>('pending');
  const [supplierGroups, setSupplierGroups] = useState<any>(null);

  useEffect(() => {
    loadRequests();
  }, [filter]);

  const loadRequests = async () => {
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
  };

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
    try {
      await reorderAPI.markOrdered(id, { order_number: orderNumber || undefined });
      loadRequests();
      alert('Marked as ordered');
    } catch (err) {
      console.error('Error marking as ordered:', err);
      alert('Failed to mark as ordered');
    }
  };

  const handleMarkReceived = async (id: number) => {
    try {
      await reorderAPI.markReceived(id);
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
                          <button
                            onClick={() => handleMarkReceived(request.id)}
                            className="btn-receive"
                          >
                            Mark Received
                          </button>
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
    </div>
  );
};

export default AdminDashboard;
