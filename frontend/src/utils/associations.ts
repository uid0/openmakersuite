/**
 * Labels for the work-order / committee associations a purchase order and its
 * lines can carry (op-shb9).
 *
 * Shared by the PO create form and the PO detail page so a job reads the same
 * in both pickers.
 */
import { WorkOrder } from '../types';

/** Minimal work-order identity the API embeds next to an association. */
export interface WorkOrderIdentity {
  id: string;
  short_id: string;
  display_title: string;
  status: string;
}

/** Minimal committee (SIG) identity the API embeds next to an association. */
export interface OwningGroupIdentity {
  id: number;
  name: string;
}

/**
 * Label a work order in a picker: its short id plus whatever it is called
 * (the backend's display_title — template title, else reported problem, else
 * asset). Falls back through the list serializer's own fields so a payload
 * without display_title still reads sensibly.
 */
export const workOrderOptionLabel = (workOrder: WorkOrder): string => {
  const title = workOrder.display_title || workOrder.maintenance_item_title || workOrder.asset_name;
  return title ? `${workOrder.short_id} — ${title}` : workOrder.short_id;
};

/** Label an attached work order from the API's embedded identity block. */
export const workOrderDetailsLabel = (details: WorkOrderIdentity | null): string =>
  details ? `${details.short_id} — ${details.display_title}` : '—';
