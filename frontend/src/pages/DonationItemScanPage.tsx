/**
 * Donation Item QR Code Scan Page
 * - Authenticated users: Identify item, select initial disposition
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { donationsAPI } from '../services/api';
import '../styles/ScanPage.css';
import { Disposition, DispositionType, DonationItem, KeptDestination, SaleMethod } from '../types';
import { showError } from '../utils/dialogs';

const DonationItemScanPage: React.FC = () => {
  const { itemId } = useParams<{ itemId: string }>();
  const navigate = useNavigate();

  // Authentication state
  const [isLoggedIn] = useState<boolean>(() => !!localStorage.getItem('token'));

  // Data state
  const [item, setItem] = useState<DonationItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dispositions, setDispositions] = useState<Disposition[]>([]);

  // Form state
  const [itemName, setItemName] = useState('');
  const [itemDescription, setItemDescription] = useState('');
  const [itemCondition, setItemCondition] = useState<DonationItem['condition']>('good');
  const [dispositionType, setDispositionType] = useState<DispositionType | ''>('');
  const [quantity, setQuantity] = useState<number>(1);
  const [saleMethod, setSaleMethod] = useState<SaleMethod | ''>('');
  const [salePrice, setSalePrice] = useState<string>('');
  const [keptDestination, setKeptDestination] = useState<KeptDestination | ''>('');
  const [notes, setNotes] = useState('');
  const [recipientName, setRecipientName] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);

  const loadItem = useCallback(async () => {
    if (!itemId) return;
    try {
      setLoading(true);
      const itemResponse = await donationsAPI.getDonationItem(itemId);
      setItem(itemResponse.data);
      setItemName(itemResponse.data.name);
      setItemDescription(itemResponse.data.description || '');
      setItemCondition(itemResponse.data.condition);
      setQuantity(itemResponse.data.remaining_quantity || itemResponse.data.quantity);
      setError(null);

      // Load existing dispositions
      const dispositionsResponse = await donationsAPI.getDispositions({ donation_item: itemId });
      setDispositions(dispositionsResponse.data.results);
    } catch (err: any) {
      setError(err.response?.data?.detail || err.response?.data?.error || 'Failed to load donation item');
      console.error('Error loading donation item:', err);
    } finally {
      setLoading(false);
    }
  }, [itemId]);

  useEffect(() => {
    if (itemId) {
      loadItem();
    }
  }, [itemId, loadItem]);

  const handleUpdateItem = async () => {
    if (!item || !isLoggedIn) return;

    try {
      setSubmitting(true);
      await donationsAPI.updateDonationItem(item.id, {
        name: itemName,
        description: itemDescription,
        condition: itemCondition,
      });
      await loadItem();
      setActionSuccess('Item information updated successfully');
      setTimeout(() => setActionSuccess(null), 3000);
    } catch (err: any) {
      showError(err.response?.data?.detail || 'Failed to update item');
      console.error('Error updating item:', err);
    } finally {
      setSubmitting(false);
    }
  };

  const handleCreateDisposition = async () => {
    if (!item || !isLoggedIn || !dispositionType) return;

    if (!dispositionType) {
      showError('Please select a disposition type');
      return;
    }

    // Validate required fields based on disposition type
    if (dispositionType === 'sold' || dispositionType === 'auctioned') {
      if (!saleMethod) {
        showError('Please select a sale method');
        return;
      }
    }

    if (dispositionType === 'kept') {
      if (!keptDestination) {
        showError('Please select where the item is being kept');
        return;
      }
    }

    if (quantity > (item.remaining_quantity || item.quantity)) {
      showError(`Quantity cannot exceed remaining quantity (${item.remaining_quantity || item.quantity})`);
      return;
    }

    try {
      setSubmitting(true);
      const dispositionData: any = {
        donation_item: item.id,
        disposition_type: dispositionType,
        quantity: quantity,
        notes: notes || undefined,
      };

      if (dispositionType === 'sold' || dispositionType === 'auctioned') {
        dispositionData.sale_method = saleMethod;
        if (salePrice) {
          dispositionData.sale_price = salePrice;
        }
        if (recipientName) {
          dispositionData.recipient_name = recipientName;
        }
      }

      if (dispositionType === 'kept') {
        dispositionData.kept_destination = keptDestination;
      }

      if (dispositionType === 'donated_out' && recipientName) {
        dispositionData.recipient_name = recipientName;
      }

      await donationsAPI.createDisposition(dispositionData);
      await loadItem();
      setActionSuccess('Disposition recorded successfully');
      
      // Reset form
      setDispositionType('');
      setSaleMethod('');
      setSalePrice('');
      setKeptDestination('');
      setNotes('');
      setRecipientName('');
      setQuantity(item.remaining_quantity || item.quantity);
      
      setTimeout(() => setActionSuccess(null), 3000);
    } catch (err: any) {
      showError(err.response?.data?.detail || 'Failed to create disposition');
      console.error('Error creating disposition:', err);
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="scan-page">
        <div className="scan-container">
          <p>Loading donation item...</p>
        </div>
      </div>
    );
  }

  if (error || !item) {
    return (
      <div className="scan-page">
        <div className="scan-container">
          <h1>Donation Item</h1>
          <p className="error">{error || 'Item not found'}</p>
          <button onClick={() => navigate('/')}>Go Home</button>
        </div>
      </div>
    );
  }

  return (
    <div className="scan-page">
      <div className="scan-container">
        <h1>Donation Item</h1>

        {actionSuccess && (
          <div className="success-message" style={{ marginBottom: '20px', padding: '10px', backgroundColor: '#d4edda', color: '#155724', borderRadius: '4px' }}>
            {actionSuccess}
          </div>
        )}

        {/* Item Information */}
        <div className="scan-section">
          <h2>Item Information</h2>
          <div className="info-grid">
            <div>
              <strong>Donation Number:</strong> {item.donation_number}
            </div>
            <div>
              <strong>Donor:</strong> {item.donor_name}
            </div>
            <div>
              <strong>Current Status:</strong> {item.status.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase())}
            </div>
            <div>
              <strong>Condition:</strong> {item.condition.charAt(0).toUpperCase() + item.condition.slice(1)}
            </div>
            <div>
              <strong>Quantity:</strong> {item.quantity}
            </div>
            <div>
              <strong>Remaining Quantity:</strong> {item.remaining_quantity}
            </div>
          </div>
        </div>

        {isLoggedIn ? (
          <>
            {/* Update Item Information */}
            <div className="scan-section">
              <h2>Identify Item</h2>
              <div className="form-group">
                <label htmlFor="itemName">Item Name *</label>
                <input
                  id="itemName"
                  type="text"
                  value={itemName}
                  onChange={(e) => setItemName(e.target.value)}
                  placeholder="Enter item name or description"
                  required
                />
              </div>
              <div className="form-group">
                <label htmlFor="itemDescription">Description</label>
                <textarea
                  id="itemDescription"
                  value={itemDescription}
                  onChange={(e) => setItemDescription(e.target.value)}
                  placeholder="Additional details about the item"
                  rows={3}
                />
              </div>
              <div className="form-group">
                <label htmlFor="itemCondition">Condition *</label>
                <select
                  id="itemCondition"
                  value={itemCondition}
                  onChange={(e) => setItemCondition(e.target.value as DonationItem['condition'])}
                  required
                >
                  <option value="excellent">Excellent</option>
                  <option value="good">Good</option>
                  <option value="fair">Fair</option>
                  <option value="poor">Poor</option>
                  <option value="unusable">Unusable</option>
                </select>
              </div>
              <button
                onClick={handleUpdateItem}
                disabled={submitting || !itemName}
                className="btn btn-primary"
              >
                {submitting ? 'Updating...' : 'Update Item Information'}
              </button>
            </div>

            {/* Create Disposition */}
            <div className="scan-section">
              <h2>Record Initial Disposition</h2>
              <div className="form-group">
                <label htmlFor="dispositionType">Disposition Type *</label>
                <select
                  id="dispositionType"
                  value={dispositionType}
                  onChange={(e) => {
                    setDispositionType(e.target.value as DispositionType);
                    // Reset dependent fields
                    setSaleMethod('');
                    setSalePrice('');
                    setKeptDestination('');
                    setRecipientName('');
                  }}
                  required
                >
                  <option value="">Select disposition type...</option>
                  <option value="kept">Kept for Use</option>
                  <option value="sold">Sold Directly</option>
                  <option value="auctioned">Auctioned</option>
                  <option value="donated_out">Donated to Another Organization</option>
                  <option value="recycled">Recycled</option>
                  <option value="disposed">Disposed/Trashed</option>
                  <option value="returned">Returned to Donor</option>
                  <option value="parted_out">Parted Out for Components</option>
                  <option value="other">Other</option>
                </select>
              </div>

              <div className="form-group">
                <label htmlFor="quantity">Quantity *</label>
                <input
                  id="quantity"
                  type="number"
                  min="1"
                  max={item.remaining_quantity || item.quantity}
                  value={quantity}
                  onChange={(e) => setQuantity(parseInt(e.target.value) || 1)}
                  required
                />
                <small>Remaining: {item.remaining_quantity}</small>
              </div>

              {/* Sale-specific fields */}
              {(dispositionType === 'sold' || dispositionType === 'auctioned') && (
                <>
                  <div className="form-group">
                    <label htmlFor="saleMethod">Sale Method *</label>
                    <select
                      id="saleMethod"
                      value={saleMethod}
                      onChange={(e) => setSaleMethod(e.target.value as SaleMethod)}
                      required
                    >
                      <option value="">Select sale method...</option>
                      <option value="direct">Direct Sale</option>
                      <option value="auction">Auction</option>
                    </select>
                  </div>
                  <div className="form-group">
                    <label htmlFor="salePrice">Sale Price</label>
                    <input
                      id="salePrice"
                      type="number"
                      step="0.01"
                      min="0"
                      value={salePrice}
                      onChange={(e) => setSalePrice(e.target.value)}
                      placeholder="0.00"
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="recipientName">Recipient Name</label>
                    <input
                      id="recipientName"
                      type="text"
                      value={recipientName}
                      onChange={(e) => setRecipientName(e.target.value)}
                      placeholder="Buyer or recipient name"
                    />
                  </div>
                </>
              )}

              {/* Kept-specific fields */}
              {dispositionType === 'kept' && (
                <div className="form-group">
                  <label htmlFor="keptDestination">Kept For *</label>
                  <select
                    id="keptDestination"
                    value={keptDestination}
                    onChange={(e) => setKeptDestination(e.target.value as KeptDestination)}
                    required
                  >
                    <option value="">Select destination...</option>
                    <option value="makerspace">Makerspace</option>
                    <option value="sig">Committee/SIG</option>
                  </select>
                </div>
              )}

              {/* Donated out recipient */}
              {dispositionType === 'donated_out' && (
                <div className="form-group">
                  <label htmlFor="recipientName">Recipient Organization</label>
                  <input
                    id="recipientName"
                    type="text"
                    value={recipientName}
                    onChange={(e) => setRecipientName(e.target.value)}
                    placeholder="Organization name"
                  />
                </div>
              )}

              <div className="form-group">
                <label htmlFor="notes">Notes</label>
                <textarea
                  id="notes"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="Additional notes about this disposition"
                  rows={3}
                />
              </div>

              <button
                onClick={handleCreateDisposition}
                disabled={submitting || !dispositionType || quantity < 1}
                className="btn btn-primary"
              >
                {submitting ? 'Recording...' : 'Record Disposition'}
              </button>
            </div>

            {/* Existing Dispositions */}
            {dispositions.length > 0 && (
              <div className="scan-section">
                <h2>Previous Dispositions</h2>
                <table className="info-table">
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th>Type</th>
                      <th>Quantity</th>
                      <th>Details</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dispositions.map((disp) => (
                      <tr key={disp.id}>
                        <td>{new Date(disp.disposition_date).toLocaleDateString()}</td>
                        <td>{disp.disposition_type.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase())}</td>
                        <td>{disp.quantity}</td>
                        <td>
                          {disp.sale_price && `$${disp.sale_price} `}
                          {disp.kept_destination && `Kept for ${disp.kept_destination} `}
                          {disp.recipient_name && `→ ${disp.recipient_name}`}
                          {disp.notes && `(${disp.notes})`}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        ) : (
          <div className="scan-section">
            <p>Please log in to identify and process this donation item.</p>
            <button onClick={() => navigate('/')} className="btn btn-primary">Go Home</button>
          </div>
        )}
      </div>
    </div>
  );
};

export default DonationItemScanPage;

