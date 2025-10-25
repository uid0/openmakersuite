/**
 * QR Code Scan Page
 * Shows item details and allows users to submit reorder requests
 */
import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { inventoryAPI, reorderAPI } from '../services/api';
import { InventoryItem } from '../types';
import '../styles/ScanPage.css';

const ScanPage: React.FC = () => {
  const { itemId } = useParams<{ itemId: string }>();
  const navigate = useNavigate();

  const [item, setItem] = useState<InventoryItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [requestedBy, setRequestedBy] = useState('');
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  useEffect(() => {
    if (itemId) {
      loadItem();
    }
  }, [itemId]);

  const loadItem = async () => {
    try {
      setLoading(true);
      const response = await inventoryAPI.getItem(itemId!);
      setItem(response.data);
      setError(null);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to load item');
      console.error('Error loading item:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmitReorder = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!item) return;

    try {
      setSubmitting(true);
      await reorderAPI.createRequest({
        item: item.id,
        quantity: item.reorder_quantity,
        requested_by: requestedBy || 'Anonymous',
        request_notes: notes,
        priority: item.needs_reorder ? 'high' : 'normal',
      });

      setSubmitted(true);
      setTimeout(() => {
        navigate('/');
      }, 3000);
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Failed to submit reorder request');
      console.error('Error submitting reorder:', err);
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="scan-page">
        <div className="loading">Loading item details...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="scan-page">
        <div className="error">
          <h2>Error</h2>
          <p>{error}</p>
          <button onClick={() => navigate('/')}>Go Home</button>
        </div>
      </div>
    );
  }

  if (!item) {
    return (
      <div className="scan-page">
        <div className="error">
          <h2>Item not found</h2>
          <button onClick={() => navigate('/')}>Go Home</button>
        </div>
      </div>
    );
  }

  if (submitted) {
    return (
      <div className="scan-page">
        <div className="success">
          <h2>✓ Reorder Request Submitted</h2>
          <p>Your request for <strong>{item.name}</strong> has been submitted.</p>
          <p>An administrator will review and process it soon.</p>
          <p className="redirect-message">Redirecting to home...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="scan-page">
      <div className="item-card">
        <div className="item-header">
          {item.image && (
            <img src={item.image} alt={item.name} className="item-image" />
          )}
          <div className="item-title-section">
            <h1>{item.name}</h1>
            {item.sku && <p className="sku">SKU: {item.sku}</p>}
          </div>
        </div>

        <div className="item-details">
          <p className="description">{item.description}</p>

          <div className="info-grid">
            <div className="info-item">
              <span className="label">Location:</span>
              <span className="value">{item.location}</span>
            </div>

            <div className="info-item">
              <span className="label">Current Stock:</span>
              <span className={`value ${item.needs_reorder ? 'low-stock' : ''}`}>
                {item.current_stock} units
              </span>
            </div>

            <div className="info-item">
              <span className="label">Reorder Quantity:</span>
              <span className="value">{item.reorder_quantity} units</span>
            </div>

            <div className="info-item">
              <span className="label">Average Lead Time:</span>
              <span className="value">{item.average_lead_time} days</span>
            </div>

            {item.supplier_name && (
              <div className="info-item">
                <span className="label">Supplier:</span>
                <span className="value">{item.supplier_name}</span>
              </div>
            )}

            {item.unit_cost && (
              <div className="info-item">
                <span className="label">Unit Cost:</span>
                <span className="value">${item.unit_cost}</span>
              </div>
            )}
          </div>

          {item.needs_reorder && (
            <div className="alert alert-warning">
              <strong>⚠ Low Stock Alert</strong>
              <p>This item is below minimum stock level and needs reordering.</p>
            </div>
          )}
        </div>

        <form onSubmit={handleSubmitReorder} className="reorder-form">
          <h2>Request Reorder</h2>

          <div className="form-group">
            <label htmlFor="requestedBy">Your Name (Optional)</label>
            <input
              type="text"
              id="requestedBy"
              value={requestedBy}
              onChange={(e) => setRequestedBy(e.target.value)}
              placeholder="Enter your name"
            />
          </div>

          <div className="form-group">
            <label htmlFor="notes">Notes (Optional)</label>
            <textarea
              id="notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Any additional information..."
              rows={3}
            />
          </div>

          <button
            type="submit"
            className="submit-button"
            disabled={submitting}
          >
            {submitting ? 'Submitting...' : `Request ${item.reorder_quantity} Units`}
          </button>
        </form>
      </div>
    </div>
  );
};

export default ScanPage;
