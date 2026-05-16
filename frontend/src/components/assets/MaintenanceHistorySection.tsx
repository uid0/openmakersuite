/**
 * Maintenance History section for the asset detail page.
 *
 * Shows a unified, date-sorted list of:
 *   - backdated/recent MaintenanceRecord rows (source: "historical")
 *   - closed ThirdPartyWorkOrder rows that touched this asset (source: "workorder")
 *
 * Supports a date range, source filter chips, a "Log Historical Work" form
 * for staff/SIG admins, and a cost rollup pill summarizing the current range.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';

import {
  MaintenanceHistoryResponse,
  MaintenanceHistoryRow,
  MaintenanceRecordCreatePayload,
  assetsAPI,
  maintenanceRecordsAPI,
} from '../../services/api';
import api from '../../services/api';

interface VendorOption {
  id: string;
  name: string;
}

interface MaintenanceHistorySectionProps {
  assetId: string;
  canManage: boolean;
}

type SourceFilter = 'all' | 'historical' | 'workorder';

const DEFAULT_RANGE_YEARS = 3;

const toIsoDate = (d: Date): string => d.toISOString().slice(0, 10);

const defaultSince = (): string => {
  const d = new Date();
  d.setFullYear(d.getFullYear() - DEFAULT_RANGE_YEARS);
  return toIsoDate(d);
};

const todayIso = (): string => toIsoDate(new Date());

const performerLabel = (row: MaintenanceHistoryRow): string => {
  const pieces: string[] = [];
  if (row.performed_by.vendor) pieces.push(row.performed_by.vendor.name);
  if (row.performed_by.internal_user) {
    pieces.push(`@${row.performed_by.internal_user.username}`);
  }
  return pieces.length > 0 ? pieces.join(' / ') : '—';
};

const sourceLabel = (s: 'historical' | 'workorder'): string =>
  s === 'workorder' ? 'Outsourced WO' : 'Logged Work';

const MaintenanceHistorySection: React.FC<MaintenanceHistorySectionProps> = ({
  assetId,
  canManage,
}) => {
  const [since, setSince] = useState<string>(defaultSince());
  const [until, setUntil] = useState<string>(todayIso());
  const [source, setSource] = useState<SourceFilter>('all');
  const [data, setData] = useState<MaintenanceHistoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [vendors, setVendors] = useState<VendorOption[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [formTitle, setFormTitle] = useState('');
  const [formDescription, setFormDescription] = useState('');
  const [formCompletedOn, setFormCompletedOn] = useState(todayIso());
  const [formPerformerKind, setFormPerformerKind] = useState<'vendor' | 'internal'>('vendor');
  const [formVendorId, setFormVendorId] = useState('');
  const [formInternalUsername, setFormInternalUsername] = useState('');
  const [formCost, setFormCost] = useState('');
  const [formInvoiceNumber, setFormInvoiceNumber] = useState('');
  const [formNotes, setFormNotes] = useState('');
  const [formAttachment, setFormAttachment] = useState<File | null>(null);

  // Inline-edit state for historical rows. Only one row is editable at a
  // time; the edit panel renders below the table once a row is selected.
  const [editingRowId, setEditingRowId] = useState<string | null>(null);
  const [editNotes, setEditNotes] = useState('');
  const [editAttachment, setEditAttachment] = useState<File | null>(null);
  const [editSubmitting, setEditSubmitting] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const openEdit = useCallback((row: MaintenanceHistoryRow) => {
    setEditingRowId(row.id);
    setEditNotes(row.notes ?? '');
    setEditAttachment(null);
    setEditError(null);
  }, []);

  const closeEdit = useCallback(() => {
    setEditingRowId(null);
    setEditNotes('');
    setEditAttachment(null);
    setEditError(null);
  }, []);

  const handleEditSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!editingRowId) return;
      setEditError(null);
      setEditSubmitting(true);
      try {
        const payload: Partial<MaintenanceRecordCreatePayload> = {
          notes: editNotes,
        };
        if (editAttachment) {
          payload.attachment = editAttachment;
        }
        await maintenanceRecordsAPI.update(editingRowId, payload);
        closeEdit();
        await loadHistory();
      } catch (err: any) {
        const detail =
          err?.response?.data?.error?.message ||
          err?.response?.data?.detail ||
          err?.message ||
          'Failed to update maintenance record';
        setEditError(detail);
      } finally {
        setEditSubmitting(false);
      }
    },
    [editingRowId, editNotes, editAttachment, closeEdit],
  );

  const loadHistory = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const resp = await assetsAPI.getMaintenanceHistory(assetId, {
        since: since || undefined,
        until: until || undefined,
        source,
      });
      setData(resp.data);
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Failed to load maintenance history');
    } finally {
      setLoading(false);
    }
  }, [assetId, since, until, source]);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  useEffect(() => {
    if (!canManage) return;
    let cancelled = false;
    (async () => {
      try {
        const resp = await api.get<{ results: VendorOption[] } | VendorOption[]>(
          '/vendors/vendors/',
        );
        if (cancelled) return;
        const body = resp.data;
        const list = Array.isArray(body) ? body : body.results || [];
        setVendors(list);
      } catch {
        // Non-fatal: staff can still leave vendor blank and log internal work.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [canManage]);

  const totalCostDisplay = useMemo(() => {
    if (!data) return '0.00';
    const n = Number(data.total_cost);
    return Number.isFinite(n) ? n.toFixed(2) : data.total_cost;
  }, [data]);

  const resetForm = () => {
    setFormTitle('');
    setFormDescription('');
    setFormCompletedOn(todayIso());
    setFormPerformerKind('vendor');
    setFormVendorId('');
    setFormInternalUsername('');
    setFormCost('');
    setFormInvoiceNumber('');
    setFormNotes('');
    setFormAttachment(null);
    setFormError(null);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    if (!formTitle.trim() || !formDescription.trim()) {
      setFormError('Title and description are required.');
      return;
    }
    if (!formCompletedOn) {
      setFormError('Completion date is required.');
      return;
    }
    if (formCompletedOn > todayIso()) {
      setFormError('Completion date cannot be in the future.');
      return;
    }
    if (formPerformerKind === 'vendor' && !formVendorId) {
      setFormError('Pick a vendor or switch to Internal staff.');
      return;
    }

    setSubmitting(true);
    try {
      let internalUserId: number | undefined;
      if (formPerformerKind === 'internal' && formInternalUsername.trim()) {
        try {
          const resp = await api.get<{ id: number }[] | { results: { id: number }[] }>(
            '/auth/users/',
            { params: { username: formInternalUsername.trim() } },
          );
          const body = resp.data as any;
          const list = Array.isArray(body) ? body : body.results || [];
          if (list.length > 0 && list[0].id) {
            internalUserId = list[0].id;
          }
        } catch {
          // Best-effort: if the user lookup endpoint isn't available the form
          // still posts with vendor=null and the API will reject if neither
          // performer field is set.
        }
      }

      const payload: MaintenanceRecordCreatePayload = {
        asset: assetId,
        title: formTitle.trim(),
        description: formDescription.trim(),
        completed_on: formCompletedOn,
        vendor: formPerformerKind === 'vendor' ? formVendorId : null,
        performed_by_internal:
          formPerformerKind === 'internal' ? internalUserId ?? null : null,
        cost: formCost.trim() || null,
        invoice_number: formInvoiceNumber.trim(),
        notes: formNotes.trim(),
        attachment: formAttachment,
      };
      await maintenanceRecordsAPI.create(payload);
      resetForm();
      setShowForm(false);
      await loadHistory();
    } catch (err: any) {
      const detail =
        err?.response?.data?.error?.message ||
        err?.response?.data?.detail ||
        err?.message ||
        'Failed to log maintenance record';
      setFormError(detail);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="asset-detail-section" data-testid="asset-maintenance-history">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: '1rem',
          flexWrap: 'wrap',
          gap: '0.5rem',
        }}
      >
        <h2 style={{ margin: 0 }}>Maintenance History</h2>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <span
            data-testid="maintenance-history-rollup"
            style={{
              background: '#eef3f8',
              border: '1px solid #cdd9e4',
              borderRadius: 999,
              padding: '0.25rem 0.75rem',
              fontSize: '0.85rem',
            }}
          >
            Total in range: ${totalCostDisplay} across {data?.count ?? 0} records
          </span>
          {canManage && (
            <button
              className="action-button"
              type="button"
              onClick={() => setShowForm((v) => !v)}
              style={{ padding: '0.4rem 0.85rem' }}
            >
              {showForm ? 'Cancel' : '+ Log Historical Work'}
            </button>
          )}
        </div>
      </div>

      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: '1rem',
          alignItems: 'flex-end',
          marginBottom: '1rem',
        }}
      >
        <div>
          <label htmlFor="mh-since" style={{ display: 'block', fontSize: '0.85rem' }}>
            Since
          </label>
          <input
            id="mh-since"
            type="date"
            value={since}
            max={until || undefined}
            onChange={(e) => setSince(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="mh-until" style={{ display: 'block', fontSize: '0.85rem' }}>
            Until
          </label>
          <input
            id="mh-until"
            type="date"
            value={until}
            min={since || undefined}
            onChange={(e) => setUntil(e.target.value)}
          />
        </div>
        <div role="group" aria-label="Source filter" style={{ display: 'flex', gap: '0.25rem' }}>
          {(['all', 'workorder', 'historical'] as const).map((opt) => (
            <button
              type="button"
              key={opt}
              onClick={() => setSource(opt)}
              aria-pressed={source === opt}
              style={{
                padding: '0.35rem 0.75rem',
                borderRadius: 999,
                border: '1px solid #c2cad3',
                background: source === opt ? '#1c7ed6' : '#fff',
                color: source === opt ? '#fff' : '#333',
                cursor: 'pointer',
                fontSize: '0.85rem',
              }}
            >
              {opt === 'all' ? 'All' : opt === 'workorder' ? 'Outsourced WO' : 'Logged Work'}
            </button>
          ))}
        </div>
      </div>

      {showForm && canManage && (
        <form
          onSubmit={handleSubmit}
          style={{
            border: '1px solid #ddd',
            borderRadius: 6,
            padding: '1rem',
            marginBottom: '1rem',
            background: '#f9fbfd',
          }}
        >
          <h3 style={{ marginTop: 0 }}>Log Historical Work</h3>
          {formError && (
            <p style={{ color: '#c92a2a', marginTop: 0 }}>{formError}</p>
          )}
          <div style={{ display: 'grid', gap: '0.75rem' }}>
            <label>
              Title *
              <input
                type="text"
                required
                value={formTitle}
                onChange={(e) => setFormTitle(e.target.value)}
                style={{ width: '100%', padding: '0.4rem' }}
              />
            </label>
            <label>
              Description *
              <textarea
                required
                rows={3}
                value={formDescription}
                onChange={(e) => setFormDescription(e.target.value)}
                style={{ width: '100%', padding: '0.4rem' }}
              />
            </label>
            <label>
              Completed on *
              <input
                type="date"
                required
                value={formCompletedOn}
                max={todayIso()}
                onChange={(e) => setFormCompletedOn(e.target.value)}
              />
            </label>
            <fieldset style={{ border: '1px solid #e0e0e0', borderRadius: 4, padding: '0.5rem' }}>
              <legend style={{ padding: '0 0.5rem', fontSize: '0.85rem' }}>Performed by</legend>
              <label style={{ marginRight: '1rem' }}>
                <input
                  type="radio"
                  name="performer"
                  value="vendor"
                  checked={formPerformerKind === 'vendor'}
                  onChange={() => setFormPerformerKind('vendor')}
                />{' '}
                Outsourced (vendor)
              </label>
              <label>
                <input
                  type="radio"
                  name="performer"
                  value="internal"
                  checked={formPerformerKind === 'internal'}
                  onChange={() => setFormPerformerKind('internal')}
                />{' '}
                Internal staff
              </label>
              {formPerformerKind === 'vendor' ? (
                <div style={{ marginTop: '0.5rem' }}>
                  <select
                    value={formVendorId}
                    onChange={(e) => setFormVendorId(e.target.value)}
                    style={{ width: '100%', padding: '0.4rem' }}
                  >
                    <option value="">— Select a vendor —</option>
                    {vendors.map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.name}
                      </option>
                    ))}
                  </select>
                </div>
              ) : (
                <div style={{ marginTop: '0.5rem' }}>
                  <input
                    type="text"
                    placeholder="Username of staff member"
                    value={formInternalUsername}
                    onChange={(e) => setFormInternalUsername(e.target.value)}
                    style={{ width: '100%', padding: '0.4rem' }}
                  />
                </div>
              )}
            </fieldset>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
              <label>
                Cost
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  value={formCost}
                  onChange={(e) => setFormCost(e.target.value)}
                  style={{ width: '100%', padding: '0.4rem' }}
                />
              </label>
              <label>
                Invoice number
                <input
                  type="text"
                  value={formInvoiceNumber}
                  onChange={(e) => setFormInvoiceNumber(e.target.value)}
                  style={{ width: '100%', padding: '0.4rem' }}
                />
              </label>
            </div>
            <label>
              Notes
              <textarea
                rows={2}
                value={formNotes}
                onChange={(e) => setFormNotes(e.target.value)}
                style={{ width: '100%', padding: '0.4rem' }}
              />
            </label>
            <label>
              Attachment (invoice/receipt)
              <input
                type="file"
                onChange={(e) => setFormAttachment(e.target.files?.[0] ?? null)}
              />
            </label>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button
                type="submit"
                className="action-button"
                disabled={submitting}
              >
                {submitting ? 'Saving…' : 'Save record'}
              </button>
              <button
                type="button"
                onClick={() => {
                  resetForm();
                  setShowForm(false);
                }}
                disabled={submitting}
                style={{
                  padding: '0.5rem 1rem',
                  background: '#ccc',
                  border: 'none',
                  borderRadius: 4,
                  cursor: 'pointer',
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        </form>
      )}

      {loading ? (
        <p>Loading maintenance history…</p>
      ) : error ? (
        <p style={{ color: '#c92a2a' }}>{error}</p>
      ) : !data || data.results.length === 0 ? (
        <p>No maintenance recorded for this asset in the selected range.</p>
      ) : (
        <div className="parts-table">
          <table>
            <thead>
              <tr>
                <th>Completed</th>
                <th>Title</th>
                <th>Performed By</th>
                <th>Cost</th>
                <th>Source</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.results.map((row) => (
                <React.Fragment key={`${row.source}-${row.id}`}>
                  <tr data-testid={`history-row-${row.id}`}>
                    <td>{row.completed_on}</td>
                    <td>{row.title}</td>
                    <td>{performerLabel(row)}</td>
                    <td>{row.cost != null ? `$${row.cost}` : '—'}</td>
                    <td>{sourceLabel(row.source)}</td>
                    <td>
                      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                        {row.detail_url && (
                          <a href={row.detail_url}>Open WO</a>
                        )}
                        {row.attachment_url && (
                          <a
                            href={row.attachment_url}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            Attachment
                          </a>
                        )}
                        {canManage && row.source === 'historical' && (
                          <button
                            type="button"
                            onClick={() => openEdit(row)}
                            data-testid={`history-row-edit-${row.id}`}
                            style={{
                              background: 'transparent',
                              border: '1px solid #ccc',
                              borderRadius: 4,
                              padding: '2px 8px',
                              cursor: 'pointer',
                              fontSize: '0.85rem',
                            }}
                          >
                            Edit notes / attachment
                          </button>
                        )}
                        {!row.detail_url &&
                          !row.attachment_url &&
                          !(canManage && row.source === 'historical') &&
                          '—'}
                      </div>
                    </td>
                  </tr>
                  {row.notes && row.notes.trim() && (
                    <tr
                      data-testid={`history-row-notes-${row.id}`}
                      className="history-notes-row"
                    >
                      <td></td>
                      <td colSpan={5}>
                        <div
                          style={{
                            color: '#555',
                            fontSize: '0.9em',
                            whiteSpace: 'pre-wrap',
                            padding: '0.25rem 0 0.5rem 0',
                          }}
                        >
                          <span style={{ fontWeight: 600, marginRight: '0.5em' }}>
                            Notes:
                          </span>
                          {row.notes}
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editingRowId && (
        <div
          style={{
            marginTop: '1rem',
            padding: '1rem',
            border: '1px solid #ccc',
            borderRadius: 4,
            background: '#fafafa',
          }}
          data-testid="history-edit-panel"
        >
          <h3 style={{ marginTop: 0 }}>Edit maintenance record</h3>
          <form onSubmit={handleEditSubmit}>
            <div style={{ marginBottom: '0.75rem' }}>
              <label style={{ display: 'block', marginBottom: '0.25rem' }}>
                Notes
              </label>
              <textarea
                value={editNotes}
                onChange={(e) => setEditNotes(e.target.value)}
                rows={4}
                style={{ width: '100%', resize: 'vertical' }}
                data-testid="history-edit-notes"
              />
            </div>
            <div style={{ marginBottom: '0.75rem' }}>
              <label style={{ display: 'block', marginBottom: '0.25rem' }}>
                Attachment (PDF, photo, etc.) — uploading replaces any existing file
              </label>
              <input
                type="file"
                accept=".pdf,application/pdf,image/*"
                onChange={(e) => setEditAttachment(e.target.files?.[0] ?? null)}
                data-testid="history-edit-attachment"
              />
            </div>
            {editError && (
              <div style={{ color: '#c00', marginBottom: '0.5rem' }} role="alert">
                {editError}
              </div>
            )}
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button
                type="submit"
                disabled={editSubmitting}
                data-testid="history-edit-submit"
              >
                {editSubmitting ? 'Saving…' : 'Save'}
              </button>
              <button
                type="button"
                onClick={closeEdit}
                disabled={editSubmitting}
                data-testid="history-edit-cancel"
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}
    </section>
  );
};

export default MaintenanceHistorySection;
