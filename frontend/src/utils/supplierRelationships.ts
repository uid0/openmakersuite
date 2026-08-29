/**
 * Helpers for writing the inventory item form's supplier-relationship editor
 * back to the `item-suppliers` endpoints.
 *
 * The editor used to be decorative: `InventoryItemFormPage` carried a
 * `TODO: Implement supplier relationship saving via ItemSupplier API` where the
 * writes belong, so every edit made in that section was dropped on Save without
 * a word. Nothing new is needed on the server — `ItemSupplierViewSet` is a full
 * `ModelViewSet` — so all of this is client-side bookkeeping: which rows changed,
 * in what order to write them, and how to name a rejection so the operator can
 * act on it.
 */
import { ItemSupplierWritePayload } from '../services/api';
import { ItemSupplier, Supplier } from '../types';
import { SupplierRelationship } from '../components/SupplierRelationshipForm';
import { extractErrorMessage } from './extractErrorMessage';

/**
 * The fields the relationship editor offers, mapped to the labels it shows.
 *
 * This is the whole contract of this module: a rejection is reported against
 * the label the operator typed into, and only these fields are ever written.
 * `ItemSupplier` carries more (`package_upc`, `unit_upc`, the package
 * dimensions and weight, `is_active`, `is_discontinued`, `notes`) — the form
 * does not show any of it, which is why updates are PATCH: a PUT would blank
 * every field this page has no control for.
 */
export const SUPPLIER_FIELD_LABELS: Record<string, string> = {
  supplier: 'Supplier',
  supplier_sku: 'Supplier SKU',
  supplier_url: 'Supplier URL',
  unit_cost: 'Unit Cost',
  package_cost: 'Package Cost',
  quantity_per_package: 'Quantity per Package',
  average_lead_time: 'Average Lead Time (days)',
  is_primary: 'Primary Supplier',
};

/**
 * How a row is named in a message: the chosen supplier, else its position.
 *
 * Takes a persisted `ItemSupplier` as readily as an editor row, so a failed
 * removal names its supplier the same way a failed create or update does — the
 * removed row is gone from the editor by then and only the server's copy is
 * left to name it with.
 */
export const relationshipLabel = (
  relationship: { supplier: number | null; supplier_name?: string },
  index: number,
  suppliers: Supplier[]
): string =>
  suppliers.find((supplier) => supplier.id === relationship.supplier)?.name ||
  relationship.supplier_name ||
  `Supplier #${index + 1}`;

/**
 * Reasons the editor's current rows cannot be written, in operator language.
 *
 * Checked before anything is sent, for the same reason the packaging chain is:
 * the item write lands first, so a row the server is certain to reject would
 * otherwise fail *after* half the save had already happened. Every reason here
 * is one the operator can act on without leaving the page.
 */
export const validateSupplierRelationships = (
  relationships: SupplierRelationship[],
  suppliers: Supplier[],
  saved: ItemSupplier[] = []
): string[] => {
  const errors: string[] = [];
  const seen = new Map<number, number>();
  const savedById = new Map(saved.map((row) => [row.id, row]));
  const keptIds = new Set(
    relationships
      .map((relationship) => relationship.id)
      .filter((id): id is number => id !== undefined)
  );

  relationships.forEach((relationship, index) => {
    const label = relationshipLabel(relationship, index, suppliers);

    if (relationship.supplier === null) {
      errors.push(`Supplier #${index + 1} has no supplier selected.`);
      return;
    }

    // `ItemSupplier.supplier_sku` is a non-blank CharField, so an empty SKU is
    // a guaranteed 400 rather than a stored blank.
    if (relationship.supplier_sku.trim() === '') {
      errors.push(`${label} needs a supplier SKU.`);
    }

    // `unique_together = [["item", "supplier"]]` — two rows for one supplier
    // cannot both be stored, and which one survives would be an accident.
    const first = seen.get(relationship.supplier);
    if (first !== undefined) {
      errors.push(
        `${label} is listed twice (Supplier #${first + 1} and #${index + 1}); ` +
          'an item can only link a supplier once.'
      );
    } else {
      seen.set(relationship.supplier, index);
    }

    // The same `unique_together`, seen from the other side: this row is being
    // moved onto a supplier another row still holds on the server. Rows are
    // written one at a time in a fixed order, so the pair is still taken when
    // this row's turn comes — a 400 no retry can get past, since every retry
    // repeats the same order. Refused here with the way out named, because the
    // operator cannot infer it from what the server says.
    const persisted = relationship.id === undefined ? undefined : savedById.get(relationship.id);
    if (persisted !== undefined && persisted.supplier !== relationship.supplier) {
      const holder = saved.find(
        (row) =>
          row.id !== persisted.id && row.supplier === relationship.supplier && keptIds.has(row.id)
      );
      if (holder !== undefined) {
        const holderIndex = relationships.findIndex((row) => row.id === holder.id);
        errors.push(
          `Supplier #${index + 1} (${relationshipLabel(persisted, index, suppliers)}) cannot ` +
            `move to ${label} while Supplier #${holderIndex + 1} still holds it — two rows ` +
            'cannot exchange suppliers in one save. Remove one of those two rows, save, then ' +
            'add it back with the other supplier.'
        );
      }
    }
  });

  return errors;
};

