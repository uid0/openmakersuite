/**
 * Modal for reporting a problem at a Location (oms-sd1).
 *
 * Anonymous reporters can submit; authenticated users have their
 * username recorded server-side. Mirrors the AssetProblem flow.
 */
import React, { useState } from 'react';
import { locationProblemsAPI } from '../services/api';
import { LocationProblemSeverity } from '../types';
import { showError } from '../utils/dialogs';

export interface ReportLocationProblemModalProps {
  locationId: string | number;
  locationName: string;
  onClose: () => void;
  onSubmitted: () => void;
}

const SEVERITY_OPTIONS: { value: LocationProblemSeverity; label: string }[] = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
  { value: 'urgent', label: 'Urgent' },
];

const ReportLocationProblemModal: React.FC<ReportLocationProblemModalProps> = ({
  locationId,
  locationName,
  onClose,
  onSubmitted,
}) => {
  const [description, setDescription] = useState('');
  const [severity, setSeverity] = useState<LocationProblemSeverity>('medium');
  const [photo, setPhoto] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!description.trim()) {
      showError('Please describe the problem.');
      return;
    }
    try {
      setSubmitting(true);
      await locationProblemsAPI.reportForLocation(locationId, {
        description: description.trim(),
        severity,
        photo: photo ?? undefined,
      });
      onSubmitted();
    } catch (err: any) {
      showError(err.response?.data?.error || 'Failed to report problem.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true">
      <div className="modal-content" style={{ maxWidth: 520 }}>
        <h2>Report a problem at {locationName}</h2>
        <form onSubmit={handleSubmit}>
          <label htmlFor="lp-description">
            What is the problem? <span aria-hidden="true">*</span>
          </label>
          <textarea
            id="lp-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={4}
            required
            placeholder="Describe the problem (e.g. leak, broken light, broken door)..."
          />

          <label htmlFor="lp-severity">Severity</label>
          <select
            id="lp-severity"
            value={severity}
            onChange={(e) => setSeverity(e.target.value as LocationProblemSeverity)}
          >
            {SEVERITY_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>

          <label htmlFor="lp-photo">Photo (optional)</label>
          <input
            id="lp-photo"
            type="file"
            accept="image/*"
            onChange={(e) => setPhoto(e.target.files?.[0] ?? null)}
          />

          <div className="modal-actions">
            <button
              type="button"
              onClick={onClose}
              className="btn-secondary"
              disabled={submitting}
            >
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={submitting}>
              {submitting ? 'Submitting...' : 'Submit Report'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default ReportLocationProblemModal;
