/**
 * Asset Meters section for the asset detail page (EAM bead-1).
 *
 * Shows the asset's usage meters — runtime hours rolled up from ForgeKey usage
 * sessions, plus manual counters like a water fountain's gallons — with the
 * current value, whether it is measured or an estimate, and its source. Staff /
 * SIG-admins (canManage) get the manual-first controls: record a reading
 * (absolute "set to" or delta "add"), post a correction ("adjust"), and add a
 * new meter so the flow is usable end-to-end from the web.
 *
 * Meters are read from the asset-detail payload (`asset.meters`) so every viewer
 * sees them; the dedicated list endpoint is staff-only. After a write we call
 * `onChanged` to reload the asset so the displayed value stays authoritative.
 */
import React, { useState } from 'react';

import { assetMetersAPI } from '../../services/api';
import { AssetMeter, AssetMeterType } from '../../types';

interface AssetMetersSectionProps {
  meters: AssetMeter[];
  canManage: boolean;
  assetId: string;
  onChanged?: () => void;
}

const METER_TYPE_OPTIONS: { value: AssetMeterType; label: string; unit: string }[] = [
  { value: 'runtime_hours', label: 'Runtime hours', unit: 'hours' },
  { value: 'volume_gallons', label: 'Volume (gallons)', unit: 'gallons' },
  { value: 'cycles', label: 'Cycles', unit: 'cycles' },
  { value: 'kwh', label: 'Energy (kWh)', unit: 'kWh' },
  { value: 'generic_count', label: 'Generic count', unit: 'count' },
];

const formatValue = (value: string): string => {
  const n = parseFloat(value);
  return Number.isFinite(n) ? n.toLocaleString(undefined, { maximumFractionDigits: 4 }) : value;
};