/** The offered fields of one row, as the endpoint takes them. */
export const relationshipPayload = (
  relationship: SupplierRelationship,
  itemId?: string
): ItemSupplierWritePayload => ({
  ...(itemId === undefined ? {} : { item: itemId }),
  supplier: relationship.supplier as number,
  supplier_sku: relationship.supplier_sku,
  supplier_url: relationship.supplier_url,
  unit_cost: relationship.unit_cost,
  package_cost: relationship.package_cost,
  quantity_per_package: relationship.quantity_per_package,
  average_lead_time: relationship.average_lead_time,
  is_primary: relationship.is_primary,
});

/**
 * Whether a persisted row differs from the server's copy in any offered field.
 *
 * Compared field by field rather than by a whole-object equality so a field the
 * form does not offer can never make a row look dirty — and so a row the
 * operator did not touch sends no request at all.
 */
export const relationshipChanged = (
  relationship: SupplierRelationship,
  saved: ItemSupplier | undefined
): boolean => {
  if (saved === undefined) {
    return true;
  }
  return (
    relationship.supplier !== saved.supplier ||
    relationship.supplier_sku !== saved.supplier_sku ||
    relationship.supplier_url !== saved.supplier_url ||
    relationship.unit_cost !== saved.unit_cost ||
    relationship.package_cost !== saved.package_cost ||
    relationship.quantity_per_package !== saved.quantity_per_package ||
    relationship.average_lead_time !== saved.average_lead_time ||
    relationship.is_primary !== saved.is_primary
  );
};

/**
 * Row indices in the order they must be written: the primary one first.
 *
 * "Only one primary" is the server's invariant, not this page's — saving a row
 * with `is_primary` true clears the flag on the item's other suppliers
 * (`inventory.services.suppliers.enforce_single_primary`, inside the same
 * transaction as the save). Writing the promotion first therefore makes the
 * one-primary outcome a property of that single request: if a later row's write
 * fails, the item still has exactly one primary — the one the operator picked —
 * rather than none, which is what a demote-first order could leave behind.
 */
export const relationshipWriteOrder = (relationships: SupplierRelationship[]): number[] => {
  const indices = relationships.map((_, index) => index);
  return [
    ...indices.filter((index) => relationships[index].is_primary),
    ...indices.filter((index) => !relationships[index].is_primary),
  ];
};

/**
 * The reason a write was rejected, in terms of the form's own labels.
 *
 * A rejected `ItemSupplier` write names the offending field, and neither shape
 * it arrives in survives `extractErrorMessage`: the standardized envelope
 * (`config.api_errors`) puts the field map under `error.details` behind the
 * flat message "One or more fields failed validation.", and an endpoint not yet
 * converted returns DRF's bare `{field: ["reason", ...]}`. Left to the generic
 * helper, the operator would be told only that something failed — with the one
 * fact they need to fix it sitting unread in the response.
 */
export const supplierFieldErrors = (err: unknown): string | null => {
  const body = (err as { response?: { data?: unknown } })?.response?.data as
    | Record<string, { details?: unknown } | unknown>
    | undefined;
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return null;
  }
  const details = (body.error as { details?: unknown } | undefined)?.details;
  const data = details && typeof details === 'object' && !Array.isArray(details) ? details : body;

  const parts: string[] = [];
  Object.entries(data).forEach(([field, value]) => {
    if (field === 'detail' || field === 'error') {
      return;
    }
    const reason = Array.isArray(value)
      ? value.find((entry) => typeof entry === 'string' && entry.trim() !== '')
      : value;
    if (typeof reason !== 'string' || reason.trim() === '') {
      return;
    }
    parts.push(
      field === 'non_field_errors' ? reason : `${SUPPLIER_FIELD_LABELS[field] ?? field}: ${reason}`
    );
  });

  return parts.length > 0 ? parts.join(' ') : null;
};

/**
 * A failed row write, re-thrown as a `detail` payload so the page reports it
 * the same way it reports a backend error — naming the supplier, because the
 * editor can hold several and only one of them failed.
 */
export const supplierWriteError = (
  err: unknown,
  relationship: { supplier: number | null; supplier_name?: string },
  index: number,
  suppliers: Supplier[]
): { detail: string } => ({
  detail: `${relationshipLabel(relationship, index, suppliers)} — ${
    supplierFieldErrors(err) ?? extractErrorMessage(err, 'please try again.')
  }`,
});
