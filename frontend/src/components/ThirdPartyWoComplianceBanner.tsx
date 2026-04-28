/**
 * Inline warning banner for the third-party WO creation form.
 *
 * On asset (and optional vendor) selection, fetches the backend's
 * /maintenance-orders/assets/<id>/wo-status/ endpoint and renders:
 *  - a warranty-active banner (Logistics should pursue manufacturer first)
 *  - a vendor compliance banner (TDLR or COI expiring/expired)
 */
import React, { useEffect, useState } from 'react';
import {
  AssetWoStatusDto,
  ComplianceBucket,
  thirdPartyMaintenanceAPI,
} from '../services/api';

interface Props {
  assetId: string | null;
  vendorId?: string | null;
}

const bucketLabel: Record<ComplianceBucket, string> = {
  ok: 'On file',
  expiring_soon: 'Expiring within 30 days',
  expired: 'Expired',
  missing: 'No date on file',
};

const bucketTone: Record<ComplianceBucket, 'warning' | 'danger' | 'info' | 'ok'> = {
  ok: 'ok',
  expiring_soon: 'warning',
  expired: 'danger',
  missing: 'info',
};

const ThirdPartyWoComplianceBanner: React.FC<Props> = ({ assetId, vendorId }) => {
  const [status, setStatus] = useState<AssetWoStatusDto | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!assetId) {
      setStatus(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    thirdPartyMaintenanceAPI
      .getAssetWoStatus(assetId, vendorId || undefined)
      .then((resp) => {
        if (!cancelled) setStatus(resp.data);
      })
      .catch((err) => {
        if (!cancelled)
          setError(err?.response?.data?.detail || 'Failed to load asset status');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [assetId, vendorId]);

  if (!assetId) return null;
  if (loading) return <div className="tpwo-banner tpwo-banner--info">Checking warranty &amp; compliance…</div>;
  if (error) return <div className="tpwo-banner tpwo-banner--danger">{error}</div>;
  if (!status) return null;

  const banners: React.ReactNode[] = [];

  if (status.warranty && status.warranty.is_active) {
    banners.push(
      <div key="warranty" className="tpwo-banner tpwo-banner--warning" role="alert">
        <strong>Warranty active</strong> — pursue manufacturer first.
        <div>
          {status.warranty.provider}
          {status.warranty.policy_number && ` · ${status.warranty.policy_number}`}
          {status.warranty.end_date && ` · ends ${status.warranty.end_date}`}
        </div>
      </div>
    );
  }

  const comp = status.vendor_compliance;
  if (comp) {
    if (comp.tdlr_status !== 'ok') {
      banners.push(
        <div
          key="tdlr"
          className={`tpwo-banner tpwo-banner--${bucketTone[comp.tdlr_status]}`}
          role="alert"
        >
          <strong>TDLR license:</strong> {bucketLabel[comp.tdlr_status]}
          {comp.tdlr_expires_at && ` (${comp.tdlr_expires_at})`}
        </div>
      );
    }
    if (comp.coi_status !== 'ok') {
      banners.push(
        <div
          key="coi"
          className={`tpwo-banner tpwo-banner--${bucketTone[comp.coi_status]}`}
          role="alert"
        >
          <strong>COI:</strong> {bucketLabel[comp.coi_status]}
          {comp.coi_expires_at && ` (${comp.coi_expires_at})`}
        </div>
      );
    }
  }

  if (banners.length === 0) return null;
  return <div className="tpwo-banner-stack">{banners}</div>;
};

export default ThirdPartyWoComplianceBanner;