const AssetMetersSection: React.FC<AssetMetersSectionProps> = ({
  meters,
  canManage,
  assetId,
  onChanged,
}) => {
  // Which meter+mode form is open. `add` targets the new-meter form.
  const [activeForm, setActiveForm] = useState<{ meterId: string; mode: 'record' | 'adjust' } | null>(
    null,
  );
  const [showAddForm, setShowAddForm] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // record-reading form state
  const [readingValue, setReadingValue] = useState('');
  const [readingIsAbsolute, setReadingIsAbsolute] = useState(true);
  const [readingIsEstimated, setReadingIsEstimated] = useState(false);

  // adjust form state
  const [adjustTarget, setAdjustTarget] = useState('');
  const [adjustReason, setAdjustReason] = useState('');

  // add-meter form state
  const [newName, setNewName] = useState('');
  const [newType, setNewType] = useState<AssetMeterType>('runtime_hours');
  const [newUnit, setNewUnit] = useState('hours');

  const resetForms = () => {
    setReadingValue('');
    setReadingIsAbsolute(true);
    setReadingIsEstimated(false);
    setAdjustTarget('');
    setAdjustReason('');
    setFormError(null);
  };

  const openRecord = (meterId: string) => {
    resetForms();
    setShowAddForm(false);
    setActiveForm({ meterId, mode: 'record' });
  };

  const openAdjust = (meterId: string) => {
    resetForms();
    setShowAddForm(false);
    setActiveForm({ meterId, mode: 'adjust' });
  };

  const closeForms = () => {
    setActiveForm(null);
    setShowAddForm(false);
    resetForms();
  };

  const handleRecordSubmit = async (e: React.FormEvent, meter: AssetMeter) => {
    e.preventDefault();
    setFormError(null);
    const value = parseFloat(readingValue);
    if (!Number.isFinite(value)) {
      setFormError('Enter a numeric value.');
      return;
    }
    setSubmitting(true);
    try {
      await assetMetersAPI.recordReading(meter.id, {
        value,
        is_absolute: readingIsAbsolute,
        is_estimated: readingIsEstimated,
      });
      closeForms();
      onChanged?.();
    } catch (err: any) {
      setFormError(err?.response?.data?.detail || 'Failed to record reading.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleAdjustSubmit = async (e: React.FormEvent, meter: AssetMeter) => {
    e.preventDefault();
    setFormError(null);
    const target = parseFloat(adjustTarget);
    if (!Number.isFinite(target)) {
      setFormError('Enter a numeric target.');
      return;
    }
    if (!adjustReason.trim()) {
      setFormError('A reason is required for an adjustment.');
      return;
    }
    setSubmitting(true);
    try {
      await assetMetersAPI.adjust(meter.id, { target, reason: adjustReason.trim() });
      closeForms();
      onChanged?.();
    } catch (err: any) {
      setFormError(err?.response?.data?.detail || 'Failed to adjust meter.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleAddSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    if (!newName.trim()) {
      setFormError('Give the meter a name.');
      return;
    }
    setSubmitting(true);
    try {
      await assetMetersAPI.create({
        asset: assetId,
        name: newName.trim(),
        meter_type: newType,
        unit: newUnit.trim(),
        source: 'manual',
      });
      setNewName('');
      setNewType('runtime_hours');
      setNewUnit('hours');
      closeForms();
      onChanged?.();
    } catch (err: any) {
      setFormError(err?.response?.data?.detail || 'Failed to add meter.');
    } finally {
      setSubmitting(false);
    }
  };

  const onTypeChange = (value: AssetMeterType) => {
    setNewType(value);
    const opt = METER_TYPE_OPTIONS.find((o) => o.value === value);
    if (opt) setNewUnit(opt.unit);
  };

  const renderMeterRow = (meter: AssetMeter) => {
    const isRecording = activeForm?.meterId === meter.id && activeForm.mode === 'record';
    const isAdjusting = activeForm?.meterId === meter.id && activeForm.mode === 'adjust';
    return (
      <div
        key={meter.id}
        data-testid={`asset-meter-row-${meter.id}`}
        style={{
          border: '1px solid #e2e8f0',
          borderRadius: 6,
          padding: '0.75rem',
          marginBottom: '0.75rem',
          background: '#fff',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.75rem', flexWrap: 'wrap' }}>
          <div>
            <strong>{meter.name}</strong>{' '}
            <span style={{ color: '#64748b', fontSize: '0.85rem' }}>· {meter.meter_type_display}</span>
            <div style={{ fontSize: '1.1rem', marginTop: '0.15rem' }}>
              <span data-testid={`asset-meter-value-${meter.id}`}>{formatValue(meter.current_value)}</span>
              {meter.unit ? ` ${meter.unit}` : ''}{' '}
              {meter.current_is_estimated ? (
                <span
                  data-testid={`asset-meter-estimated-${meter.id}`}
                  style={{ color: '#b7791f', fontSize: '0.75rem', fontWeight: 600 }}
                >
                  (estimated)
                </span>
              ) : (
                <span style={{ color: '#2f855a', fontSize: '0.75rem', fontWeight: 600 }}>(measured)</span>
              )}
            </div>
            <div style={{ color: '#64748b', fontSize: '0.8rem' }}>Source: {meter.source_display}</div>
          </div>
          {canManage && (
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.5rem', flexWrap: 'wrap' }}>
              <button
                type="button"
                className="action-button"
                onClick={() => openRecord(meter.id)}
                data-testid={`asset-meter-record-${meter.id}`}
                style={{ padding: '0.25rem 0.6rem', fontSize: '0.8rem' }}
              >
                Record reading
              </button>
              <button
                type="button"
                className="action-button"
                onClick={() => openAdjust(meter.id)}
                data-testid={`asset-meter-adjust-${meter.id}`}
                style={{ padding: '0.25rem 0.6rem', fontSize: '0.8rem' }}
              >
                Adjust
              </button>
            </div>
          )}
        </div>

        {canManage && isRecording && (
          <form
            onSubmit={(e) => handleRecordSubmit(e, meter)}
            data-testid={`asset-meter-record-form-${meter.id}`}
            style={{ marginTop: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}
          >
            <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', alignItems: 'center' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                <select
                  value={readingIsAbsolute ? 'absolute' : 'delta'}
                  onChange={(e) => setReadingIsAbsolute(e.target.value === 'absolute')}
                  data-testid={`asset-meter-record-mode-${meter.id}`}
                  aria-label="Reading mode"
                >
                  <option value="absolute">Set to</option>
                  <option value="delta">Add</option>
                </select>
              </label>
              <input
                type="number"
                step="any"
                value={readingValue}
                onChange={(e) => setReadingValue(e.target.value)}
                placeholder={meter.unit || 'value'}
                data-testid={`asset-meter-record-value-${meter.id}`}
                aria-label="Reading value"
                required
              />
              <label style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', fontSize: '0.85rem' }}>
                <input
                  type="checkbox"
                  checked={readingIsEstimated}
                  onChange={(e) => setReadingIsEstimated(e.target.checked)}
                  data-testid={`asset-meter-record-estimated-${meter.id}`}
                />
                Estimated
              </label>
            </div>
            {formError && (
              <div role="alert" style={{ color: '#c0392b', fontSize: '0.85rem' }}>
                {formError}
              </div>
            )}
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button
                type="submit"
                className="action-button"
                disabled={submitting}
                data-testid={`asset-meter-record-submit-${meter.id}`}
              >
                {submitting ? 'Saving…' : 'Save reading'}
              </button>
              <button type="button" className="action-button" onClick={closeForms} disabled={submitting}>
                Cancel
              </button>
            </div>
          </form>
        )}

        {canManage && isAdjusting && (
          <form
            onSubmit={(e) => handleAdjustSubmit(e, meter)}
            data-testid={`asset-meter-adjust-form-${meter.id}`}
            style={{ marginTop: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}
          >
            <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', alignItems: 'center' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', fontSize: '0.85rem' }}>
                Correct to
                <input
                  type="number"
                  step="any"
                  value={adjustTarget}
                  onChange={(e) => setAdjustTarget(e.target.value)}
                  placeholder={meter.unit || 'value'}
                  data-testid={`asset-meter-adjust-target-${meter.id}`}
                  aria-label="Adjust target"
                  required
                />
              </label>
              <input
                type="text"
                value={adjustReason}
                onChange={(e) => setAdjustReason(e.target.value)}
                placeholder="Reason (required)"
                data-testid={`asset-meter-adjust-reason-${meter.id}`}
                aria-label="Adjust reason"
                required
              />
            </div>
            {formError && (
              <div role="alert" style={{ color: '#c0392b', fontSize: '0.85rem' }}>
                {formError}
              </div>
            )}
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button
                type="submit"
                className="action-button"
                disabled={submitting}
                data-testid={`asset-meter-adjust-submit-${meter.id}`}
              >
                {submitting ? 'Saving…' : 'Save correction'}
              </button>
              <button type="button" className="action-button" onClick={closeForms} disabled={submitting}>
                Cancel
              </button>
            </div>
          </form>
        )}
      </div>
    );
  };

  return (
    <section className="asset-detail-section" data-testid="asset-meters-section">
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
        <h2 style={{ margin: 0 }}>Meters</h2>
        {canManage && !showAddForm && (
          <button
            type="button"
            className="action-button"
            onClick={() => {
              resetForms();
              setActiveForm(null);
              setShowAddForm(true);
            }}
            data-testid="asset-meters-add"
            style={{ padding: '0.4rem 0.85rem' }}
          >
            + Add meter
          </button>
        )}
      </div>

      {canManage && showAddForm && (
        <form
          onSubmit={handleAddSubmit}
          data-testid="asset-meters-add-form"
          style={{
            border: '1px solid #cdd9e4',
            borderRadius: 6,
            padding: '1rem',
            marginBottom: '1rem',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.75rem',
          }}
        >
          <div>
            <label htmlFor="asset-meter-name" style={{ display: 'block', fontSize: '0.85rem' }}>
              Name
            </label>
            <input
              id="asset-meter-name"
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              data-testid="asset-meter-name"
              required
            />
          </div>
          <div>
            <label htmlFor="asset-meter-type" style={{ display: 'block', fontSize: '0.85rem' }}>
              Type
            </label>
            <select
              id="asset-meter-type"
              value={newType}
              onChange={(e) => onTypeChange(e.target.value as AssetMeterType)}
              data-testid="asset-meter-type"
            >
              {METER_TYPE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="asset-meter-unit" style={{ display: 'block', fontSize: '0.85rem' }}>
              Unit
            </label>
            <input
              id="asset-meter-unit"
              type="text"
              value={newUnit}
              onChange={(e) => setNewUnit(e.target.value)}
              data-testid="asset-meter-unit"
            />
          </div>
          {formError && (
            <div role="alert" style={{ color: '#c0392b', fontSize: '0.85rem' }}>
              {formError}
            </div>
          )}
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button
              type="submit"
              className="action-button"
              disabled={submitting}
              data-testid="asset-meter-add-submit"
            >
              {submitting ? 'Adding…' : 'Add meter'}
            </button>
            <button type="button" className="action-button" onClick={closeForms} disabled={submitting}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {meters.length === 0 ? (
        <p data-testid="asset-meters-empty" style={{ color: '#64748b' }}>
          No meters yet.
        </p>
      ) : (
        meters.map((meter) => renderMeterRow(meter))
      )}
    </section>
  );
};

export default AssetMetersSection;
