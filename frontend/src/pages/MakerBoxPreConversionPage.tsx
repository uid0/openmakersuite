/**
 * Maker Box Pre-Conversion Page
 *
 * Phase 1 of the three-phase maker-box conversion workflow:
 *
 *   1. Pre-conversion (this page) — staff scans a badge or types a
 *      WHMCS username; the cascade resolves identity from Common API
 *      and/or WHMCS; the row is queued for bin allocation later.
 *   2. Conversion (PR 3) — staff allocates ``MBX-NNN`` and prints the
 *      label.
 *   3. Post-conversion — the existing scan screen verifies status.
 *
 * The "Add to queue" button hits ``/pre-convert/``, which is
 * idempotent on resolved username: re-scanning the same member just
 * refreshes their identity row rather than producing duplicates.
 */
import React, { useCallback, useEffect, useState } from 'react';
import ServiceUnavailableNotice from '../components/ServiceUnavailableNotice';
import { useServiceStatus } from '../hooks/useServiceStatus';
import {
  makerBoxesAPI,
  MakerBox,
  MakerBoxLookupResult,
} from '../services/api';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const IDENTITY_SOURCE_LABEL: Record<string, string> = {
  whmcs: 'WHMCS',
  common_api: 'Common API (badge / AD)',
  manual: 'Manual admin entry',
  '': 'Unknown',
};

