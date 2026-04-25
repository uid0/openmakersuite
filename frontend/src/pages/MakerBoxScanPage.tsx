/**
 * Maker Box Scan Page
 *
 * Logistics-side workflow: a staff member walks the floor with a barcode
 * scanner, scans the bin id and the username from a QR code, and gets a
 * green / yellow / red status reflecting WHMCS membership state. On red
 * (expired or unknown) we expose a button to email the former member with
 * a pickup notice.
 */
import React, { useCallback, useState } from 'react';
import { makerBoxesAPI, MakerBoxScanResult } from '../services/api';

type FormState = {
  binId: string;
  username: string;
};

const MakerBoxScanPage: React.FC = () => {
  const [form, setForm] = useState<FormState>({ binId: '', username: '' });
  const [result, setResult] = useState<MakerBoxScanResult | null>(null);
  const [boxId, setBoxId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [emailSentTo, setEmailSentTo] = useState<string | null>(null);
  const [emailSending, setEmailSending] = useState(false);

  const handleSubmit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      setSubmitting(true);
      setError(null);
      setEmailSentTo(null);
      try {
        const response = await makerBoxesAPI.scan(form.binId.trim(), form.username.trim());
        setResult(response.data);
        // Look up the bin row so we can hit /email-pickup/ if needed.
        const list = await makerBoxesAPI.list();
        const rows = Array.isArray(list.data) ? list.data : list.data.results;
        const match = rows.find((b) => b.bin_id === response.data.bin_id);
        setBoxId(match ? match.id : null);
      } catch (err: any) {
        const detail = err?.response?.data?.detail;
        setError(detail || 'Scan failed.');
        setResult(null);
        setBoxId(null);
      } finally {
        setSubmitting(false);
      }
    },
    [form.binId, form.username],
  );

  const handleEmailPickup = useCallback(async () => {
    if (!boxId) return;
    setEmailSending(true);
    setError(null);
    try {
      const response = await makerBoxesAPI.emailPickup(boxId);
      setEmailSentTo(response.data.to);
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Email failed to send.');
    } finally {
      setEmailSending(false);
    }
  }, [boxId]);

  const reset = useCallback(() => {
    setForm({ binId: '', username: '' });
    setResult(null);
    setBoxId(null);
    setError(null);
    setEmailSentTo(null);
  }, []);

  let statusColor = '#f5f5f5';
  let statusLabel = '';
  let statusDetail = '';
  if (result) {
    if (result.status === 'valid') {
      statusColor = '#1f8a3a';
      statusLabel = 'VALID MEMBER';
      statusDetail = result.days_remaining
        ? `${result.days_remaining} days remaining`
        : 'Active membership';
    } else if (result.status === 'grace') {
      statusColor = '#d4a017';
      statusLabel = 'GRACE PERIOD';
      statusDetail = `${result.days_remaining ?? 0} days of grace remaining`;
    } else if (result.status === 'expired') {
      statusColor = '#c0392b';
      statusLabel = 'EXPIRED';
      statusDetail = 'Membership has lapsed beyond the 7-day grace window.';
    } else {
      statusColor = '#c0392b';
      statusLabel = 'UNKNOWN USER';
      statusDetail = 'WHMCS has no record of this username.';
    }
  }

  return (
    <div style={{ padding: '1.5rem', maxWidth: '720px', margin: '0 auto' }}>
      <h1>Maker Box Scan</h1>
      <p>Scan or type a bin id and the member's WHMCS username.</p>

      <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
        <label>
          Bin ID
          <input
            type="text"
            value={form.binId}
            onChange={(e) => setForm((s) => ({ ...s, binId: e.target.value }))}
            placeholder="PSB-007"
            required
            autoFocus
            style={{ width: '100%', padding: '0.5rem', fontSize: '1rem' }}
          />
        </label>
        <label>
          Username
          <input
            type="text"
            value={form.username}
            onChange={(e) => setForm((s) => ({ ...s, username: e.target.value }))}
            placeholder="member username"
            required
            style={{ width: '100%', padding: '0.5rem', fontSize: '1rem' }}
          />
        </label>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button type="submit" disabled={submitting} style={{ padding: '0.5rem 1rem' }}>
            {submitting ? 'Checking…' : 'Scan'}
          </button>
          <button type="button" onClick={reset} style={{ padding: '0.5rem 1rem' }}>
            Reset
          </button>
        </div>
      </form>

      {error && (
        <div role="alert" style={{ marginTop: '1rem', color: '#c0392b' }}>
          {error}
        </div>
      )}

      {result && (
        <div
          data-testid="scan-result"
          data-status={result.status}
          style={{
            marginTop: '1.5rem',
            padding: '1.5rem',
            borderRadius: '8px',
            background: statusColor,
            color: 'white',
            textAlign: 'center',
          }}
        >
          <h2 style={{ margin: 0 }}>{statusLabel}</h2>
          <p style={{ fontSize: '1.25rem', margin: '0.5rem 0' }}>
            {result.first_name || result.last_name
              ? `${result.first_name} ${result.last_name}`.trim()
              : result.username}
          </p>
          <p style={{ margin: 0 }}>{statusDetail}</p>
          {(result.status === 'expired' || result.status === 'unknown') && boxId !== null && (
            <button
              type="button"
              onClick={handleEmailPickup}
              disabled={emailSending || !!emailSentTo}
              style={{
                marginTop: '1rem',
                padding: '0.5rem 1rem',
                background: 'white',
                color: '#c0392b',
                border: 'none',
                borderRadius: '4px',
                cursor: 'pointer',
              }}
            >
              {emailSentTo
                ? `Email sent to ${emailSentTo}`
                : emailSending
                  ? 'Sending…'
                  : 'Send pickup email'}
            </button>
          )}
        </div>
      )}
    </div>
  );
};

export default MakerBoxScanPage;
