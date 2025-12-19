/**
 * Code Entry Page
 * Allows users who are phobic of scanning QR codes to enter a 6-character code
 * to navigate to the appropriate asset, inventory item, or location page.
 */
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import QRScanner from '../components/QRScanner';
import { inventoryAPI } from '../services/api';
import '../styles/ScanPage.css';

const CodeEntryPage: React.FC = () => {
  const navigate = useNavigate();
  const [code, setCode] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showScanner, setShowScanner] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!code || code.length !== 6) {
      setError('Please enter a 6-character code');
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const response = await inventoryAPI.lookupByCode(code.toUpperCase());
      const { url } = response.data;
      
      // Navigate to the URL
      if (url) {
        // Extract path from full URL if needed
        const urlObj = new URL(url);
        navigate(urlObj.pathname);
      } else {
        setError('Invalid response from server');
      }
    } catch (err: any) {
      const errorMessage = err.response?.data?.error || 'Code not found. Please check and try again.';
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  };

  const handleCodeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    // Convert to uppercase and filter out invalid characters
    const value = e.target.value.toUpperCase().replace(/[^A-HJ-NP-Z2-9]/g, '');
    // Limit to 6 characters
    const limitedValue = value.slice(0, 6);
    setCode(limitedValue);
    setError(null);
  };

  const processScannedCode = async (scannedText: string) => {
    setLoading(true);
    setError(null);

    try {
      // Extract code from URL if it's a full URL
      let codeToUse = scannedText;
      if (scannedText.includes('/')) {
        const urlParts = scannedText.split('/');
        codeToUse = urlParts[urlParts.length - 1];
      }

      const response = await inventoryAPI.lookupByCode(codeToUse.toUpperCase());
      const { url } = response.data;
      
      if (url) {
        const urlObj = new URL(url);
        navigate(urlObj.pathname);
      } else {
        setError('Invalid response from server');
      }
    } catch (err: any) {
      const errorMessage = err.response?.data?.error || 'Code not found. Please check and try again.';
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

  const handleScanError = (error: string) => {
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
          <h1>Enter Access Code</h1>
          <p className="scan-description">
            Scan a QR code or enter the 6-character code manually to access the item.
          </p>

          <div style={{ marginBottom: '1.5rem', textAlign: 'center' }}>
            <button
              onClick={handleOpenScanner}
              className="submit-button"
              style={{
                padding: '1rem 2rem',
                fontSize: '1.2rem',
                backgroundColor: '#28a745',
                marginBottom: '1rem',
              }}
            >
              📱 Scan QR Code
            </button>
            <p style={{ color: '#666', fontSize: '0.9rem' }}>or</p>
          </div>

        <form onSubmit={handleSubmit} className="code-entry-form">
          <div className="code-input-group">
            <label htmlFor="code">Access Code</label>
            <input
              id="code"
              type="text"
              value={code}
              onChange={handleCodeChange}
              placeholder="Enter 6-character code"
              maxLength={6}
              autoFocus
              className="code-input"
              style={{
                fontSize: '2rem',
                letterSpacing: '0.5rem',
                textAlign: 'center',
                textTransform: 'uppercase',
                fontFamily: 'monospace',
                padding: '1rem',
                border: '2px solid #ccc',
                borderRadius: '8px',
                width: '100%',
                maxWidth: '300px',
              }}
            />
            <p className="code-hint">
              Enter the 6-character code (letters and numbers, excluding I, O, 0, 1, L)
            </p>
          </div>

          {error && (
            <div className="error-message" style={{ marginTop: '1rem' }}>
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading || code.length !== 6}
            className="submit-button"
            style={{
              marginTop: '1.5rem',
              padding: '1rem 2rem',
              fontSize: '1.2rem',
            }}
          >
            {loading ? 'Looking up...' : 'Go to Item'}
          </button>
        </form>
      </div>
      </div>
    </>
  );
};

export default CodeEntryPage;

