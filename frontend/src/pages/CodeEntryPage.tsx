/**
 * Code Entry Page
 * Allows users who are phobic of scanning QR codes to enter a 6-character code
 * to navigate to the appropriate asset, inventory item, or location page.
 */
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { inventoryAPI } from '../services/api';
import '../styles/ScanPage.css';

const CodeEntryPage: React.FC = () => {
  const navigate = useNavigate();
  const [code, setCode] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <div className="scan-page">
      <div className="scan-container">
        <h1>Enter Access Code</h1>
        <p className="scan-description">
          If you prefer not to scan a QR code, you can enter the 6-character code
          shown below the QR code to access the item.
        </p>

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
  );
};

export default CodeEntryPage;

