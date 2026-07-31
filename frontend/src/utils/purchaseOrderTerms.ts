/**
 * Purchase-order header terms and the payment they imply (op-bwo9 / op-uc0o).
 *
 * Shared by the PO create form and the PO detail page so a term reads the same
 * in both, and so the create form can show the payment a cart implies *before*
 * the order exists: `derivePaymentSchedule` is the client-side twin of
 * `PurchaseOrder.payment_schedule`.
 *
 * The backend stays the authority. Once the order is saved the detail page
 * renders the API's `payment_schedule` verbatim — this mirror only covers the
 * gap where there is nothing to ask the server about yet.
 */
import {
  PurchaseOrderFreightTerms,
  PurchaseOrderPaymentSchedule,
  PurchaseOrderPaymentTerms,
  PurchaseOrderPriority,
} from '../services/api';
import { addDaysToYmd, formatDateOnly } from './dates';

export interface TermsOption<T extends string> {
  value: T;
  label: string;
}

export const PO_PRIORITY_LABELS: Record<PurchaseOrderPriority, string> = {
  low: 'Low',
  normal: 'Normal',
  high: 'High',
  urgent: 'Urgent',
};

export const PO_PAYMENT_TERMS_LABELS: Record<PurchaseOrderPaymentTerms, string> = {
  due_on_receipt: 'Due on receipt',
  net_15: 'Net 15',
  net_30: 'Net 30',
  net_60: 'Net 60',
  cod: 'COD',
  prepaid: 'Prepaid',
};

export const PO_FREIGHT_TERMS_LABELS: Record<PurchaseOrderFreightTerms, string> = {
  fob_origin: 'FOB Origin',
  fob_destination: 'FOB Destination',
  prepaid: 'Prepaid',
  collect: 'Collect',
  third_party: 'Third-party',
};

const toOptions = <T extends string>(labels: Record<T, string>): TermsOption<T>[] =>
  (Object.keys(labels) as T[]).map((value) => ({ value, label: labels[value] }));

export const PO_PRIORITY_OPTIONS = toOptions(PO_PRIORITY_LABELS);
export const PO_PAYMENT_TERMS_OPTIONS = toOptions(PO_PAYMENT_TERMS_LABELS);
export const PO_FREIGHT_TERMS_OPTIONS = toOptions(PO_FREIGHT_TERMS_LABELS);

const labelFor = (
  labels: Record<string, string>,
  value: string | null | undefined,
  fallback = '—',
): string => (value && labels[value]) || fallback;

/** Display label for a stored choice; an em dash when blank or unrecognised. */
export const priorityLabel = (value: string | null | undefined): string =>
  labelFor(PO_PRIORITY_LABELS, value);

export const paymentTermsLabel = (value: string | null | undefined): string =>
  labelFor(PO_PAYMENT_TERMS_LABELS, value);

export const freightTermsLabel = (value: string | null | undefined): string =>
  labelFor(PO_FREIGHT_TERMS_LABELS, value);

/** Days-until-due for the "net N" terms — mirrors `PurchaseOrder.NET_PAYMENT_DAYS`. */
const NET_PAYMENT_DAYS: Partial<Record<PurchaseOrderPaymentTerms, number>> = {
  net_15: 15,
  net_30: 30,
  net_60: 60,
};

export interface PaymentScheduleInput {
  /** Business date the order was placed, 'YYYY-MM-DD'. */
  orderDate: string;
  /** Promised delivery date, 'YYYY-MM-DD' or '' when none is set. */
  expectedDeliveryDate: string;
  paymentTerms: PurchaseOrderPaymentTerms | '';
  /** Running total the payment covers. */
  amount: number;
}

/**
 * The payment a cart implies, in the API's `payment_schedule` shape.
 *
 * Same rule as the server: net terms fall due that many days after the order
 * date, prepaid on the order date itself, and receipt-anchored terms on the
 * promised delivery — which is null (and stays "On delivery") until one is
 * given. No terms agreed means no due date at all.
 */
export function derivePaymentSchedule({
  orderDate,
  expectedDeliveryDate,
  paymentTerms,
  amount,
}: PaymentScheduleInput): PurchaseOrderPaymentSchedule {
  const netDays = paymentTerms ? NET_PAYMENT_DAYS[paymentTerms] : undefined;

  let dueDate: string | null;
  let basis: string;

  if (netDays !== undefined) {
    dueDate = addDaysToYmd(orderDate, netDays) || null;
    basis = `${PO_PAYMENT_TERMS_LABELS[paymentTerms as PurchaseOrderPaymentTerms]} from order date`;
  } else if (paymentTerms === 'prepaid') {
    dueDate = orderDate || null;
    basis = 'Prepaid';
  } else if (paymentTerms === 'due_on_receipt' || paymentTerms === 'cod') {
    dueDate = expectedDeliveryDate || null;
    basis = 'On delivery';
  } else {
    dueDate = null;
    basis = 'No payment terms set';
  }

  return {
    due_date: dueDate,
    amount: Number.isFinite(amount) ? amount.toFixed(2) : '0.00',
    basis,
  };
}

const CURRENCY_FORMAT = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
});

/**
 * One-line reading of a payment schedule: how much, when it falls due, and on
 * what basis — e.g. "$100.00 — due Apr 30, 2026 (Net 30 from order date)".
 */
export function paymentScheduleSummary(
  schedule: PurchaseOrderPaymentSchedule | null | undefined,
): string {
  if (!schedule) return '—';
  const amount = parseFloat(schedule.amount);
  const money = Number.isNaN(amount) ? '—' : CURRENCY_FORMAT.format(amount);
  const due = schedule.due_date ? `due ${formatDateOnly(schedule.due_date)}` : 'no due date';
  return `${money} — ${due} (${schedule.basis})`;
}
