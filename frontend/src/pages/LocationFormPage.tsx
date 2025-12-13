/**
 * Location Form Page
 * Create/Edit form for locations
 */
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { inventoryAPI } from '../services/api';
import '../styles/LocationFormPage.css';
import { Location } from '../types';

interface LocationFormData {
  name: string;
  description: string;
  parent: number | null;
  is_active: boolean;
}

const LocationFormPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const isEditMode = !!id;

  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [locations, setLocations] = useState<Location[]>([]);
  const [formData, setFormData] = useState<LocationFormData>({
    name: '',
    description: '',
    parent: null,
    is_active: true,
  });

  useEffect(() => {
    loadLocations();
    if (isEditMode) {
      loadLocation();
    }
  }, [id, isEditMode]);

  const loadLocations = async () => {
    try {
      const response = await inventoryAPI.listLocations();
      setLocations(response.data.results);
    } catch (err) {
      console.error('Error loading locations:', err);
    }
  };

  const loadLocation = async () => {
    if (!id) return;

    try {
      setLoading(true);
      const response = await inventoryAPI.getLocation(id);
      const location = response.data;
      
      setFormData({
        name: location.name,
        description: location.description || '',
        parent: location.parent,
        is_active: location.is_active,
      });
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to load location');
      console.error('Error loading location:', err);
    } finally {
      setLoading(false);
    }
  };

  const buildLocationOptions = (items: Location[], excludeId?: number, level: number = 0): JSX.Element[] => {
    const options: JSX.Element[] = [];
    
    items
      .filter((item) => item.id !== excludeId)
      .forEach((item) => {
        const indent = '  '.repeat(level);
        options.push(
          <option key={item.id} value={item.id}>
            {indent}{item.name}
          </option>
        );
        
        if (item.children && item.children.length > 0) {
          options.push(...buildLocationOptions(item.children, excludeId, level + 1));
        }
      });
    
    return options;
  };

  const locationOptions = useMemo(() => {
    const roots = locations.filter((l) => !l.parent);
    return buildLocationOptions(roots, isEditMode ? parseInt(id!) : undefined);
  }, [locations, id, isEditMode]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!formData.name.trim()) {
      setError('Name is required');
      return;
    }

    try {
      setSaving(true);
      setError(null);

      const data: Partial<Location> = {
        name: formData.name.trim(),
        description: formData.description.trim(),
        is_active: formData.is_active,
      };

      if (formData.parent) {
        data.parent = formData.parent;
      }

      if (isEditMode) {
        await inventoryAPI.updateLocation(id!, data);
      } else {
        await inventoryAPI.createLocation(data);
      }

      navigate('/inventory/locations');
    } catch (err: any) {
      setError(err.response?.data?.detail || err.response?.data?.error || 'Failed to save location');
      console.error('Error saving location:', err);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="location-form-page">
        <div className="loading">Loading location...</div>
      </div>
    );
  }

  return (
    <div className="location-form-page">
      <header className="page-header">
        <h1>{isEditMode ? 'Edit Location' : 'Create Location'}</h1>
      </header>

      {error && (
        <div className="error-message">
          {error}
        </div>
      )}

      <form onSubmit={handleSubmit} className="location-form">
        <div className="form-group">
          <label htmlFor="name">
            Name <span className="required">*</span>
          </label>
          <input
            id="name"
            type="text"
            value={formData.name}
            onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            required
            className="form-input"
            placeholder="Location name"
          />
        </div>

        <div className="form-group">
          <label htmlFor="description">Description</label>
          <textarea
            id="description"
            value={formData.description}
            onChange={(e) => setFormData({ ...formData, description: e.target.value })}
            className="form-textarea"
            placeholder="Location description"
            rows={4}
          />
        </div>

        <div className="form-group">
          <label htmlFor="parent">Parent Location</label>
          <select
            id="parent"
            value={formData.parent || ''}
            onChange={(e) =>
              setFormData({
                ...formData,
                parent: e.target.value ? parseInt(e.target.value) : null,
              })
            }
            className="form-select"
          >
            <option value="">None (Top Level)</option>
            {locationOptions}
          </select>
        </div>

        <div className="form-group">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={formData.is_active}
              onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
              className="form-checkbox"
            />
            <span>Active</span>
          </label>
        </div>

        <div className="form-actions">
          <button
            type="button"
            onClick={() => navigate('/inventory/locations')}
            className="btn-secondary"
            disabled={saving}
          >
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={saving}>
            {saving ? 'Saving...' : isEditMode ? 'Update Location' : 'Create Location'}
          </button>
        </div>
      </form>
    </div>
  );
};

export default LocationFormPage;
