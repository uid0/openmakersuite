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
 * The way out of an `(item, supplier)` collision, in the operator's terms.
 *
 * Shared by the pre-flight refusal and the report of a collision the server
 * caught first, so both name the same escape route: the rows are written one at
 * a time and there is no order in which two of them can trade suppliers, so the
 * pair has to be freed by a save of its own.
 */
export const SUPPLIER_PAIR_ESCAPE =
  'Two rows cannot exchange suppliers in one save. Remove the row that holds it, save, then ' +
  'add it back with the other supplier.';

/** DRF's `UniqueTogetherValidator` sentence, which names nothing to act on. */
const UNIQUE_TOGETHER_REASON = /must make a unique set/i;

/**
 * Reasons the editor's current rows cannot be written, in operator language.
 *
 * Checked before anything is sent, for the same reason the packaging chain is:
 * the item write lands first, so a row the server is certain to reject would
 * otherwise fail *after* half the save had already happened. Every reason here
 * is one the operator can act on without leaving the page.
 *
 * The `(item, supplier)` check walks the real write sequence rather than
 * judging rows in isolation, because whether a pair is free depends entirely on
 * what has already been written when a row's turn comes. Only a conflict the
 * walk can actually prove is refused — anything it cannot prove is sent, and a
 * rejection is reported with the same escape route (`supplierFieldErrors`).
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

  // What the server still holds when the first create/update goes out. Removals
  // run ahead of every other write in `saveSupplierRelationships`, so a row the
  // editor no longer keeps has already let its pair go by then.
  const heldBy = new Map<number, number>();
  saved.forEach((row) => {
    if (keptIds.has(row.id)) {
      heldBy.set(row.supplier, row.id);
    }
  });

  relationshipWriteOrder(relationships).forEach((index) => {
    const relationship = relationships[index];
    if (relationship.supplier === null) return;

    const persisted =
      relationship.id === undefined ? undefined : savedById.get(relationship.id);
    // A row that keeps its supplier never frees the pair — it may not even send
    // a request. Only a row moving away releases what it held.
    if (
      persisted !== undefined &&
      persisted.supplier !== relationship.supplier &&
      heldBy.get(persisted.supplier) === persisted.id
    ) {
      heldBy.delete(persisted.supplier);
    }

    const holderId = heldBy.get(relationship.supplier);
    if (holderId === undefined || holderId === relationship.id) return;

    // Still held, and its holder has not been written yet — every retry repeats
    // this same order and collides in the same place.
    const holderIndex = relationships.findIndex((row) => row.id === holderId);
    const targetName =
      suppliers.find((supplier) => supplier.id === relationship.supplier)?.name ??
      `supplier #${relationship.supplier}`;
    errors.push(
      `Supplier #${index + 1} cannot take ${targetName}: Supplier #${holderIndex + 1} still ` +
        `holds it on this item. ${SUPPLIER_PAIR_ESCAPE}`
    );
  });

  relationships.forEach((relationship, index) => {
    const label = relationshipLabel(relationship, index, suppliers);

    if (relationship.supplier === null) {
      errors.push(`Supplier #${index + 1} has no supplier selected.`);
      return;
    }

    const first = seen.get(relationship.supplier);
    if (first === undefined) {
      seen.set(relationship.supplier, index);
    }

    // Only a row this save actually writes can be rejected by the server; a
    // persisted row nobody touched sends no request, so refusing it would only
    // block work that would have succeeded.
    if (
      relationship.id !== undefined &&
      !relationshipChanged(relationship, savedById.get(relationship.id))
    ) {
      return;
    }

    // `ItemSupplier.supplier_sku` is a non-blank CharField, so an empty SKU is
    // a guaranteed 400 rather than a stored blank.
    if (relationship.supplier_sku.trim() === '') {
      errors.push(`${label} needs a supplier SKU.`);
    }

    // `unique_together = [["item", "supplier"]]` — two rows for one supplier
    // cannot both be stored, and which one survives would be an accident.
    if (first !== undefined) {
      errors.push(
        `${label} is listed twice (Supplier #${first + 1} and #${index + 1}); ` +
          'an item can only link a supplier once.'
      );
    }
  });

  return errors;
};

/**
 * One editor row, built from the server's copy of it.
 *
 * The single mapping used both when the page loads and after every write, so
 * the editable row and the `ItemSupplier` `relationshipChanged` compares it
 * against are always built the same way. Two consequences that matter:
 *
 * 1. **A derived cost reaches the boxes.** `ItemSupplier.save()` derives from
 *    the DELTA against the stored row, so a box still holding the figure the
 *    server has just superseded is not inert on the next save — it MOVED, and a
 *    moved unit cost governs and re-prices the case price.
 * 2. **A row cannot look dirty forever.** Building the two sides differently
 *    would leave a field permanently unequal, and the page would re-PATCH an
 *    untouched row on every save.
 */
export const relationshipFromSaved = (saved: ItemSupplier): SupplierRelationship => ({
  id: saved.id,
  supplier: saved.supplier,
  supplier_sku: saved.supplier_sku,
  supplier_url: saved.supplier_url,
  unit_cost: saved.unit_cost,
  package_cost: saved.package_cost,
  quantity_per_package: saved.quantity_per_package,
  average_lead_time: saved.average_lead_time,
  is_primary: saved.is_primary,
});

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
    // The one rejection whose own wording names nothing the operator can do:
    // the pre-flight walk proves what it can and lets the rest through, so this
    // is where an `(item, supplier)` collision it could not prove has to become
    // actionable rather than "must make a unique set".
    if (UNIQUE_TOGETHER_REASON.test(reason)) {
      parts.push(`this supplier is already linked to this item by another row. ${SUPPLIER_PAIR_ESCAPE}`);
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
