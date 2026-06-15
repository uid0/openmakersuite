/**
 * Maker Box scan verification (public)
 *
 * Route: ``/scan/makerbox/:binId/:username``
 *
 * This is what the printed label QR resolves to. A phone scans the
 * bin's QR, lands here, and the page calls the public
 * ``GET /api/maker-boxes/verify/<bin>/<user>/`` endpoint to display
 * the membership verdict — no auth required, so kiosk-mode phones
 * work.
 */
import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { makerBoxesAPI, MakerBoxScanResult } from '../services/api';
import { extractErrorMessage } from '../utils/extractErrorMessage';

type VerifyResult = MakerBoxScanResult & { detail?: string };

const STATUS_STYLE: Record<
  string,
  { background: string; label: string; detail: (r: VerifyResult) => string }
> = {
  valid: {
    background: '#1f8a3a',
    label: 'VALID',
    detail: (r) =>
      r.days_remaining
        ? `${r.days_remaining} days remaining`
        : 'Active membership',
  },
  grace: {
    background: '#d4a017',
    label: 'GRACE PERIOD',
    detail: (r) => `${r.days_remaining ?? 0} days of grace remaining`,
  },
  expired: {
    background: '#c0392b',
    label: 'EXPIRED',
    detail: () => 'Membership has lapsed beyond the 7-day grace window.',
  },
  unknown: {
    background: '#7a7a7a',
    label: 'UNKNOWN',
    detail: (r) => r.detail || 'No record for this bin or user.',
  },
};

const MakerBoxVerifyScanPage: React.FC = () => {
  const { binId = '', username = '' } = useParams<{
    binId: string;
    username: string;
  }>();
  const [result, setResult] = useState<VerifyResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    makerBoxesAPI
      .verifyScan(binId, username)
      .then((response) => {
        if (!cancelled) {
          setResult(response.data);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(extractErrorMessage(err, 'Verification failed.'));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [binId, username]);

  if (loading) {
    return (
      <div style={{ padding: '2rem', textAlign: 'center' }}>
        <p>Looking up bin {binId}…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: '2rem', textAlign: 'center' }}>
        <h1>Verification error</h1>
        <p>{error}</p>
      </div>
    );
  }

  const style = result ? STATUS_STYLE[result.status] || STATUS_STYLE.unknown : null;

  return (
    <div style={{ padding: '1.5rem', maxWidth: '480px', margin: '0 auto' }}>
      <h1 style={{ margin: 0 }}>Maker Box</h1>
      <div style={{ color: '#666', marginBottom: '1rem' }}>
        {binId} · {username}
      </div>
      {result && style && (
        <div
          style={{
            background: style.background,
            color: 'white',
            padding: '1.5rem',
            borderRadius: '0.5rem',
            textAlign: 'center',
          }}
        >
          <div style={{ fontSize: '1.5rem', fontWeight: 700 }}>
            {style.label}
          </div>
          <div style={{ marginTop: '0.5rem' }}>{style.detail(result)}</div>
        </div>
      )}
      {result && result.first_name && (
        <div style={{ marginTop: '1rem' }}>
          <strong>
            {result.first_name} {result.last_name}
          </strong>
          {result.email && (
            <div style={{ color: '#666' }}>{result.email}</div>
          )}
        </div>
      )}
    </div>
  );
};

export default MakerBoxVerifyScanPage;
