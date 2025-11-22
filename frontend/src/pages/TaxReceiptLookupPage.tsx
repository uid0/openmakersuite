/**
 * Tax Receipt Lookup Page
 * Public-facing page for looking up tax receipts by serial number
 */
import React, { useState } from 'react';
import { donationsAPI } from '../services/api';
import { TaxReceipt } from '../types';
import '../styles/ScanPage.css';

const TaxReceiptLookupPage: React.FC = () => {
  const [serialNumber, setSerialNumber] = useState('');
  const [receipt, setReceipt] = useState<TaxReceipt | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleLookup = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!serialNumber.trim()) {
      setError('Please enter a serial number');
      return;
    }

    setLoading(true);
    setError(null);
    setReceipt(null);

    try {
      const response = await donationsAPI.lookupTaxReceipt(serialNumber.trim());
      setReceipt(response.data);
    } catch (err: any) {
      setError(err.response?.data?.error || 'Tax receipt not found');
    } finally {
      setLoading(false);
    }
  };

  const handleDownload = async (isCopy: boolean = false) => {
    if (!receipt) return;

    try {
      const response = await donationsAPI.downloadTaxReceipt(receipt.id, isCopy);
      const blob = new Blob([response.data], { type: 'application/pdf' });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `tax_receipt_${receipt.serial_number}${isCopy ? '_copy' : ''}.pdf`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (err: any) {
      setError('Failed to download PDF');
    }
  };

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    });
  };

  return (
    <div className="scan-page">
      <div className="scan-container">
        <h1>Tax Receipt Lookup</h1>
        <p className="scan-subtitle">
          Enter your tax receipt serial number to view and download your receipt
        </p>

        <form onSubmit={handleLookup} className="scan-form">
          <div className="form-group">
            <label htmlFor="serialNumber">Serial Number</label>
            <input
              type="text"
              id="serialNumber"
              value={serialNumber}
              onChange={(e) => setSerialNumber(e.target.value)}
              placeholder="Enter receipt serial number"
              className="form-control"
              disabled={loading}
            />
          </div>
          <button type="submit" className="btn btn-primary" disabled={loading}>
            {loading ? 'Looking up...' : 'Lookup Receipt'}
          </button>
        </form>

        {error && (
          <div className="alert alert-error" style={{ marginTop: '20px' }}>
            {error}
          </div>
        )}

        {receipt && (
          <div className="receipt-details" style={{ marginTop: '30px' }}>
            <h2>Receipt Details</h2>
            <div className="receipt-info">
              <div className="info-row">
                <strong>Serial Number:</strong> {receipt.serial_number}
              </div>
              <div className="info-row">
                <strong>Donor Name:</strong> {receipt.donor_name}
              </div>
              <div className="info-row">
                <strong>Donation Date:</strong> {formatDate(receipt.issued_date)}
              </div>
              <div className="info-row">
                <strong>Issued Date:</strong> {formatDate(receipt.issued_date)}
              </div>
              <div className="info-row">
                <strong>Donation Number:</strong> {receipt.donation_number}
              </div>
            </div>

            <div className="receipt-actions" style={{ marginTop: '20px' }}>
              <button
                onClick={() => handleDownload(false)}
                className="btn btn-primary"
                style={{ marginRight: '10px' }}
              >
                Download Receipt (Clean)
              </button>
              <button
                onClick={() => handleDownload(true)}
                className="btn btn-secondary"
              >
                Download Receipt (Copy)
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default TaxReceiptLookupPage;

