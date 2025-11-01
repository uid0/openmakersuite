/**
 * QR Code Scan Page
 * Shows item details and allows users to submit reorder requests
 * - Non-logged users: Simple reorder → thanks page
 * - Logged users: Supplier selection with cost optimization
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { inventoryAPI, reorderAPI } from '../services/api';
import '../styles/ScanPage.css';
import { InventoryItem, ItemSupplier } from '../types';

const ScanPage: React.FC = () => {
  const { itemId } = useParams<{ itemId: string }>();
  const navigate = useNavigate();

  // Authentication state
  const [isLoggedIn] = useState<boolean>(() => !!localStorage.getItem('token'));

  // Data state
  const [item, setItem] = useState<InventoryItem | null>(null);
  const [suppliers, setSuppliers] = useState<ItemSupplier[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Form state
  const [requestedBy, setRequestedBy] = useState('');
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  // Enhanced form state (logged in users)
  const [selectedSupplier, setSelectedSupplier] = useState<ItemSupplier | null>(null);
  const [packageQuantity, setPackageQuantity] = useState<number>(1);
  const [totalUnits, setTotalUnits] = useState<number>(0);
  const [estimatedCost, setEstimatedCost] = useState<number>(0);
  const [estimatedLeadTime, setEstimatedLeadTime] = useState<number>(0);

  const loadItem = useCallback(async () => {
    try {
      setLoading(true);
      const itemResponse = await inventoryAPI.getItem(itemId!);
      setItem(itemResponse.data);

      // Load supplier data for logged in users
      if (isLoggedIn) {
        const suppliersResponse = await inventoryAPI.getItemSuppliers(itemId!);
        const supplierList = suppliersResponse.data.results;
        setSuppliers(supplierList);

        // Find the most cost-effective supplier (lowest unit cost)
        if (supplierList.length > 0) {
          const bestSupplier = supplierList
            .filter(s => s.is_active && s.unit_cost)
            .sort((a, b) => parseFloat(a.unit_cost!) - parseFloat(b.unit_cost!))[0];
          
          if (bestSupplier) {
            setSelectedSupplier(bestSupplier);
            setPackageQuantity(1);
            updateCalculations(bestSupplier, 1);
          }
        }
      }

      setError(null);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to load item');
      console.error('Error loading item:', err);
    } finally {
      setLoading(false);
    }
  }, [itemId, isLoggedIn]);

  useEffect(() => {
    if (itemId) {
      loadItem();
    }
  }, [itemId, loadItem]);

  // Auto-submit reorder for non-logged users (only if no pending request exists)
  useEffect(() => {
    const autoSubmitReorder = async () => {
      if (!isLoggedIn && item && !submitting && !submitted) {
        // Check if item already has a pending reorder request
        if (item.has_pending_reorder) {
          // Don't auto-submit, just set submitted to show the existing request message
          setSubmitted(true);
          return;
        }

        try {
          setSubmitting(true);
          await reorderAPI.createRequest({
            item: item.id,
            quantity: item.reorder_quantity,
            requested_by: 'Anonymous',
            request_notes: 'Auto-submitted via QR scan',
            priority: item.needs_reorder ? 'high' : 'normal',
          });

          // Redirect to thanks page immediately
          navigate('/thanks');
        } catch (err: any) {
          console.error('Error auto-submitting reorder:', err);
          // On error, show the form so user can manually submit
          setSubmitting(false);
        }
      }
    };

    autoSubmitReorder();
  }, [isLoggedIn, item, submitting, submitted, navigate]);

  // Update calculations when supplier or quantity changes
  const updateCalculations = useCallback((supplier: ItemSupplier, packages: number) => {
    const units = packages * supplier.quantity_per_package;
    const cost = packages * parseFloat(supplier.package_cost || '0');
    
    setTotalUnits(units);
    setEstimatedCost(cost);
    setEstimatedLeadTime(supplier.average_lead_time);
  }, []);

  // Handle supplier selection change
  const handleSupplierChange = (supplierId: number) => {
    const supplier = suppliers.find(s => s.id === supplierId);
    if (supplier) {
      setSelectedSupplier(supplier);
      updateCalculations(supplier, packageQuantity);
    }
  };

  // Handle package quantity change
  const handlePackageQuantityChange = (quantity: number) => {
    setPackageQuantity(quantity);
    if (selectedSupplier) {
      updateCalculations(selectedSupplier, quantity);
    }
  };

  const handleSubmitReorder = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!item || !isLoggedIn) return;

    // Logged in user: Use supplier selection and package quantities
    if (!selectedSupplier) {
      alert('Please select a supplier.');
      return;
    }

    try {
      setSubmitting(true);

      await reorderAPI.createRequest({
        item: item.id,
        quantity: totalUnits,
        requested_by: requestedBy || 'User',
        request_notes: notes,
        priority: item.needs_reorder ? 'high' : 'normal',
        preferred_supplier: selectedSupplier.id,
        package_quantity: packageQuantity,
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

  // Show submitting state for non-logged users
  if (!isLoggedIn && submitting) {
    return (
      <div className="scan-page">
        <div className="loading">
          <h2>🔄 Submitting Reorder Request</h2>
          <p>Please wait while we process your request...</p>
        </div>
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
    // Check if we have an existing reorder request
    if (item?.has_pending_reorder && item?.active_reorder_request) {
      const request = item.active_reorder_request;
      const statusMessage = {
        'pending': 'Your reorder request is pending admin approval',
        'approved': 'Your reorder request has been approved and will be ordered soon',
        'ordered': `Your reorder was placed on ${new Date(request.ordered_at || '').toLocaleDateString()}`
      }[request.status] || 'Your reorder request is being processed';

      return (
        <div className="scan-page">
          <div className="info">
            <h2>ℹ️ Reorder Already Requested</h2>
            <p><strong>{item.name}</strong> already has a reorder request in progress.</p>
            <div className="request-details">
              <p><strong>Status:</strong> {request.status.charAt(0).toUpperCase() + request.status.slice(1)}</p>
              <p><strong>Quantity:</strong> {request.quantity} units</p>
              <p><strong>Requested:</strong> {new Date(request.requested_at).toLocaleDateString()}</p>
              {request.requested_by && <p><strong>Requested by:</strong> {request.requested_by}</p>}
              {item.expected_delivery_date && (
                <p><strong>Expected Delivery:</strong> {new Date(item.expected_delivery_date).toLocaleDateString()}</p>
              )}
            </div>
            <p className="status-message">{statusMessage}</p>
            <p className="redirect-message">Redirecting to home...</p>
          </div>
        </div>
      );
    }

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

        {!isLoggedIn && !submitting && (
          <div className="auto-submit-message">
            <h2>🔄 Processing Reorder Request</h2>
            <p>We're automatically submitting a reorder request for <strong>{item.reorder_quantity} units</strong> of this item.</p>
            <p>You'll be redirected to a confirmation page shortly...</p>
          </div>
        )}

        {isLoggedIn && item.has_pending_reorder && item.active_reorder_request && (
          <div className="existing-request-info">
            <h2>ℹ️ Reorder In Progress</h2>
            <p>This item already has a reorder request:</p>
            <div className="request-summary">
              <p><strong>Status:</strong> {item.active_reorder_request.status.charAt(0).toUpperCase() + item.active_reorder_request.status.slice(1)}</p>
              <p><strong>Quantity:</strong> {item.active_reorder_request.quantity} units</p>
              <p><strong>Requested:</strong> {new Date(item.active_reorder_request.requested_at).toLocaleDateString()}</p>
              {item.active_reorder_request.requested_by && (
                <p><strong>Requested by:</strong> {item.active_reorder_request.requested_by}</p>
              )}
              {item.expected_delivery_date && (
                <p><strong>Expected Delivery:</strong> {new Date(item.expected_delivery_date).toLocaleDateString()}</p>
              )}
            </div>
          </div>
        )}

        {isLoggedIn && !item.has_pending_reorder && (
          <form onSubmit={handleSubmitReorder} className="reorder-form">
            <h2>Request Reorder</h2>
            
            {suppliers.length > 0 && (
                <div className="form-group">
                  <label htmlFor="supplierSelect">Supplier</label>
                  <select
                    id="supplierSelect"
                    value={selectedSupplier?.id || ''}
                    onChange={(e) => handleSupplierChange(Number(e.target.value))}
                    required
                  >
                    <option value="">Select a supplier...</option>
                    {suppliers
                      .filter(s => s.is_active)
                      .sort((a, b) => parseFloat(a.unit_cost || '999') - parseFloat(b.unit_cost || '999'))
                      .map(supplier => (
                        <option key={supplier.id} value={supplier.id}>
                          {supplier.supplier_name} - ${supplier.unit_cost}/unit 
                          ({supplier.quantity_per_package} per package)
                          {supplier.package_dimensions_display !== 'No dimensions specified' && 
                            ` - ${supplier.package_dimensions_display}`}
                        </option>
                      ))}
                  </select>
                </div>
              )}

              {selectedSupplier && (
                <>
                  <div className="supplier-details">
                    <h3>Package Details</h3>
                    <div className="detail-grid">
                      <div>
                        <strong>Units per package:</strong> {selectedSupplier.quantity_per_package}
                      </div>
                      <div>
                        <strong>Package cost:</strong> ${selectedSupplier.package_cost}
                      </div>
                      <div>
                        <strong>Unit cost:</strong> ${selectedSupplier.unit_cost}
                      </div>
                      <div>
                        <strong>Lead time:</strong> {selectedSupplier.average_lead_time} days
                      </div>
                      {selectedSupplier.package_dimensions_display !== 'No dimensions specified' && (
                        <div>
                          <strong>Dimensions:</strong> {selectedSupplier.package_dimensions_display}
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="form-group">
                    <label htmlFor="packageQuantity">Number of Packages</label>
                    <input
                      type="number"
                      id="packageQuantity"
                      value={packageQuantity}
                      onChange={(e) => handlePackageQuantityChange(Number(e.target.value))}
                      min="1"
                      required
                    />
                    <small className="form-help">
                      = {totalUnits} total units (${estimatedCost.toFixed(2)} estimated cost)
                    </small>
                  </div>

                  <div className="order-summary">
                    <h3>Order Summary</h3>
                    <div className="summary-item">
                      <span>Total Units:</span>
                      <span><strong>{totalUnits} units</strong></span>
                    </div>
                    <div className="summary-item">
                      <span>Estimated Cost:</span>
                      <span><strong>${estimatedCost.toFixed(2)}</strong></span>
                    </div>
                    <div className="summary-item">
                      <span>Estimated Lead Time:</span>
                      <span><strong>{estimatedLeadTime} days</strong></span>
                    </div>
                  </div>
                </>
              )}

              <div className="form-group">
                <label htmlFor="requestedBy">Your Name</label>
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
                disabled={submitting || !selectedSupplier}
              >
                {submitting ? 'Submitting...' : `Request ${totalUnits} Units`}
              </button>
          </form>
        )}
      </div>
    </div>
  );
};

export default ScanPage;
