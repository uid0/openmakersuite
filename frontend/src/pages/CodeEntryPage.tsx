/**
 * Inventory Scan Page (`/inventory/scan`)
 *
 * Camera-based QR scanner for inventory. A scanned payload is resolved through
 * the unified scanner dispatcher (`/scanner/dispatch/`), which understands our
 * QR URLs, UPC/EAN barcodes, and location codes, then navigates to the target.
 *
 * The legacy 6-character manual access-code entry was removed once the org
 * standardized on QR/UPC scanning. Inventory items and assets no longer carry
 * an `access_code`; the dispatcher resolves them by QR URL or UPC instead.
 */
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import QRScanner, { QRScannerError } from '../components/QRScanner';
import { scannerAPI } from '../services/api';
import '../styles/ScanPage.css';

const CodeEntryPage: React.FC = () => {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showScanner, setShowScanner] = useState(false);

  const processScannedCode = async (scannedText: string) => {
    setLoading(true);
    setError(null);

    try {
      const { data } = await scannerAPI.dispatch(scannedText.trim());

      // A resolved scan always carries a target_url (a frontend route path);
      // a miss comes back as action `unknown` with no target.
      if (data.target_url) {
        navigate(data.target_url);
      } else {
        setError(data.message || 'Code not found. Please check and try again.');
      }
    } catch (err: any) {
      const errorMessage =
        err.response?.data?.detail ||
        err.response?.data?.error ||
        'Could not reach the scanner. Please try again.';
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  };

  const handleScanSuccess = (decodedText: string) => {
    setShowScanner(false);
    document.body.classList.remove('qr-scanner-open');
    processScannedCode(decodedText);
  };

  const handleScanError = (_error: QRScannerError) => {
    // Error handling is done in the scanner component
  };

  const handleOpenScanner = () => {
    setShowScanner(true);
    document.body.classList.add('qr-scanner-open');
  };

  const handleCloseScanner = () => {
    setShowScanner(false);
    document.body.classList.remove('qr-scanner-open');
  };

  return (
    <>
      {showScanner && (
        <QRScanner
          onScanSuccess={handleScanSuccess}
          onScanError={handleScanError}
          onClose={handleCloseScanner}
        />
      )}
      <div className="scan-page">
        <div className="scan-container">
          <h1>Scan QR Code</h1>
          <p className="scan-description">
            Scan an item, asset, or location QR code to open it.
          </p>

          <div style={{ marginBottom: '1.5rem', textAlign: 'center' }}>
            <button
              onClick={handleOpenScanner}
              disabled={loading}
              className="submit-button"
              style={{
                padding: '1rem 2rem',
                fontSize: '1.2rem',
                backgroundColor: '#28a745',
              }}
            >
              📱 Scan QR Code
            </button>
          </div>

          {loading && (
            <p style={{ textAlign: 'center', color: '#666' }}>Looking up…</p>
          )}

          {error && (
            <div className="error-message" style={{ marginTop: '1rem' }}>
              {error}
            </div>
          )}
        </div>
      </div>
    </>
  );
};

export default CodeEntryPage;
