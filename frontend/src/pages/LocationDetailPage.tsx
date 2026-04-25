/**
 * Location Detail Page
 * Display location details, QR code, and fixtures
 */
import React, { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import LocationFixturesList from '../components/LocationFixturesList';
import LocationTrafficPanel from '../components/LocationTrafficPanel';
import { inventoryAPI } from '../services/api';
import '../styles/LocationDetailPage.css';
import { Location } from '../types';
import { confirmDelete, showError, showSuccess } from '../utils/dialogs';

const LocationDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [location, setLocation] = useState<Location | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isStaff, setIsStaff] = useState(false);
  const [generatingQR, setGeneratingQR] = useState(false);

  useEffect(() => {
    const staffStatus = localStorage.getItem('is_staff');
    setIsStaff(staffStatus === 'true');
    if (id) {
      loadLocation();
    }
  }, [id]);

  const loadLocation = async () => {
    if (!id) return;

    try {
      setLoading(true);
      const response = await inventoryAPI.getLocation(id);
      setLocation(response.data);
      setError(null);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to load location');
      console.error('Error loading location:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleGenerateQR = async () => {
    if (!id) return;

    try {
      setGeneratingQR(true);
      await inventoryAPI.generateLocationQR(id);
      await loadLocation();
      showSuccess('QR code generated successfully');
    } catch (err: any) {
      showError(err.response?.data?.error || 'Failed to generate QR code');
    } finally {
      setGeneratingQR(false);
    }
  };

  const handleDelete = () => {
    if (!id) return;
    confirmDelete('Are you sure you want to delete this location?', async () => {
      try {
        await inventoryAPI.deleteLocation(id);
        navigate('/inventory/locations');
      } catch (err: any) {
        showError(err.response?.data?.detail || 'Failed to delete location');
      }
    });
  };

  if (loading) {
    return (
      <div className="location-detail-page">
        <div className="loading">Loading location...</div>
      </div>
    );
  }

  if (error || !location) {
    return (
      <div className="location-detail-page">
        <div className="error">{error || 'Location not found'}</div>
        <Link to="/inventory/locations" className="btn-secondary">
          Back to Locations
        </Link>
      </div>
    );
  }

  return (
    <div className="location-detail-page">
      <header className="page-header">
        <div>
          <h1>{location.name}</h1>
          {location.description && (
            <p className="location-description">{location.description}</p>
          )}
        </div>
        {isStaff && (
          <div className="header-actions">
            <Link
              to={`/inventory/locations/${location.id}/reconcile`}
              className="btn-secondary"
            >
              Reconcile inventory
            </Link>
            <Link
              to={`/inventory/locations/${location.id}/edit`}
              className="btn-edit"
            >
              Edit
            </Link>
            <button onClick={handleDelete} className="btn-delete">
              Delete
            </button>
          </div>
        )}
      </header>

      <div className="location-details">
        <div className="detail-section">
          <h2>Location Information</h2>
          <div className="detail-grid">
            <div className="detail-item">
              <label>Status</label>
              <span className={`status-badge ${location.is_active ? 'active' : 'inactive'}`}>
                {location.is_active ? 'Active' : 'Inactive'}
              </span>
            </div>
            {location.parent_name && (
              <div className="detail-item">
                <label>Parent Location</label>
                <span>{location.parent_name}</span>
              </div>
            )}
            {location.fixture_count !== undefined && (
              <div className="detail-item">
                <label>Fixtures</label>
                <span>{location.fixture_count}</span>
              </div>
            )}
            {location.access_code && (
              <div className="detail-item">
                <label>Access Code</label>
                <span className="access-code">{location.access_code}</span>
              </div>
            )}
          </div>
        </div>

        <div className="detail-section">
          <h2>QR Code</h2>
          {location.qr_code_url ? (
            <div className="qr-section">
              <img
                src={location.qr_code_url}
                alt="Location QR Code"
                className="qr-image"
              />
              <div className="qr-actions">
                <a
                  href={location.qr_code_url}
                  download={`location_${location.id}_qr.png`}
                  className="btn-download"
                >
                  Download QR Code
                </a>
                <button
                  onClick={handleGenerateQR}
                  disabled={generatingQR}
                  className="btn-regenerate"
                >
                  {generatingQR ? 'Generating...' : 'Regenerate QR Code'}
                </button>
              </div>
            </div>
          ) : (
            <div className="qr-section">
              <p className="no-qr-message">No QR code generated yet.</p>
              <button
                onClick={handleGenerateQR}
                disabled={generatingQR}
                className="btn-generate"
              >
                {generatingQR ? 'Generating...' : 'Generate QR Code'}
              </button>
            </div>
          )}
        </div>

        {location.fixture_count !== undefined && location.fixture_count > 0 && (
          <div className="detail-section">
            <h2>Fixtures</h2>
            <LocationFixturesList locationId={location.id.toString()} />
          </div>
        )}

        <div className="detail-section">
          <h2>Traffic</h2>
          <LocationTrafficPanel locationId={location.id} />
        </div>
      </div>
    </div>
  );
};

export default LocationDetailPage;
