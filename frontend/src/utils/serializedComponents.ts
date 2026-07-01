/**
 * Presentation helpers for serialized-component UI (#818/#819).
 *
 * The backend already returns human labels (`status_display`,
 * `action_display`) and the legal transitions (`available_actions`), so these
 * helpers only cover the frontend-owned bits: badge colours and the button
 * styling / confirmation semantics for each lifecycle action.
 */
import {
  SerializedComponentAction,
  SerializedComponentStatus,
} from '../services/api';

/** Mantine colour for a unit's lifecycle status badge. */
export const SERIALIZED_STATUS_COLOR: Record<SerializedComponentStatus, string> = {
  received: 'gray',
  in_stock: 'teal',
  installed: 'blue',
  removed: 'yellow',
  consumed: 'grape',
  retired: 'orange',
  disposed: 'red',
};

/** Fallback labels for lifecycle actions (backend also sends action_display). */
export const SERIALIZED_ACTION_LABEL: Record<SerializedComponentAction, string> = {
  receive: 'Receive',
  install: 'Install',
  remove: 'Remove',
  consume: 'Consume',
  retire: 'Retire',
  dispose: 'Dispose',
};

/** Button colour per action so destructive transitions read as such. */
export const SERIALIZED_ACTION_COLOR: Record<SerializedComponentAction, string> = {
  receive: 'teal',
  install: 'blue',
  remove: 'yellow',
  consume: 'grape',
  retire: 'orange',
  dispose: 'red',
};

/** Install needs a target asset; dispose needs a reason — both prompt first. */
export const SERIALIZED_ACTIONS_NEEDING_INPUT: ReadonlySet<SerializedComponentAction> =
  new Set<SerializedComponentAction>(['install', 'dispose']);

export function serializedStatusColor(status: SerializedComponentStatus): string {
  return SERIALIZED_STATUS_COLOR[status] ?? 'gray';
}

export function serializedActionColor(action: SerializedComponentAction): string {
  return SERIALIZED_ACTION_COLOR[action] ?? 'blue';
}

export function serializedActionLabel(action: SerializedComponentAction): string {
  return SERIALIZED_ACTION_LABEL[action] ?? action;
}

/**
 * Split a textarea of pasted serial numbers (one per line, or
 * comma-separated) into a clean, de-duplicated, order-preserving list.
 */
export function parseSerialNumbers(raw: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const token of raw.split(/[\n,]/)) {
    const serial = token.trim();
    if (!serial || seen.has(serial)) continue;
    seen.add(serial);
    out.push(serial);
  }
  return out;
}
