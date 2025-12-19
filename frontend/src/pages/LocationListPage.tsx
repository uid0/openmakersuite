/**
 * Location List Page
 * Display locations in hierarchical tree structure
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import TreeView, { TreeNode } from '../components/TreeView';
import { inventoryAPI } from '../services/api';
import '../styles/LocationListPage.css';
import { Location } from '../types';

const LocationListPage: React.FC = () => {
  const [locations, setLocations] = useState<Location[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [isStaff, setIsStaff] = useState(false);

  useEffect(() => {
    const staffStatus = localStorage.getItem('is_staff');
    setIsStaff(staffStatus === 'true');
    loadLocations();
  }, []);

  const loadLocations = async () => {
    try {
      setLoading(true);
      const response = await inventoryAPI.listLocations();
      setLocations(response.data.results);
      setError(null);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to load locations');
      console.error('Error loading locations:', err);
    } finally {
      setLoading(false);
    }
  };

  const buildTree = (items: Location[]): TreeNode[] => {
    const itemMap = new Map<number, TreeNode>();
    const roots: TreeNode[] = [];

    // First pass: create all nodes
    items.forEach((item) => {
      itemMap.set(item.id, {
        ...item,
      });
    });

    // Second pass: build tree structure
    items.forEach((item) => {
      const node = itemMap.get(item.id)!;
      if (item.parent === null) {
        roots.push(node);
      } else {
        const parent = itemMap.get(item.parent);
        if (parent) {
          if (!parent.children) {
            parent.children = [];
          }
          parent.children.push(node);
        } else {
          // Parent not found, treat as root
          roots.push(node);
        }
      }
    });

    return roots;
  };

  const filteredLocations = useMemo(() => {
    if (!searchTerm) return locations;
    const term = searchTerm.toLowerCase();
    return locations.filter(
      (loc) =>
        loc.name.toLowerCase().includes(term) ||
        loc.description?.toLowerCase().includes(term)
    );
  }, [locations, searchTerm]);

  const treeNodes = useMemo(() => buildTree(filteredLocations), [filteredLocations]);

  const handleDelete = async (id: number) => {
    if (!window.confirm('Are you sure you want to delete this location?')) {
      return;
    }

    try {
      await inventoryAPI.deleteLocation(id.toString());
      await loadLocations();
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Failed to delete location');
    }
  };

  const handleGenerateQR = async (id: number) => {
    try {
      await inventoryAPI.generateLocationQR(id.toString());
      await loadLocations();
      alert('QR code generated successfully');
    } catch (err: any) {
      alert(err.response?.data?.error || 'Failed to generate QR code');
    }
  };

  const renderLocationNode = (node: TreeNode, level: number) => {
    const location = node as unknown as Location;
    return (
      <div className="location-row">
        <div className="location-info">
          <div className="location-details">
            <div className="location-name">{location.name}</div>
            {location.description && (
              <div className="location-description">{location.description}</div>
            )}
          </div>
        </div>
        <div className="location-meta">
          <span className={`status-badge ${location.is_active ? 'active' : 'inactive'}`}>
            {location.is_active ? 'Active' : 'Inactive'}
          </span>
          <span className={`qr-status ${location.qr_code_url ? 'has-qr' : 'no-qr'}`}>
            {location.qr_code_url ? '✓ QR' : 'No QR'}
          </span>
          <span className="fixture-count">
            {location.fixture_count || 0} fixtures
          </span>
        </div>
        {isStaff && (
          <div className="location-actions">
            <Link
              to={`/inventory/locations/${location.id}`}
              className="btn-view"
            >
              View
            </Link>
            <Link
              to={`/inventory/locations/${location.id}/edit`}
              className="btn-edit"
            >
              Edit
            </Link>
            <button
              onClick={() => handleGenerateQR(location.id)}
              className="btn-qr"
              title="Generate QR Code"
            >
              QR
            </button>
            <button
              onClick={() => handleDelete(location.id)}
              className="btn-delete"
            >
              Delete
            </button>
          </div>
        )}
      </div>
    );
  };

  if (loading) {
    return (
      <div className="location-list-page">
        <div className="loading">Loading locations...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="location-list-page">
        <div className="error">{error}</div>
      </div>
    );
  }

  return (
    <div className="location-list-page">
      <header className="page-header">
        <h1>Locations</h1>
        {isStaff && (
          <Link to="/inventory/locations/new" className="btn-primary">
            Create Location
          </Link>
        )}
      </header>

      <div className="page-controls">
        <input
          type="text"
          placeholder="Search locations..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          className="search-input"
        />
      </div>

      {treeNodes.length === 0 ? (
        <div className="empty-state">
          <p>No locations found.</p>
          {isStaff && (
            <Link to="/inventory/locations/new" className="btn-primary">
              Create First Location
            </Link>
          )}
        </div>
      ) : (
        <div className="location-tree">
          <TreeView
            nodes={treeNodes}
            renderNode={renderLocationNode}
            defaultExpanded={true}
          />
        </div>
      )}
    </div>
  );
};

export default LocationListPage;
