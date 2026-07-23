/**
 * Asset Problem Detail Page (op-ybpn).
 *
 * The asset-side twin of LocationProblemDetailPage: one reported problem with
 * its photos, flagged components, and the promote-to-work-order / resolve
 * actions. Mutations patch the visible problem from the response rather than
 * re-fetching — see docs/REACTIVE_MUTATIONS.md.
 *
 * Unlike the location sibling there is no MaintenanceItem picker: a corrective
 * work order anchors straight to the problem's asset.
 */
import { Button, Paper, Text } from '@mantine/core';
import React, { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import api, { assetProblemsAPI } from '../services/api';
import { AssetProblem } from '../types';
import { showError, showSuccess } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';

interface VendorOption {
  id: string;
  name: string;
}

const STATUS_LABELS: Record<string, string> = {
  reported: 'Reported',
  in_progress: 'In Progress',
  resolved: 'Resolved',
  closed: 'Closed',
};

const WORK_TYPES: Array<{ value: string; label: string }> = [
  { value: 'standard', label: 'Standard' },
  { value: 'major_repair', label: 'Major Repair' },
  { value: 'buildout', label: 'Buildout' },
  { value: 'building_emergency', label: 'Building Emergency' },
];

const AssetProblemDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [problem, setProblem] = useState<AssetProblem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [pendingAction, setPendingAction] =
    useState<'promote-standard' | 'promote-tp' | 'resolve' | null>(null);

  const [vendors, setVendors] = useState<VendorOption[]>([]);
  const [tpVendor, setTpVendor] = useState<string>('');
  const [tpTitle, setTpTitle] = useState<string>('');
  const [tpWorkType, setTpWorkType] = useState<string>('standard');

  const [resolutionNotes, setResolutionNotes] = useState<string>('');

  useEffect(() => {
    setIsLoggedIn(Boolean(localStorage.getItem('token')));
  }, []);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    const load = async () => {
      try {
        setLoading(true);
        setError(null);
        const resp = await assetProblemsAPI.get(id);
        if (!cancelled) setProblem(resp?.data ?? null);
      } catch (err: any) {
        if (!cancelled) setError(extractErrorMessage(err, 'Failed to load'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [id]);

  useEffect(() => {
    if (!isLoggedIn) return;
    let cancelled = false;
    const load = async () => {
      try {
        const resp = await api.get<{ results: VendorOption[] } | VendorOption[]>(
          '/vendors/vendors/',
        );
        if (cancelled) return;
        const body = resp?.data;
        setVendors(Array.isArray(body) ? body : body?.results ?? []);
      } catch {
        // Non-fatal: the picker stays empty and the in-house path still works.
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [isLoggedIn]);

  // Patch the visible problem from a mutation response. Partial merge keeps a
  // thin mock body from blanking the panel; production returns the full object.
  const applyProblemUpdate = (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    setProblem((prev) =>
      prev ? ({ ...prev, ...(data as Partial<AssetProblem>) }) : (data as AssetProblem),
    );
  };

  const handlePromoteStandard = async () => {
    if (!id || pendingAction) return;
    try {
      setPendingAction('promote-standard');
      const resp = await assetProblemsAPI.promoteStandard(id);
      applyProblemUpdate(resp?.data);
      showSuccess('Work order opened.');
    } catch (err: any) {
      showError(extractErrorMessage(err, 'Failed to promote.'));
    } finally {
      setPendingAction(null);
    }
  };

  const handlePromoteThirdParty = async () => {
    if (!id) return;
    if (!tpVendor || !tpTitle.trim()) {
      showError('Vendor and title are required.');
      return;
    }
    if (pendingAction) return;
    try {
      setPendingAction('promote-tp');
      const resp = await assetProblemsAPI.promoteThirdParty(id, {
        vendor: tpVendor,
        title: tpTitle.trim(),
        work_type: tpWorkType,
      });
      applyProblemUpdate(resp?.data);
      showSuccess('Vendor work order opened.');
    } catch (err: any) {
      showError(extractErrorMessage(err, 'Failed to promote.'));
    } finally {
      setPendingAction(null);
    }
  };

  const handleResolve = async (status: 'resolved' | 'closed') => {
    if (!id || pendingAction) return;
    try {
      setPendingAction('resolve');
      const resp = await assetProblemsAPI.resolve(id, {
        status,
        resolution_notes: resolutionNotes.trim() || undefined,
      });
      applyProblemUpdate(resp?.data);
      showSuccess(`Problem marked ${status}.`);
    } catch (err: any) {
      showError(extractErrorMessage(err, 'Failed to resolve.'));
    } finally {
      setPendingAction(null);
    }
  };

  if (loading) {
    return (
      <WorkspacePage
        testId="asset-problem-detail-page"
        hero={{
          eyebrow: 'Maintenance · Asset problem',
          title: 'Asset problem',
          description: 'Loading…',
        }}
      >
        <Paper withBorder p="md">
          <Text c="dimmed">Loading problem…</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  if (error || !problem) {
    return (
      <WorkspacePage
        testId="asset-problem-detail-page"
        hero={{
          eyebrow: 'Maintenance · Asset problem',
          title: 'Asset problem',
          description: error || 'Not found.',
          action: (
            <Button component={Link} to="/maintenance" variant="default">
              Back to maintenance
            </Button>
          ),
        }}
      >
        <Paper withBorder p="md" radius="md" bg="red.0" c="red.9">
          <Text>{error || 'Problem not found.'}</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  const isPromoted =
    Boolean(problem.work_order) || Boolean(problem.third_party_work_order);
  const isResolved = problem.status === 'resolved' || problem.status === 'closed';

  return (
    <WorkspacePage
      testId="asset-problem-detail-page"
      hero={{
        eyebrow: `Maintenance · ${problem.asset_name}`,
        title: 'Asset problem',
        description: STATUS_LABELS[problem.status] || problem.status,
        action: (
          <Button component={Link} to={`/assets/${problem.asset}`} variant="default">
            Open asset
          </Button>
        ),
      }}
    >
      <div className="page asset-problem-detail-page">
        <div className="header-actions" style={{ marginBottom: '1rem' }}>
          <span className={`status-badge ${problem.status}`}>
            {STATUS_LABELS[problem.status] || problem.status}
          </span>
        </div>

        <section className="detail-section">
          <h2>Description</h2>
          <p>{problem.description}</p>
          {problem.reported_by && (
            <p className="muted">
              Reported by {problem.reported_by} on{' '}
              {new Date(problem.created_at).toLocaleString()}
            </p>
          )}
        </section>

        {problem.affected_parts && problem.affected_parts.length > 0 && (
          <section className="detail-section">
            <h2>Components Flagged</h2>
            <ul>
              {problem.affected_parts.map((part) => (
                <li key={part.id}>
                  {part.part_name}
                  {part.quantity_needed ? ` (qty ${part.quantity_needed})` : ''}
                </li>
              ))}
            </ul>
          </section>
        )}

        {problem.photos && problem.photos.length > 0 && (
          <section className="detail-section">
            <h2>Photos</h2>
            {problem.photos.map((photo) => (
              <p key={photo.id}>
                <a href={photo.image_url || '#'} target="_blank" rel="noreferrer">
                  {photo.caption || 'Reporter photo'}
                </a>
              </p>
            ))}
          </section>
        )}

        {(problem.work_order_short_id || problem.third_party_work_order_short_id) && (
          <section className="detail-section">
            <h2>Promoted To</h2>
            {problem.work_order_short_id && (
              <p>
                Work Order:{' '}
                <Link to={`/maintenance/work-orders/${problem.work_order}`}>
                  {problem.work_order_short_id}
                </Link>
              </p>
            )}
            {problem.third_party_work_order_short_id && (
              <p>
                Vendor Work Order:{' '}
                <Link to={`/maintenance/third-party/${problem.third_party_work_order}`}>
                  {problem.third_party_work_order_short_id}
                </Link>
              </p>
            )}
          </section>
        )}

        {problem.resolution_notes && (
          <section className="detail-section">
            <h2>Resolution</h2>
            <p>{problem.resolution_notes}</p>
          </section>
        )}

        {isLoggedIn && !isPromoted && !isResolved && (
          <>
            <section className="detail-section">
              <h2>Create Work Order</h2>
              <p className="muted">
                Opens an in-house corrective work order against {problem.asset_name}.
              </p>
              <button
                type="button"
                className="btn-primary"
                onClick={handlePromoteStandard}
                disabled={pendingAction !== null}
              >
                {pendingAction === 'promote-standard' ? 'Creating…' : 'Create Work Order'}
              </button>
            </section>

            <section className="detail-section">
              <h2>Send to Vendor</h2>
              <label htmlFor="ap-vendor">Vendor</label>
              <select
                id="ap-vendor"
                value={tpVendor}
                onChange={(e) => setTpVendor(e.target.value)}
              >
                <option value="">Select…</option>
                {vendors.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </select>
              <label htmlFor="ap-title">Title</label>
              <input
                id="ap-title"
                type="text"
                value={tpTitle}
                onChange={(e) => setTpTitle(e.target.value)}
                placeholder="Short summary of the work"
              />
              <label htmlFor="ap-worktype">Work Type</label>
              <select
                id="ap-worktype"
                value={tpWorkType}
                onChange={(e) => setTpWorkType(e.target.value)}
              >
                {WORK_TYPES.map((wt) => (
                  <option key={wt.value} value={wt.value}>
                    {wt.label}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="btn-primary"
                onClick={handlePromoteThirdParty}
                disabled={!tpVendor || !tpTitle.trim() || pendingAction !== null}
              >
                {pendingAction === 'promote-tp' ? 'Sending…' : 'Open Vendor Work Order'}
              </button>
            </section>
          </>
        )}

        {isLoggedIn && !isResolved && (
          <section className="detail-section">
            <h2>Resolve</h2>
            <label htmlFor="ap-notes">Resolution notes</label>
            <textarea
              id="ap-notes"
              rows={3}
              value={resolutionNotes}
              onChange={(e) => setResolutionNotes(e.target.value)}
            />
            <div className="modal-actions">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => handleResolve('resolved')}
                disabled={pendingAction !== null}
                aria-busy={pendingAction === 'resolve' ? 'true' : undefined}
              >
                Mark Resolved
              </button>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => handleResolve('closed')}
                disabled={pendingAction !== null}
                aria-busy={pendingAction === 'resolve' ? 'true' : undefined}
              >
                Mark Closed
              </button>
            </div>
          </section>
        )}
      </div>
    </WorkspacePage>
  );
};

export default AssetProblemDetailPage;
