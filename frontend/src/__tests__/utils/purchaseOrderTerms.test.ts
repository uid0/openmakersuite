/**
 * The client-side twin of `PurchaseOrder.payment_schedule` (op-bwo9/op-uc0o).
 *
 * Every case here mirrors one branch of the backend rule, so the create form's
 * live summary and the API agree about the payment an order implies.
 */
import {
  derivePaymentSchedule,
  freightTermsLabel,
  paymentScheduleSummary,
  paymentTermsLabel,
  PO_PAYMENT_TERMS_OPTIONS,
  priorityLabel,
} from '../../utils/purchaseOrderTerms';

const schedule = (overrides: Partial<Parameters<typeof derivePaymentSchedule>[0]> = {}) =>
  derivePaymentSchedule({
    orderDate: '2026-04-01',
    expectedDeliveryDate: '',
    paymentTerms: '',
    amount: 100,
    ...overrides,
  });

describe('utils/purchaseOrderTerms — derivePaymentSchedule', () => {
  it.each([
    ['net_15', '2026-04-16', 'Net 15 from order date'],
    ['net_30', '2026-05-01', 'Net 30 from order date'],
    ['net_60', '2026-05-31', 'Net 60 from order date'],
  ] as const)('%s falls due that many days after the order date', (terms, due, basis) => {
    expect(schedule({ paymentTerms: terms })).toEqual({
      due_date: due,
      amount: '100.00',
      basis,
    });
  });

  it('prepaid falls due on the order date itself', () => {
    expect(schedule({ paymentTerms: 'prepaid' })).toMatchObject({
      due_date: '2026-04-01',
      basis: 'Prepaid',
    });
  });

  it.each(['due_on_receipt', 'cod'] as const)(
    '%s falls due on the promised delivery date',
    (terms) => {
      expect(
        schedule({ paymentTerms: terms, expectedDeliveryDate: '2026-04-20' })
      ).toMatchObject({ due_date: '2026-04-20', basis: 'On delivery' });
    }
  );

  it('stays on delivery — with no date — when nothing has been promised yet', () => {
    expect(schedule({ paymentTerms: 'cod' })).toMatchObject({
      due_date: null,
      basis: 'On delivery',
    });
  });

  it('has no due date at all until terms are agreed', () => {
    expect(schedule()).toEqual({
      due_date: null,
      amount: '100.00',
      basis: 'No payment terms set',
    });
  });

  it('carries the running total as the amount, to the cent', () => {
    expect(schedule({ amount: 62.5 }).amount).toBe('62.50');
    expect(schedule({ amount: 0 }).amount).toBe('0.00');
    // An empty cart of unpriced lines must not render "NaN".
    expect(schedule({ amount: Number.NaN }).amount).toBe('0.00');
  });

  it('crosses a month boundary by calendar day, not by 30-day arithmetic', () => {
    expect(schedule({ orderDate: '2026-01-31', paymentTerms: 'net_30' }).due_date).toBe(
      '2026-03-02'
    );
  });

  it('offers every payment term the backend accepts', () => {
    expect(PO_PAYMENT_TERMS_OPTIONS.map((option) => option.value)).toEqual([
      'due_on_receipt',
      'net_15',
      'net_30',
      'net_60',
      'cod',
      'prepaid',
    ]);
  });
});

describe('utils/purchaseOrderTerms — labels', () => {
  it('labels a stored choice', () => {
    expect(priorityLabel('urgent')).toBe('Urgent');
    expect(paymentTermsLabel('net_30')).toBe('Net 30');
    expect(freightTermsLabel('fob_origin')).toBe('FOB Origin');
  });

  it('reads a blank or unknown term as an em dash', () => {
    expect(paymentTermsLabel('')).toBe('—');
    expect(freightTermsLabel(null)).toBe('—');
    expect(priorityLabel('made_up')).toBe('—');
  });
});

describe('utils/purchaseOrderTerms — paymentScheduleSummary', () => {
  it('reads amount, due date and basis in one line', () => {
    expect(
      paymentScheduleSummary({
        due_date: '2026-05-01',
        amount: '100.00',
        basis: 'Net 30 from order date',
      })
    ).toBe('$100.00 — due May 1, 2026 (Net 30 from order date)');
  });

  it('says so plainly when nothing anchors the payment', () => {
    expect(
      paymentScheduleSummary({ due_date: null, amount: '0.00', basis: 'No payment terms set' })
    ).toBe('$0.00 — no due date (No payment terms set)');
  });

  it('falls back to an em dash when the order carries no schedule', () => {
    expect(paymentScheduleSummary(null)).toBe('—');
  });
});
