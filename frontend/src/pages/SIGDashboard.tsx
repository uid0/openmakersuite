/**
 * SIG Dashboard
 * Main dashboard for SIG admins to manage their SIG's resources
 */
import React, { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { assetsAPI, inventoryAPI, reorderAPI, sigAPI } from '../services/api';
import { Asset, InventoryItem, ReorderRequest, SIG } from '../types';
import '../styles/SIGDashboard.css';

type TabType = 'overview' | 'members' | 'assets' | 'inventory' | 'reorders';

const SIGDashboard: React.FC = () => {
  const { sigId } = useParams<{ sigId?: string }>();
  const [activeTab, setActiveTab] = useState<TabType>('overview');
  const [sigs, setSigs] = useState<SIG[]>([]);
  const [selectedSIG, setSelectedSIG] = useState<SIG | null>(null);
  const [loading, setLoading] = useState(true);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [inventory, setInventory] = useState<InventoryItem[]>([]);
  const [reorderRequests, setReorderRequests] = useState<ReorderRequest[]>([]);
  const [members, setMembers] = useState<any[]>([]);

  useEffect(() => {
    loadSIGs();
  }, []);

  useEffect(() => {
    if (sigId && sigs.length > 0) {
      const sig = sigs.find((s) => s.id === parseInt(sigId));
      if (sig) {
        setSelectedSIG(sig);
      }
    } else if (sigs.length > 0) {
      setSelectedSIG(sigs[0]);
    }
  }, [sigId, sigs]);

  useEffect(() => {
    if (selectedSIG) {
      loadSIGData();
    }
  }, [selectedSIG, activeTab]);

  const loadSIGs = async () => {
    try {
      setLoading(true);
      const response = await sigAPI.listMySIGs();
      setSigs(response.data.results || []);
    } catch (error) {
      console.error('Error loading SIGs:', error);
    } finally {
      setLoading(false);
    }
  };

  const loadSIGData = async () => {
    if (!selectedSIG) return;

    try {
      if (activeTab === 'assets') {
        const response = await assetsAPI.listAssets({ owning_group: selectedSIG.id });
        setAssets(response.data.results || []);
      } else if (activeTab === 'inventory') {
        const response = await inventoryAPI.listItems({ owning_group: selectedSIG.id });
        setInventory(response.data.results || []);
      } else if (activeTab === 'reorders') {
        const response = await reorderAPI.getSIGPendingRequests();
        setReorderRequests(response.data || []);
      } else if (activeTab === 'members') {
        const response = await sigAPI.getSIGMembers(selectedSIG.id);
        setMembers(response.data || []);
      }
    } catch (error) {
      console.error(`Error loading ${activeTab}:`, error);
    }
  };

  if (loading) {
    return <div className="sig-dashboard-loading">Loading SIGs...</div>;
  }

  if (sigs.length === 0) {
    return (
      <div className="sig-dashboard-empty">
        <h1>SIG Dashboard</h1>
        <p>You are not an admin of any SIGs.</p>
      </div>
    );
  }

  return (
    <div className="sig-dashboard">
      <div className="sig-dashboard-header">
        <h1>SIG Dashboard</h1>
        {sigs.length > 1 && (
          <select
            value={selectedSIG?.id || ''}
            onChange={(e) => {
              const sig = sigs.find((s) => s.id === parseInt(e.target.value));
              setSelectedSIG(sig || null);
            }}
            className="sig-selector"
          >
            {sigs.map((sig) => (
              <option key={sig.id} value={sig.id}>
                {sig.name}
              </option>
            ))}
          </select>
        )}
      </div>

      {selectedSIG && (
        <>
          <div className="sig-tabs">
            <button
              className={activeTab === 'overview' ? 'active' : ''}
              onClick={() => setActiveTab('overview')}
            >
              Overview
            </button>
            <button
              className={activeTab === 'members' ? 'active' : ''}
              onClick={() => setActiveTab('members')}
            >
              Members ({selectedSIG.member_count})
            </button>
            <button
              className={activeTab === 'assets' ? 'active' : ''}
              onClick={() => setActiveTab('assets')}
            >
              Assets ({selectedSIG.asset_count})
            </button>
            <button
              className={activeTab === 'inventory' ? 'active' : ''}
              onClick={() => setActiveTab('inventory')}
            >
              Inventory ({selectedSIG.inventory_count})
            </button>
            <button
              className={activeTab === 'reorders' ? 'active' : ''}
              onClick={() => setActiveTab('reorders')}
            >
              Reorder Requests
            </button>
          </div>

          <div className="sig-content">
            {activeTab === 'overview' && (
              <div className="sig-overview">
                <h2>{selectedSIG.name}</h2>
                <div className="sig-stats">
                  <div className="stat-card">
                    <h3>Members</h3>
                    <p className="stat-value">{selectedSIG.member_count}</p>
                  </div>
                  <div className="stat-card">
                    <h3>Assets</h3>
                    <p className="stat-value">{selectedSIG.asset_count}</p>
                  </div>
                  <div className="stat-card">
                    <h3>Inventory Items</h3>
                    <p className="stat-value">{selectedSIG.inventory_count}</p>
                  </div>
                </div>
                <div className="sig-admins">
                  <h3>Admins</h3>
                  <ul>
                    {selectedSIG.admins.map((admin) => (
                      <li key={admin.id}>
                        {admin.handle || admin.username} ({admin.email})
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            )}

            {activeTab === 'members' && (
              <div className="sig-members">
                <h2>Members</h2>
                <table>
                  <thead>
                    <tr>
                      <th>Username</th>
                      <th>Email</th>
                      <th>Handle</th>
                      <th>Admin</th>
                    </tr>
                  </thead>
                  <tbody>
                    {members.map((member) => (
                      <tr key={member.id}>
                        <td>{member.username}</td>
                        <td>{member.email}</td>
                        <td>{member.handle || '-'}</td>
                        <td>{member.is_sig_admin ? 'Yes' : 'No'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {activeTab === 'assets' && (
              <div className="sig-assets">
                <h2>Assets</h2>
                <div className="asset-list">
                  {assets.map((asset) => (
                    <div key={asset.id} className="asset-card">
                      <h3>{asset.name}</h3>
                      <p>{asset.description}</p>
                      <p>Status: {asset.status}</p>
                      <p>Location: {asset.location_name || 'Unknown'}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {activeTab === 'inventory' && (
              <div className="sig-inventory">
                <h2>Inventory Items</h2>
                <div className="inventory-list">
                  {inventory.map((item) => (
                    <div key={item.id} className="inventory-card">
                      <h3>{item.name}</h3>
                      <p>Stock: {item.current_stock} / {item.minimum_stock}</p>
                      <p>SKU: {item.sku}</p>
                      {item.needs_reorder && <span className="needs-reorder">Needs Reorder</span>}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {activeTab === 'reorders' && (
              <div className="sig-reorders">
                <h2>Pending Reorder Requests</h2>
                <div className="reorder-list">
                  {reorderRequests.map((request) => (
                    <div key={request.id} className="reorder-card">
                      <h3>{request.item_details?.name || 'Unknown Item'}</h3>
                      <p>Quantity: {request.quantity}</p>
                      <p>Priority: {request.priority}</p>
                      <p>Requested by: {request.requested_by}</p>
                      <p>Requested at: {new Date(request.requested_at).toLocaleString()}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
};

export default SIGDashboard;

