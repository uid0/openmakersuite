import {
  LEAD_TIME_NOT_RECORDED,
  aggregateLeadTimeText,
  leadTimeText,
} from '../../utils/leadTime';

const supplierValueSurfaces = [
  'supplier relationship editor',
  'item detail supplier table',
  'supplier detail item table',
  'scan chosen supplier',
  'scan supplier detail',
  'scan order summary',
  'admin dashboard reorder row',
  'purchase order line',
  'item metrics strip',
  'serialized forecast',
];

describe.each(supplierValueSurfaces)('%s lead time', () => {
  test('never presents an absence or default as a bare number', () => {
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
