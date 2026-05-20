/**
 * Location Problem Detail Page (oms-sd1).
 *
 * Shows a single LocationProblem with reporter info, photo, paper-form
 * attachment, and promote-to-WO / resolve actions for staff. Resolve and
 * promote mutations patch the visible problem from the response — see
 * docs/REACTIVE_MUTATIONS.md. The "Loading problem…" placeholder is
 * reserved for the initial fetch.
 */
import { Button, Paper, Text } from '@mantine/core';
import React, { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import api, { locationProblemsAPI, maintenanceAPI } from '../services/api';
import { LocationProblem, MaintenanceItem } from '../types';
import { showError, showSuccess } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';

interface VendorOption {
  id: string;
  name: string;
}

const SEVERITY_COLORS: Record<string, string> = {
  low: '#6c757d',
  medium: '#17a2b8',
  high: '#fd7e14',
  urgent: '#dc3545',
};

const LocationProblemDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [problem, setProblem] = useState<LocationProblem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isStaff, setIsStaff] = useState(false);
  const [pendingAction, setPendingAction] =
    useState<'promote-standard' | 'promote-tp' | 'resolve' | null>(null);

  // Promote-standard inputs
  const [maintenanceItems, setMaintenanceItems] = useState<MaintenanceItem[]>([]);
  const [chosenItem, setChosenItem] = useState<string>('');

  // Promote-third-party inputs
  const [vendors, setVendors] = useState<VendorOption[]>([]);
  const [tpVendor, setTpVendor] = useState<string>('');
  const [tpTitle, setTpTitle] = useState<string>('');
  const [tpWorkType, setTpWorkType] = useState<string>('standard');

  // Resolve inputs
  const [resolutionNotes, setResolutionNotes] = useState<string>('');

  useEffect(() => {
    setIsStaff(localStorage.getItem('is_staff') === 'true');
  }, []);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    const load = async () => {
      try {
        setLoading(true);
        setError(null);
        const resp = await locationProblemsAPI.get(id);
        if (!cancelled) setProblem(resp.data);
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
    if (!isStaff) return;
    const load = async () => {
      try {
        const [mi, vd] = await Promise.all([
          maintenanceAPI.listItems({ is_active: true }),
          api.get<{ results: VendorOption[] }>('/vendors/vendors/'),
        ]);
        setMaintenanceItems(mi.data.results || []);
        const vData = vd.data;
        setVendors(Array.isArray(vData) ? vData : vData.results || []);
      } catch (err) {
        // Silently — staff can still type ids manually if needed
      }
    };
    load();
  }, [isStaff]);

  // Patch the visible problem from a mutation response. Falls back to a
  // partial merge so unit tests with a {} mock body don't blank out the
  // panel; in production these endpoints return the full LocationProblem.
  const applyProblemUpdate = (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    setProblem((prev) =>
      prev ? ({ ...prev, ...(data as Partial<LocationProblem>) }) : (data as LocationProblem),
    );
  };

  const handlePromoteStandard = async () => {
    if (!id || !chosenItem) {
      showError('Choose a maintenance item to anchor the work order under.');
      return;
    }
    if (pendingAction) return;
    try {
      setPendingAction('promote-standard');
      const resp = await locationProblemsAPI.promoteStandard(id, chosenItem);
      applyProblemUpdate(resp.data);
      showSuccess('Standard work order opened.');
    } catch (err: any) {
      showError(extractErrorMessage(err, 'Failed to promote.'));
    } finally {
      setPendingAction(null);
    }
  };

  const handlePromoteThirdParty = async () => {
    if (!id || !tpVendor || !tpTitle.trim()) {
      showError('Vendor and title are required.');
      return;
    }
    if (pendingAction) return;
    try {
      setPendingAction('promote-tp');
      const resp = await locationProblemsAPI.promoteThirdParty(id, {
        vendor: tpVendor,
        title: tpTitle.trim(),
        work_type: tpWorkType,
      });
      applyProblemUpdate(resp.data);
      showSuccess('Third-party work order opened.');
    } catch (err: any) {
      showError(extractErrorMessage(err, 'Failed to promote.'));
    } finally {
      setPendingAction(null);
    }
  };

  const handleResolve = async (status: 'resolved' | 'closed') => {
    if (!id) return;
    if (pendingAction) return;
    try {
      setPendingAction('resolve');
      const resp = await locationProblemsAPI.resolve(id, {
        status,
        resolution_notes: resolutionNotes.trim() || undefined,
      });
      applyProblemUpdate(resp.data);
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
        testId="location-problem-detail-page"
        hero={{
          eyebrow: 'Maintenance · Location problem',
          title: 'Location problem',
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
        testId="location-problem-detail-page"
        hero={{
          eyebrow: 'Maintenance · Location problem',
          title: 'Location problem',
          description: error || 'Not found.',
          action: (
            <Button component={Link} to="/inventory/locations" variant="default">
              Back to locations
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
      testId="location-problem-detail-page"
      hero={{
        eyebrow: `Maintenance · ${problem.location_name}`,
        title: 'Location problem',
        description: problem.severity_display,
      }}
    >
      <div className="page location-problem-detail-page">
        <div className="header-actions" style={{ marginBottom: '1rem' }}>
          <span
            className="lp-severity-badge"
            style={{
              background: SEVERITY_COLORS[problem.severity] || '#6c757d',
              color: 'white',
              padding: '4px 8px',
              borderRadius: 4,
            }}
          >
            {problem.severity_display}
          </span>
          <span className={`status-badge ${problem.status}`}>
            {problem.status_display}
          </span>
        </div>

      <section className="detail-section">
        <h2>Description</h2>
        <p>{problem.description}</p>
        {problem.reported_by && (
          <p className="muted">
            Reported by {problem.reported_by} on{' '}
            {new Date(problem.reported_at).toLocaleString()}
          </p>
        )}
      </section>

      {(problem.photo_url || problem.paper_form_url) && (
        <section className="detail-section">
          <h2>Attachments</h2>
          {problem.photo_url && (
            <p>
              <a href={problem.photo_url} target="_blank" rel="noreferrer">
                Reporter photo
              </a>
            </p>
          )}
          {problem.paper_form_url && (
            <p>
              <a href={problem.paper_form_url} target="_blank" rel="noreferrer">
                Original paper form (PDF)
              </a>
            </p>
          )}
        </section>
      )}

      {(problem.work_order_short_id || problem.third_party_work_order_short_id) && (
        <section className="detail-section">
          <h2>Promoted To</h2>
          {problem.work_order_short_id && (
            <p>Standard PM Work Order: {problem.work_order_short_id}</p>
          )}
          {problem.third_party_work_order_short_id && (
            <p>
              Third-Party Work Order:{' '}
              <Link
                to={`/maintenance/third-party-work-orders/${problem.third_party_work_order}`}
              >
                {problem.third_party_work_order_short_id}
              </Link>
            </p>
          )}
        </section>
      )}

      {isStaff && !isPromoted && !isResolved && (
        <>
          <section className="detail-section">
            <h2>Open Standard Work Order</h2>
            <label htmlFor="lp-mi">Maintenance item</label>
            <select
              id="lp-mi"
              value={chosenItem}
              onChange={(e) => setChosenItem(e.target.value)}
            >
              <option value="">Select…</option>
              {maintenanceItems.map((mi) => (
                <option key={mi.id} value={mi.id}>
                  {mi.title}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn-primary"
              onClick={handlePromoteStandard}
              disabled={!chosenItem || pendingAction !== null}
            >
              {pendingAction === 'promote-standard' ? 'Opening…' : 'Open Work Order'}
            </button>
          </section>

          <section className="detail-section">
            <h2>Open 3rd-Party Work Order</h2>
            <label htmlFor="lp-vendor">Vendor</label>
            <select
              id="lp-vendor"
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
            <label htmlFor="lp-title">Title</label>
            <input
              id="lp-title"
              type="text"
              value={tpTitle}
              onChange={(e) => setTpTitle(e.target.value)}
              placeholder="Short summary of the work"
            />
            <label htmlFor="lp-worktype">Work Type</label>
            <select
              id="lp-worktype"
              value={tpWorkType}
              onChange={(e) => setTpWorkType(e.target.value)}
            >
              <option value="standard">Standard</option>
              <option value="major_repair">Major Repair</option>
              <option value="buildout">Buildout</option>
              <option value="building_emergency">Building Emergency</option>
            </select>
            <button
              type="button"
              className="btn-primary"
              onClick={handlePromoteThirdParty}
              disabled={!tpVendor || !tpTitle.trim() || pendingAction !== null}
            >
              {pendingAction === 'promote-tp' ? 'Opening…' : 'Open 3rd-Party Work Order'}
            </button>
          </section>
        </>
      )}

      {isStaff && !isResolved && (
        <section className="detail-section">
          <h2>Resolve</h2>
          <label htmlFor="lp-notes">Resolution notes</label>
          <textarea
            id="lp-notes"
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

export default LocationProblemDetailPage;
