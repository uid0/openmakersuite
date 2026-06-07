/**
 * Maker Box Admin Page
 *
 * Logistics admin view: lists all known bins with their current status, and
 * exposes a form for manually creating a single label (when the volunteer
 * has the name and username but doesn't want to bind it to a specific bin
 * row yet).
 */
import React, { useCallback, useEffect, useState } from 'react';
import { makerBoxesAPI, MakerBox } from '../services/api';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const STATUS_BADGE: Record<MakerBox['status'], { label: string; color: string }> = {
  valid: { label: 'Valid', color: '#1f8a3a' },
  grace: { label: 'Grace', color: '#d4a017' },
  expired: { label: 'Expired', color: '#c0392b' },
  unassigned: { label: 'Unassigned', color: '#777' },
  unknown: { label: 'Unknown', color: '#c0392b' },
};

// Avery 5371 holds 10 cards per sheet — surface the cap so the warden
// knows how many bins will land on a single page.
const SHEET_CAPACITY = 10;

const MakerBoxAdminPage: React.FC = () => {
  const [boxes, setBoxes] = useState<MakerBox[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [manual, setManual] = useState({ username: '', first_name: '', last_name: '' });
  const [generating, setGenerating] = useState(false);
  const [sheetPrinting, setSheetPrinting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await makerBoxesAPI.list();
      const rows = Array.isArray(response.data) ? response.data : response.data.results;
      setBoxes(rows);
      setError(null);
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to load maker boxes.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleManualSubmit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      if (!manual.username.trim()) return;
      setGenerating(true);
      try {
        const response = await makerBoxesAPI.manualLabel({
          username: manual.username.trim(),
          first_name: manual.first_name.trim(),
          last_name: manual.last_name.trim(),
        });
        const blob = new Blob([response.data], { type: 'image/png' });
        const url = URL.createObjectURL(blob);
        window.open(url, '_blank');
      } catch (err: any) {
        setError(extractErrorMessage(err, 'Manual label generation failed.'));
      } finally {
        setGenerating(false);
      }
    },
    [manual.username, manual.first_name, manual.last_name],
  );

  const handlePrintSheet = useCallback(async () => {
    if (boxes.length === 0) return;
    setSheetPrinting(true);
    setError(null);
    try {
      const ids = boxes.slice(0, SHEET_CAPACITY).map((b) => b.bin_id);
      const res = await makerBoxesAPI.printSheet(ids);
      const blob = new Blob([res.data], { type: 'image/png' });
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank');
      // 60 s is plenty for the new tab to fetch the bytes.
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to render Avery sheet.'));
    } finally {
      setSheetPrinting(false);
    }
  }, [boxes]);

  return (
    <div style={{ padding: '1.5rem' }}>
      <h1>Maker Box Admin</h1>

      <section style={{ marginBottom: '2rem' }}>
        <h2>Manually create a label</h2>
        <form
          onSubmit={handleManualSubmit}
          style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}
        >
          <input
            type="text"
            placeholder="Username"
            value={manual.username}
            onChange={(e) => setManual((s) => ({ ...s, username: e.target.value }))}
            required
          />
          <input
            type="text"
            placeholder="First name"
            value={manual.first_name}
            onChange={(e) => setManual((s) => ({ ...s, first_name: e.target.value }))}
          />
          <input
            type="text"
            placeholder="Last name"
            value={manual.last_name}
            onChange={(e) => setManual((s) => ({ ...s, last_name: e.target.value }))}
          />
          <button type="submit" disabled={generating}>
            {generating ? 'Generating…' : 'Generate label'}
          </button>
        </form>
      </section>

      <section>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            gap: '0.5rem',
            marginBottom: '0.5rem',
          }}
        >
          <h2 style={{ margin: 0 }}>All bins</h2>
          <button
            type="button"
            onClick={handlePrintSheet}
            disabled={sheetPrinting || boxes.length === 0}
            data-testid="print-avery-sheet"
          >
            {sheetPrinting
              ? 'Rendering…'
              : `Print Avery sheet (${Math.min(boxes.length, SHEET_CAPACITY)}/${SHEET_CAPACITY})`}
          </button>
        </div>
        {loading && <p>Loading…</p>}
        {error && <p style={{ color: '#c0392b' }}>{error}</p>}
        {!loading && !error && (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th style={{ textAlign: 'left', padding: '0.25rem' }}>Bin</th>
                <th style={{ textAlign: 'left', padding: '0.25rem' }}>Member</th>
                <th style={{ textAlign: 'left', padding: '0.25rem' }}>Username</th>
                <th style={{ textAlign: 'left', padding: '0.25rem' }}>Status</th>
                <th style={{ textAlign: 'left', padding: '0.25rem' }}>Expires</th>
                <th style={{ textAlign: 'left', padding: '0.25rem' }}>Label</th>
              </tr>
            </thead>
            <tbody>
              {boxes.map((box) => {
                const badge = STATUS_BADGE[box.status];
                return (
                  <tr key={box.id} style={{ borderTop: '1px solid #ddd' }}>
                    <td style={{ padding: '0.25rem' }}>{box.bin_id}</td>
                    <td style={{ padding: '0.25rem' }}>{box.display_name}</td>
                    <td style={{ padding: '0.25rem' }}>{box.assigned_username}</td>
                    <td style={{ padding: '0.25rem' }}>
                      <span
                        style={{
                          background: badge.color,
                          color: 'white',
                          padding: '0.1rem 0.4rem',
                          borderRadius: '4px',
                          fontSize: '0.85rem',
                        }}
                      >
                        {badge.label}
                      </span>
                    </td>
                    <td style={{ padding: '0.25rem' }}>
                      {box.expires_at ? new Date(box.expires_at).toLocaleDateString() : '—'}
                    </td>
                    <td style={{ padding: '0.25rem' }}>
                      <a href={makerBoxesAPI.labelUrl(box.id)} target="_blank" rel="noreferrer">
                        Print
                      </a>
                    </td>
                  </tr>
                );
              })}
              {boxes.length === 0 && (
                <tr>
                  <td colSpan={6} style={{ padding: '0.5rem', color: '#777' }}>
                    No maker boxes yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
};

export default MakerBoxAdminPage;
