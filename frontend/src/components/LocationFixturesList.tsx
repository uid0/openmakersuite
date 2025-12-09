/**
 * Location Fixtures List Component
 * Display fixtures for a specific location
 */
import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { inventoryAPI } from '../services/api';
import '../styles/LocationFixturesList.css';
import { Fixture } from '../types';

interface LocationFixturesListProps {
  locationId: string;
}

const LocationFixturesList: React.FC<LocationFixturesListProps> = ({ locationId }) => {
  const [fixtures, setFixtures] = useState<Fixture[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadFixtures();
  }, [locationId]);

  const loadFixtures = async () => {
    try {
      setLoading(true);
      // Use the location fixtures endpoint
      const response = await inventoryAPI.getLocationFixtures(locationId);
      setFixtures(response.data);
      setError(null);
    } catch (err: any) {
      // If endpoint doesn't exist yet, show empty state
      console.error('Error loading fixtures:', err);
      setFixtures([]);
      setError(null);
    } finally {
      setLoading(false);
    }
  };

  // Note: This component needs the fixtures endpoint to be properly implemented
  // For now, it shows a placeholder
  if (loading) {
    return <div className="fixtures-loading">Loading fixtures...</div>;
  }

  if (error) {
    return <div className="fixtures-error">{error}</div>;
  }

  if (fixtures.length === 0) {
    return (
      <div className="fixtures-empty">
        <p>No fixtures found at this location.</p>
      </div>
    );
  }

  return (
    <div className="location-fixtures-list">
      <div className="fixtures-grid">
        {fixtures.map((fixture) => (
          <div key={fixture.id} className="fixture-card">
            <h3>{fixture.name}</h3>
            {fixture.description && <p className="fixture-description">{fixture.description}</p>}
            <div className="fixture-meta">
              <span className="fixture-item">
                Refill: {fixture.refill_item_name}
              </span>
              {fixture.pending_requests_count > 0 && (
                <span className="pending-requests">
                  {fixture.pending_requests_count} pending requests
                </span>
              )}
            </div>
            <div className="fixture-actions">
              <Link
                to={`/inventory/scan/fixture/${fixture.id}`}
                className="btn-scan"
              >
                Scan Fixture
              </Link>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default LocationFixturesList;
