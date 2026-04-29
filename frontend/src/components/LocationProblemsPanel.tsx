/**
 * LocationProblemsPanel - Lists problem reports for a location and links
 * to detail pages for staff promotion / resolution.
 */
import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { locationProblemsAPI } from '../services/api';
import { LocationProblem, LocationProblemSeverity } from '../types';

export interface LocationProblemsPanelProps {
  locationId: string | number;
  refreshKey?: number;
}

const SEVERITY_COLORS: Record<LocationProblemSeverity, string> = {
  low: '#6c757d',
  medium: '#17a2b8',
  high: '#fd7e14',
  urgent: '#dc3545',
};

const LocationProblemsPanel: React.FC<LocationProblemsPanelProps> = ({
  locationId,
  refreshKey,
}) => {
  const [problems, setProblems] = useState<LocationProblem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        setLoading(true);
        setError(null);
        const resp = await locationProblemsAPI.listForLocation(locationId);
        if (cancelled) return;
        setProblems(resp.data);
      } catch (err: any) {
        if (cancelled) return;
        setError(err.response?.data?.detail || 'Failed to load problem reports');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [locationId, refreshKey]);

  if (loading) return <p>Loading problem reports...</p>;
  if (error) return <p className="error">{error}</p>;
  if (problems.length === 0) {
    return <p className="muted">No problems reported here yet.</p>;
  }

  return (
    <ul className="location-problems-list">
      {problems.map((p) => (
        <li key={p.id} className="location-problem-row">
          <div className="lp-row-main">
            <span
              className="lp-severity-badge"
              style={{ background: SEVERITY_COLORS[p.severity], color: 'white' }}
            >
              {p.severity_display}
            </span>
            <Link to={`/maintenance/location-problems/${p.id}`}>
              {p.description.length > 100
                ? `${p.description.slice(0, 100)}…`
                : p.description}
            </Link>
          </div>
          <div className="lp-row-meta">
            <span className={`status-badge ${p.status}`}>{p.status_display}</span>
            <small>
              Reported {new Date(p.reported_at).toLocaleDateString()}
              {p.reported_by ? ` by ${p.reported_by}` : ''}
            </small>
          </div>
        </li>
      ))}
    </ul>
  );
};

export default LocationProblemsPanel;
