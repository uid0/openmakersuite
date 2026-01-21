/**
 * Committee List Page
 * Display committees with their Special Interest Groups (SIGs)
 */
import React, { useEffect, useState } from 'react';
import { committeeAPI, sigAPI } from '../services/api';
import '../styles/CommitteeListPage.css';
import { Committee, CreateSIGRequest, SIG } from '../types';

const CommitteeListPage: React.FC = () => {
  const [committees, setCommittees] = useState<Committee[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [selectedCommittee, setSelectedCommittee] = useState<number | null>(null);
  const [newSIGName, setNewSIGName] = useState('');
  const [newSIGDescription, setNewSIGDescription] = useState('');
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    loadCommittees();
  }, []);

  const loadCommittees = async () => {
    try {
      setLoading(true);
      const response = await committeeAPI.listCommittees();
      setCommittees(response.data.results);
      setError(null);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to load committees');
      console.error('Error loading committees:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleCreateSIG = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedCommittee || !newSIGName.trim()) {
      return;
    }

    try {
      setCreating(true);
      const data: CreateSIGRequest = {
        name: newSIGName.trim(),
        committee_id: selectedCommittee,
        description: newSIGDescription.trim() || undefined,
      };
      await sigAPI.createSIG(data);
      // Reload committees to show the new SIG
      await loadCommittees();
      // Reset form
      setShowCreateForm(false);
      setSelectedCommittee(null);
      setNewSIGName('');
      setNewSIGDescription('');
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Failed to create SIG');
      console.error('Error creating SIG:', err);
    } finally {
      setCreating(false);
    }
  };

  const userCanCreateSIG = (committee: Committee): boolean => {
    return committee.is_user_chair;
  };

  if (loading) {
    return <div className="committee-list-loading">Loading committees...</div>;
  }

  if (error) {
    return <div className="committee-list-error">Error: {error}</div>;
  }

  return (
    <div className="committee-list-page">
      <div className="committee-list-header">
        <h1>Committees & Special Interest Groups</h1>
        <button
          className="btn-create-sig"
          onClick={() => setShowCreateForm(!showCreateForm)}
        >
          {showCreateForm ? 'Cancel' : 'Create SIG'}
        </button>
      </div>

      {showCreateForm && (
        <div className="create-sig-form">
          <h2>Create New Special Interest Group</h2>
          <form onSubmit={handleCreateSIG}>
            <div className="form-group">
              <label htmlFor="committee-select">Committee:</label>
              <select
                id="committee-select"
                value={selectedCommittee || ''}
                onChange={(e) => setSelectedCommittee(parseInt(e.target.value))}
                required
              >
                <option value="">Select a committee...</option>
                {committees
                  .filter((c) => c.is_active && userCanCreateSIG(c))
                  .map((committee) => (
                    <option key={committee.id} value={committee.id}>
                      {committee.name}
                    </option>
                  ))}
              </select>
            </div>
            <div className="form-group">
              <label htmlFor="sig-name">SIG Name:</label>
              <input
                id="sig-name"
                type="text"
                value={newSIGName}
                onChange={(e) => setNewSIGName(e.target.value)}
                required
                placeholder="Enter SIG name"
              />
            </div>
            <div className="form-group">
              <label htmlFor="sig-description">Description (optional):</label>
              <textarea
                id="sig-description"
                value={newSIGDescription}
                onChange={(e) => setNewSIGDescription(e.target.value)}
                placeholder="Enter SIG description"
                rows={3}
              />
            </div>
            <div className="form-actions">
              <button type="submit" disabled={creating || !selectedCommittee || !newSIGName.trim()}>
                {creating ? 'Creating...' : 'Create SIG'}
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="committees-list">
        {committees.length === 0 ? (
          <div className="no-committees">No committees found.</div>
        ) : (
          committees
            .filter((c) => c.is_active)
            .map((committee) => (
              <div key={committee.id} className="committee-card">
                <div className="committee-header">
                  <h2>{committee.name}</h2>
                  {committee.is_user_chair && (
                    <span className="chair-badge">You are a Chair</span>
                  )}
                </div>
                {committee.description && (
                  <p className="committee-description">{committee.description}</p>
                )}
                <div className="committee-stats">
                  <span>{committee.sig_count} SIG{committee.sig_count !== 1 ? 's' : ''}</span>
                  {committee.chairs.length > 0 && (
                    <span>
                      Chairs: {committee.chairs.map((c) => c.handle || c.username).join(', ')}
                    </span>
                  )}
                </div>
                {committee.sigs.length > 0 ? (
                  <div className="sigs-list">
                    <h3>Special Interest Groups:</h3>
                    <ul>
                      {committee.sigs.map((sig) => (
                        <li key={sig.id} className="sig-item">
                          <div className="sig-info">
                            <span className="sig-name">{sig.name}</span>
                            <span className="sig-stats">
                              {sig.member_count} member{sig.member_count !== 1 ? 's' : ''} •{' '}
                              {sig.asset_count} asset{sig.asset_count !== 1 ? 's' : ''} •{' '}
                              {sig.inventory_count} inventory item{sig.inventory_count !== 1 ? 's' : ''}
                            </span>
                          </div>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : (
                  <div className="no-sigs">No SIGs in this committee yet.</div>
                )}
              </div>
            ))
        )}
      </div>
    </div>
  );
};

export default CommitteeListPage;
