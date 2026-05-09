/**
 * Location List Page
 * Display locations in hierarchical tree structure
 */
import { Button, Paper, Stack, Text, TextInput } from '@mantine/core';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import TreeView, { TreeNode } from '../components/TreeView';
import { inventoryAPI } from '../services/api';
import '../styles/LocationListPage.css';
import { Location } from '../types';
import { confirmDelete, showError, showSuccess } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const LocationListPage: React.FC = () => {
  const [locations, setLocations] = useState<Location[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [isStaff, setIsStaff] = useState(false);

  const loadLocations = useCallback(async () => {
    try {
      setLoading(true);
      const response = await inventoryAPI.listLocations();
      setLocations(response.data.results);
      setError(null);
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to load locations'));
      console.error('Error loading locations:', err);
      // Re-throw to trigger the Highlight error boundary if it's a critical error
      if (!err.response) {
        throw err;
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const staffStatus = localStorage.getItem('is_staff');
    setIsStaff(staffStatus === 'true');
    loadLocations();
  }, [loadLocations]);

  const buildTree = (items: Location[]): TreeNode[] => {
    try {
      // Filter out invalid items
      const validItems = items.filter((item) => {
        if (!item || typeof item.id !== 'number') {
          console.warn('Invalid location item:', item);
          return false;
        }
        return true;
      });

      if (validItems.length === 0) {
        return [];
      }

      const itemMap = new Map<number, TreeNode>();
      const roots: TreeNode[] = [];

      // First pass: create all nodes
      validItems.forEach((item) => {
        itemMap.set(item.id, {
          ...item,
          name: item.name || 'Unnamed Location',
        } as TreeNode);
      });

      // Second pass: build tree structure
      validItems.forEach((item) => {
        const node = itemMap.get(item.id);
        if (!node) {
          console.warn(`Node with id ${item.id} not found in map`);
          return;
        }
        
        // Handle parent relationship - check for null, undefined, or valid number
        if (item.parent === null || item.parent === undefined) {
          roots.push(node);
        } else if (typeof item.parent === 'number') {
          const parent = itemMap.get(item.parent);
          if (parent) {
            if (!parent.children) {
              parent.children = [];
            }
            parent.children.push(node);
          } else {
            // Parent not found, treat as root (orphaned node)
            console.warn(`Parent ${item.parent} not found for location ${item.id}, treating as root`);
            roots.push(node);
          }
        } else {
          // Invalid parent type, treat as root
          console.warn(`Invalid parent type for location ${item.id}:`, typeof item.parent);
          roots.push(node);
        }
      });

      return roots;
    } catch (err) {
      console.error('Error building tree:', err);
      // Return empty array instead of throwing to prevent component crash
      return [];
    }
  };

  const filteredLocations = useMemo(() => {
    if (!locations || !Array.isArray(locations)) {
      return [];
    }
    if (!searchTerm) return locations;
    const term = searchTerm.toLowerCase();
    return locations.filter(
      (loc) =>
        loc &&
        (loc.name?.toLowerCase().includes(term) ||
          loc.description?.toLowerCase().includes(term))
    );
  }, [locations, searchTerm]);

  const treeNodes = useMemo(() => {
    try {
      return buildTree(filteredLocations);
    } catch (err) {
      console.error('Error building tree nodes:', err);
      return [];
    }
  }, [filteredLocations]);

  const handleDelete = (id: number) => {
    confirmDelete('Are you sure you want to delete this location?', async () => {
      try {
        await inventoryAPI.deleteLocation(id.toString());
        await loadLocations();
      } catch (err: any) {
        showError(extractErrorMessage(err, 'Failed to delete location'));
      }
    });
  };

  const handleGenerateQR = async (id: number) => {
    try {
      await inventoryAPI.generateLocationQR(id.toString());
      await loadLocations();
      showSuccess('QR code generated successfully');
    } catch (err: any) {
      showError(extractErrorMessage(err, 'Failed to generate QR code'));
    }
  };

  const renderLocationNode = (node: TreeNode, level: number) => {
    const location = node as unknown as Location;
    
    // Defensive checks to prevent rendering errors
    if (!location || typeof location.id !== 'number') {
      console.error('Invalid location node:', location);
      return (
        <div className="location-row error">
          <div className="error-message">Invalid location data</div>
        </div>
      );
    }

    const locationName = location.name || 'Unnamed Location';
    const locationId = location.id;
    const isActive = location.is_active !== undefined ? location.is_active : true;
    const fixtureCount = location.fixture_count ?? 0;

    return (
      <div className="location-row">
        <div className="location-info">
          <div className="location-details">
            <div className="location-name">{locationName}</div>
            {location.description && (
              <div className="location-description">{location.description}</div>
            )}
          </div>
        </div>
        <div className="location-meta">
          <span className={`status-badge ${isActive ? 'active' : 'inactive'}`}>
            {isActive ? 'Active' : 'Inactive'}
          </span>
          <span className={`qr-status ${location.qr_code_url ? 'has-qr' : 'no-qr'}`}>
            {location.qr_code_url ? '✓ QR' : 'No QR'}
          </span>
          <span className="fixture-count">
            {fixtureCount} fixtures
          </span>
        </div>
        {isStaff && (
          <div className="location-actions">
            <Link
              to={`/inventory/locations/${locationId}`}
              className="btn-view"
            >
              View
            </Link>
            <Link
              to={`/inventory/locations/${locationId}/edit`}
              className="btn-edit"
            >
              Edit
            </Link>
            <button
              onClick={() => handleGenerateQR(locationId)}
              className="btn-qr"
              title="Generate QR Code"
            >
              QR
            </button>
            <button
              onClick={() => handleDelete(locationId)}
              className="btn-delete"
            >
              Delete
            </button>
          </div>
        )}
      </div>
    );
  };

  return (
    <WorkspacePage
      testId="location-list-page"
      hero={{
        eyebrow: 'Inventory',
        title: 'Locations',
        description: loading
          ? 'Loading…'
          : `${treeNodes.length} top-level location${treeNodes.length === 1 ? '' : 's'} in the tree.`,
        action: isStaff ? (
          <Button component={Link} to="/inventory/locations/new">
            Create location
          </Button>
        ) : undefined,
      }}
    >
      {error && (
        <Paper withBorder p="md" radius="md" bg="red.0" c="red.9">
          <Text>{error}</Text>
        </Paper>
      )}

      <Paper withBorder p="md">
        <TextInput
          placeholder="Search locations…"
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
        />
      </Paper>

      {loading ? (
        <Paper withBorder p="md">
          <Text c="dimmed">Loading locations…</Text>
        </Paper>
      ) : !treeNodes || treeNodes.length === 0 ? (
        <Paper withBorder p="xl" radius="md">
          <Stack gap="sm" align="center">
            <Text fw={500}>No locations found.</Text>
            {isStaff && (
              <Button component={Link} to="/inventory/locations/new">
                Create first location
              </Button>
            )}
          </Stack>
        </Paper>
      ) : (
        <Paper withBorder p="md" radius="md" className="location-tree">
          <TreeView
            nodes={treeNodes}
            renderNode={renderLocationNode}
            defaultExpanded={true}
          />
        </Paper>
      )}
    </WorkspacePage>
  );
};

export default LocationListPage;