const MakerBoxPreConversionPage: React.FC = () => {
  // The identity cascade layers WHMCS over Common API, and it consults WHMCS on
  // *both* paths — so a degraded WHMCS breaks every lookup and the buttons go
  // with it. A degraded Common API only breaks badge scans; typing a username
  // still resolves, so that one warns without taking the control away.
  const { isDegraded } = useServiceStatus();
  const billingDown = isDegraded('whmcs');
  const [query, setQuery] = useState('');
  const [notes, setNotes] = useState('');
  const [preview, setPreview] = useState<MakerBoxLookupResult | null>(null);
  const [previewQuery, setPreviewQuery] = useState('');
  const [previewing, setPreviewing] = useState(false);
  const [adding, setAdding] = useState(false);
  const [queue, setQueue] = useState<MakerBox[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<string | null>(null);
  const [convertingId, setConvertingId] = useState<number | null>(null);
  const [convertedBinId, setConvertedBinId] = useState<{
    id: number;
    binId: string;
  } | null>(null);

  const refreshQueue = useCallback(async () => {
    try {
      const response = await makerBoxesAPI.preConversionQueue();
      setQueue(response.data);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to load queue.'));
    }
  }, []);

  useEffect(() => {
    refreshQueue();
  }, [refreshQueue]);

  const runLookup = useCallback(async () => {
    const cleaned = query.trim();
    if (!cleaned) return;
    setPreviewing(true);
    setError(null);
    setConfirmation(null);
    try {
      const response = await makerBoxesAPI.lookup(cleaned);
      setPreview(response.data);
      setPreviewQuery(cleaned);
    } catch (err) {
      setPreview(null);
      setError(extractErrorMessage(err, 'Lookup failed.'));
    } finally {
      setPreviewing(false);
    }
  }, [query]);

  const handleLookupSubmit = useCallback(
    (event: React.FormEvent) => {
      event.preventDefault();
      runLookup();
    },
    [runLookup],
  );

  const handleConvert = useCallback(
    async (row: MakerBox) => {
      setConvertingId(row.id);
      setError(null);
      setConfirmation(null);
      setConvertedBinId(null);
      try {
        const response = await makerBoxesAPI.convert({ id: row.id });
        const converted = response.data;
        if (converted.bin_id) {
          setConvertedBinId({ id: converted.id, binId: converted.bin_id });
          setConfirmation(
            `Allocated ${converted.bin_id} to ${converted.assigned_username}.`,
          );
        }
        await refreshQueue();
      } catch (err) {
        setError(extractErrorMessage(err, 'Convert failed.'));
      } finally {
        setConvertingId(null);
      }
    },
    [refreshQueue],
  );

  const handleAddToQueue = useCallback(async () => {
    const cleaned = (previewQuery || query).trim();
    if (!cleaned) return;
    setAdding(true);
    setError(null);
    setConfirmation(null);
    try {
      const response = await makerBoxesAPI.preConvert(cleaned, notes.trim() || undefined);
      const row = response.data;
      setConfirmation(
        `${row.display_name || row.assigned_username} queued for bin allocation.`,
      );
      setQuery('');
      setNotes('');
      setPreview(null);
      setPreviewQuery('');
      await refreshQueue();
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to add to queue.'));
    } finally {
      setAdding(false);
    }
  }, [previewQuery, query, notes, refreshQueue]);

  const previewIsFresh = preview && previewQuery === query.trim();

  return (
    <div style={{ padding: '1.5rem', maxWidth: '880px', margin: '0 auto' }}>
      <h1>Maker Box — Pre-Conversion Queue</h1>
      <p>
        Scan a badge or type a WHMCS username. We'll look up the member
        and queue them for bin allocation. Bin IDs (<code>MBX-NNN</code>)
        are assigned in the next phase.
      </p>

      <form
        onSubmit={handleLookupSubmit}
        style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginBottom: '1rem' }}
      >
        <label>
          Badge or username
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="12345678 or ada.lovelace"
            required
            autoFocus
            style={{ width: '100%', padding: '0.5rem', fontSize: '1rem' }}
          />
        </label>
        <label>
          Notes (optional)
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="e.g. wants the small bin"
            rows={2}
            style={{ width: '100%', padding: '0.5rem', fontSize: '0.95rem' }}
          />
        </label>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button
            type="submit"
            disabled={previewing || !query.trim() || billingDown}
            style={{ padding: '0.5rem 1rem' }}
          >
            {previewing ? 'Looking up…' : 'Look up'}
          </button>
          <button
            type="button"
            onClick={handleAddToQueue}
            disabled={adding || !previewIsFresh || !preview?.found || billingDown}
            style={{ padding: '0.5rem 1rem' }}
          >
            {adding ? 'Adding…' : 'Add to queue'}
          </button>
        </div>
        <ServiceUnavailableNotice
          service="whmcs"
          message="Membership lookups are unavailable right now — identity can't be resolved. Try again shortly."
          testId="preconvert-whmcs-notice"
        />
        <ServiceUnavailableNotice
          service="common_api"
          message="Badge lookups are unavailable right now — type the member's username instead."
          testId="preconvert-common-api-notice"
        />
      </form>

      {error && (
        <div
          role="alert"
          style={{
            background: '#fdecea',
            border: '1px solid #c0392b',
            color: '#7a1f15',
            padding: '0.75rem',
            marginBottom: '1rem',
          }}
        >
          {error}
        </div>
      )}

      {confirmation && (
        <div
          role="status"
          style={{
            background: '#eaf6ec',
            border: '1px solid #1f8a3a',
            color: '#0f5320',
            padding: '0.75rem',
            marginBottom: '1rem',
          }}
        >
          {confirmation}
          {convertedBinId && (
            <>
              {' '}
              <a
                href={makerBoxesAPI.labelUrl(convertedBinId.id)}
                target="_blank"
                rel="noopener noreferrer"
                style={{ marginLeft: '0.5rem' }}
              >
                Download label ({convertedBinId.binId}.png)
              </a>
            </>
          )}
        </div>
      )}

      {previewIsFresh && (
        <div
          data-testid="lookup-preview"
          style={{
            border: '1px solid #ccc',
            padding: '0.75rem',
            marginBottom: '1.5rem',
            background: preview.found ? '#f5fbf6' : '#fef5f4',
          }}
        >
          {preview.found ? (
            <>
              <div style={{ fontSize: '1.1rem', fontWeight: 600 }}>
                {preview.first_name} {preview.last_name} ({preview.username})
              </div>
              <div>{preview.email || <em>no email on file</em>}</div>
              <div style={{ marginTop: '0.25rem' }}>
                Identity source: {IDENTITY_SOURCE_LABEL[preview.identity_source] || preview.identity_source}
              </div>
              <div>
                Membership: {preview.membership_status || 'unknown'}
                {preview.days_remaining !== null && ` (${preview.days_remaining} days remaining)`}
              </div>
            </>
          ) : (
            <div>
              No match. If this is an add-on user, have them scan their badge so
              the Common API can resolve their identity.
            </div>
          )}
        </div>
      )}

      <h2>Queue ({queue.length})</h2>
      {queue.length === 0 ? (
        <p>
          <em>No members are waiting for bin allocation.</em>
        </p>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ background: '#f0f0f0', textAlign: 'left' }}>
              <th style={{ padding: '0.5rem' }}>Username</th>
              <th style={{ padding: '0.5rem' }}>Name</th>
              <th style={{ padding: '0.5rem' }}>Source</th>
              <th style={{ padding: '0.5rem' }}>Notes</th>
              <th style={{ padding: '0.5rem' }}>Queued</th>
              <th style={{ padding: '0.5rem' }}>Convert</th>
            </tr>
          </thead>
          <tbody>
            {queue.map((row) => (
              <tr key={row.id} style={{ borderTop: '1px solid #eee' }}>
                <td style={{ padding: '0.5rem' }}>{row.assigned_username}</td>
                <td style={{ padding: '0.5rem' }}>{row.display_name}</td>
                <td style={{ padding: '0.5rem' }}>
                  {IDENTITY_SOURCE_LABEL[row.identity_source] || row.identity_source || '—'}
                </td>
                <td style={{ padding: '0.5rem', whiteSpace: 'pre-wrap' }}>{row.notes}</td>
                <td style={{ padding: '0.5rem' }}>
                  {row.created_at ? new Date(row.created_at).toLocaleString() : ''}
                </td>
                <td style={{ padding: '0.5rem' }}>
                  <button
                    type="button"
                    onClick={() => handleConvert(row)}
                    disabled={convertingId === row.id}
                  >
                    {convertingId === row.id ? 'Allocating…' : 'Convert'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
};

export default MakerBoxPreConversionPage;
