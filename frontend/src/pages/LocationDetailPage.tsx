/**
 * Location Detail Page
 * Display location details, QR code, and fixtures
 */
import { Button, Group, Paper, Stack, Text } from '@mantine/core';
import { IconAlertTriangle, IconBolt, IconPlus, IconShieldHalfFilled } from '@tabler/icons-react';
import React, { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import LocationFixturesList from '../components/LocationFixturesList';
import LocationProblemsPanel from '../components/LocationProblemsPanel';
import LocationTrafficPanel from '../components/LocationTrafficPanel';
import ReportLocationProblemModal from '../components/ReportLocationProblemModal';
import { climateAPI, inventoryAPI, Thermostat } from '../services/api';
import '../styles/LocationDetailPage.css';
import { Location } from '../types';
import { confirmDelete, showError, showSuccess } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const LocationDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [location, setLocation] = useState<Location | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isStaff, setIsStaff] = useState(false);
  const [generatingQR, setGeneratingQR] = useState(false);
  const [showReportModal, setShowReportModal] = useState(false);
  const [problemsRefreshKey, setProblemsRefreshKey] = useState(0);
  const [thermostats, setThermostats] = useState<Thermostat[]>([]);

  const loadThermostats = useCallback(async (locationId: string) => {
    try {
      const response = await climateAPI.listThermostats({
        controls_location: Number(locationId),
      });
      setThermostats(response.data.results);
    } catch (err) {
      console.warn('Failed to load thermostats for location', err);
      setThermostats([]);
    }
  }, []);

  useEffect(() => {
    const staffStatus = localStorage.getItem('is_staff');
    setIsStaff(staffStatus === 'true');
    if (id) {
      loadLocation();
      loadThermostats(id);
    }
  }, [id, loadThermostats]);

  const loadLocation = async () => {
    if (!id) return;

    try {
      setLoading(true);
      const response = await inventoryAPI.getLocation(id);
      setLocation(response.data);
      setError(null);
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to load location'));
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
      showError(extractErrorMessage(err, 'Failed to generate QR code'));
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
        showError(extractErrorMessage(err, 'Failed to delete location'));
      }
    });
  };

  if (loading) {
    return (
      <WorkspacePage
        testId="location-detail-page"
        hero={{ eyebrow: 'Inventory · Location', title: 'Location', description: 'Loading…' }}
      >
        <Paper withBorder p="md">
          <Text c="dimmed">Loading location…</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  if (error || !location) {
    return (
      <WorkspacePage
        testId="location-detail-page"
        hero={{
          eyebrow: 'Inventory · Location',
          title: 'Location',
          description: error || 'Not found.',
          action: (
            <Button component={Link} to="/inventory/locations" variant="default">
              Back to locations
            </Button>
          ),
        }}
      >
        <Paper withBorder p="md" radius="md" bg="red.0" c="red.9">
          <Text>{error || 'Location not found.'}</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  return (
    <WorkspacePage
      testId="location-detail-page"
      hero={{
        eyebrow: location.parent_name
          ? `Inventory · in ${location.parent_name}`
          : 'Inventory · Location',
        title: location.name,
        description: location.description || undefined,
        action: (
          <Group gap="sm">
            <Button onClick={() => setShowReportModal(true)} variant="default">
              Report problem
            </Button>
            {isStaff && (
              <>
                <Button
                  component={Link}
                  to={`/facilities/locations/${location.id}/safety-sign`}
                  variant="default"
                  leftSection={<IconShieldHalfFilled size={16} />}
                  data-testid="open-safety-sign-button"
                >
                  Safety sign
                </Button>
                <Button
                  component={Link}
                  to={`/inventory/locations/${location.id}/reconcile`}
                  variant="default"
                >
                  Reconcile
                </Button>
                <Button
                  component={Link}
                  to={`/inventory/locations/${location.id}/edit`}
                >
                  Edit
                </Button>
                <Button color="red" variant="light" onClick={handleDelete}>
                  Delete
                </Button>
              </>
            )}
          </Group>
        ),
      }}
    >
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

        <div className="detail-section" data-testid="location-thermostats-section">
          <Group justify="space-between" align="flex-end" mb="xs">
            <h2 style={{ margin: 0 }}>Thermostats controlling this room</h2>
            {isStaff && (
              <Button
                component={Link}
                to={`/facilities/climate/thermostats/new?location=${location.id}`}
                size="xs"
                variant="light"
                leftSection={<IconPlus size={14} />}
                data-testid="add-thermostat-button"
              >
                Add thermostat
              </Button>
            )}
          </Group>
          {thermostats.length === 0 ? (
            <Paper withBorder p="md" radius="md">
              <Group gap="xs">
                <IconAlertTriangle size={16} color="orange" />
                <Text size="sm" c="dimmed">
                  No thermostats registered for this room. Without one, the safety sign cannot
                  surface HVAC kill breakers.
                </Text>
              </Group>
            </Paper>
          ) : (
            <Stack gap="xs">
              {thermostats.map((t) => (
                <Paper
                  key={t.id}
                  withBorder
                  p="sm"
                  radius="md"
                  data-testid={`location-thermostat-${t.id}`}
                >
                  <Group justify="space-between" align="flex-start" wrap="wrap">
                    <div>
                      <Group gap="xs">
                        <IconBolt size={14} />
                        <Text fw={600}>{t.label}</Text>
                      </Group>
                      <Text size="xs" c="dimmed" mt={2}>
                        {t.controlled_asset_name
                          ? `Controls ${t.controlled_asset_name}`
                          : 'No controlled asset linked'}
                        {t.manufacturer || t.model
                          ? ` · ${[t.manufacturer, t.model].filter(Boolean).join(' ')}`
                          : ''}
                      </Text>
                    </div>
                    {isStaff && (
                      <Button
                        component={Link}
                        to={`/facilities/climate/thermostats/${t.id}/edit`}
                        size="xs"
                        variant="subtle"
                      >
                        Edit
                      </Button>
                    )}
                  </Group>
                </Paper>
              ))}
            </Stack>
          )}
        </div>

        <div className="detail-section">
          <h2>Problem Reports</h2>
          <LocationProblemsPanel
            locationId={location.id}
            refreshKey={problemsRefreshKey}
          />
        </div>
      </div>

      {showReportModal && (
        <ReportLocationProblemModal
          locationId={location.id}
          locationName={location.name}
          onClose={() => setShowReportModal(false)}
          onSubmitted={() => {
            setShowReportModal(false);
            setProblemsRefreshKey((k) => k + 1);
            showSuccess('Problem reported. Maintenance has been notified.');
          }}
        />
      )}
    </WorkspacePage>
  );
};

export default LocationDetailPage;
