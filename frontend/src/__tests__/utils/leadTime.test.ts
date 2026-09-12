import {
  LEAD_TIME_NOT_RECORDED,
  aggregateLeadTimeText,
  leadTimeText,
} from '../../utils/leadTime';

describe('lead-time formatting', () => {
  test('distinguishes absence, planning defaults, and unknown provenance', () => {
    expect(leadTimeText(null, null)).toBe(LEAD_TIME_NOT_RECORDED);
    expect(leadTimeText(7, 'default')).toBe('7 days (planning default)');
    expect(leadTimeText(7, 'unknown')).toBe('7 days (provenance unknown)');
  });
});

describe('purchase order supplier aggregate', () => {
  test('never presents an absence or included default as a bare number', () => {
    expect(aggregateLeadTimeText(null, null)).toBe(LEAD_TIME_NOT_RECORDED);
    expect(aggregateLeadTimeText(7, 'default')).toBe(
      '7 days (includes planning default)'
    );
    expect(aggregateLeadTimeText(0, 'recorded')).toBe('0 days');
  });
});
