/**
 * Helpers for writing the inventory item form's supplier-relationship editor
 * back to the `item-suppliers` endpoints.
 *
 * The editor used to be decorative: `InventoryItemFormPage` carried a
 * `TODO: Implement supplier relationship saving via ItemSupplier API` where the
 * writes belong, so every edit made in that section was dropped on Save without
 * a word. `ItemSupplierViewSet` is a full `ModelViewSet`, so most of this is
 * client-side bookkeeping: which rows changed, in what order to write them, and
 * how to name a rejection so the operator can act on it. The one change no
 * row-by-row order can express — rows exchanging suppliers — goes to the
 * server's atomic batch instead (`needsAtomicSupplierWrite`).
 */
import { ItemSupplierBatchEntry, ItemSupplierWritePayload } from '../services/api';
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
 * The way out of an `(item, supplier)` collision the server caught, in the
 * operator's terms.
 *
 * Every collision between this page's own rows is either resolved before
 * anything is sent — rows that trade suppliers go out as one atomic batch — or
 * refused as a supplier listed twice. So a collision the server still reports
 * means a row this page does not show holds the supplier: someone else linked
 * it after the page loaded, and only a reload shows it.
 */
export const SUPPLIER_PAIR_ESCAPE =
  'Another row on this item that this page does not show already holds it — reload the page ' +
  "to see the item's current suppliers, then choose again.";

/** DRF's `UniqueTogetherValidator` sentence, which names nothing to act on. */
const UNIQUE_TOGETHER_REASON = /must make a unique set/i;

/**
 * Whether the row-by-row writes of `saveSupplierRelationships` would collide on
 * an `(item, supplier)` pair this same save is freeing — the rows then have to
 * go to the server as one atomic batch instead.
 *
 * Walks the real write sequence rather than judging rows in isolation, because
 * whether a pair is free depends entirely on what has already been written when
 * a row's turn comes. A swap (row A X→Y, row B Y→X) collides in every order; so
 * does a new primary row claiming the supplier an existing row is moving away
 * from, since the primary is written first. Every retry repeats the same order,
 * so without the batch such a save could never land.
 *
 * A pair two rows would still share once everything is written is not a
 * collision this answers: that is a real conflict, and
 * `validateSupplierRelationships` refuses it before anything is sent.
 */
export const needsAtomicSupplierWrite = (
  relationships: SupplierRelationship[],
  saved: ItemSupplier[] = []
): boolean => {
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

  return relationshipWriteOrder(relationships).some((index) => {
    const relationship = relationships[index];
    if (relationship.supplier === null) return false;

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
    return holderId !== undefined && holderId !== relationship.id;
  });
};

/**
 * Reasons the editor's current rows cannot be written, in operator language.
 *
 * Checked before anything is sent, for the same reason the packaging chain is:
 * the item write lands first, so a row the server is certain to reject would
 * otherwise fail *after* half the save had already happened. Every reason here
 * is one the operator can act on without leaving the page.
 *
 * Rows trading suppliers are not refused: `needsAtomicSupplierWrite` sends them
 * as one batch. Only a supplier two rows would still share once the save is
 * complete is a real conflict.
 */
export const validateSupplierRelationships = (
  relationships: SupplierRelationship[],
  suppliers: Supplier[],
  saved: ItemSupplier[] = []
): string[] => {
  const errors: string[] = [];
  const seen = new Map<number, number>();
  const savedById = new Map(saved.map((row) => [row.id, row]));

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

    // `(item, supplier)` is unique — two rows for one supplier cannot both be
    // stored, and which one survives would be an accident. Judged on the end
    // state before the untouched-row skip below: it is a conflict whichever of
    // the two rows this save writes.
    if (first !== undefined) {
      errors.push(
        `${label} is listed twice (Supplier #${first + 1} and #${index + 1}); ` +
          'an item can only link a supplier once.'
      );
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

/**
 * The offered fields of one row, as the endpoint takes them.
 *
 * `loaded` is the server's copy the row was built from, on an update; a create
 * has none. An update always carries that copy's `version`, so a row someone
 * else has written since this page loaded it is refused (a 409
 * `stale_version`, `inventory/services/link_version.py`) instead of having its
 * newer values overwritten by the ones on this page.
 */
export const relationshipPayload = (
  relationship: SupplierRelationship,
  itemId?: string,
  loaded?: ItemSupplier
): ItemSupplierWritePayload => ({
  ...(itemId === undefined ? {} : { item: itemId }),
  supplier: relationship.supplier as number,
  supplier_sku: relationship.supplier_sku,
  supplier_url: relationship.supplier_url,
  unit_cost: relationship.unit_cost,
  package_cost: relationship.package_cost,
  quantity_per_package: relationship.quantity_per_package,
  // Omitted where the editor recorded nothing, rather than sent as a number
  // the operator never typed. That is how every other write path gets the
  // model's default of 7 — the kit form's `supplier_terms` and
  // `_sync_primary_supplier` both leave the key out — so a create takes the
  // default and an update leaves a stored quote alone. Sending `0` instead,
  // which this row used to seed, recorded same-day pickup.
  //
  // A number still equal to the one loaded IS sent. The server keeps the
  // stored source when an echoed number equals the stored one
  // (`inventory/services/lead_time_source.py`), and the one way the two could
  // differ — someone else, or the measuring task, wrote the row after this
  // page loaded it — is now refused through `version` rather than dodged by
  // leaving the lead time out while every other field was still overwritten.
  ...(relationship.average_lead_time === null
    ? {}
    : { average_lead_time: relationship.average_lead_time }),
  is_primary: relationship.is_primary,
  ...(loaded === undefined ? {} : { version: loaded.version }),
});

/**
 * Whether a rejected write was refused because this page's copy is stale.
 *
 * The server's documented refusal (`docs/API_ERROR_CONTRACT.md`): a 409 whose
 * `error.code` is `stale_version`. Recognised by code, not by wording.
 */
export const isStaleSupplierLink = (err: unknown): boolean => {
  const response = (err as { response?: { status?: number; data?: unknown } })?.response;
  const code = (response?.data as { error?: { code?: unknown } } | undefined)?.error?.code;
  return response?.status === 409 && code === 'stale_version';
};

/** The server's sentence for a stale refusal, with this page's own fallback. */
const staleSupplierLinkMessage = (err: unknown): string => {
  const message = (err as { response?: { data?: { error?: { message?: unknown } } } })?.response
    ?.data?.error?.message;
  return typeof message === 'string' && message.trim() !== ''
    ? message
    : 'Someone else changed this supplier after you loaded the page, so your changes to it were ' +
        'not saved. Your copy is out of date: reload the suppliers to see the current values.';
};

/**
 * The server's copies of the rows this page's own promotion has just demoted.
 *
 * Saving a row as primary makes the SERVER clear the flag on the item's other
 * suppliers, and that demotion is a write: it moves each demoted row's version
 * on. The rows this page loaded as primary therefore no longer hold the version
 * they were loaded at — not because anyone else touched them, but because this
 * save did. Left alone, the page's own next write to such a row (the demotion
 * the editor shows, or an edit alongside it) would be refused as stale.
 *
 * A row is adopted from `fresh` only when the ONLY offered field that moved
 * since it was loaded is the primary flag, now cleared — exactly the demotion
 * this save caused. Anything else that moved means someone else wrote the row,
 * and it keeps its loaded version so that write is refused, not overwritten.
 */
export const adoptOwnDemotions = (
  saved: ItemSupplier[],
  fresh: ItemSupplier[],
  promotedId: number
): ItemSupplier[] => {
  const freshById = new Map(fresh.map((row) => [row.id, row]));
  return saved.map((row) => {
    const current = freshById.get(row.id);
    if (row.id === promotedId || !row.is_primary || current === undefined || current.is_primary) {
      return row;
    }
    const onlyDemoted = !relationshipChanged(relationshipFromSaved({ ...row, is_primary: false }), current);
    return onlyDemoted ? current : row;
  });
};

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
    // A cleared box counts as a change even though `relationshipPayload` then
    // sends no lead time: the PATCH leaves the stored quote alone and the row
    // is replaced by the server's copy, so the box refills with what is
    // actually stored rather than staying blank and dirty forever. An operator
    // is told the truth — a recorded lead time cannot be un-recorded while the
    // column is NOT NULL (`oms-lead-time-nullable`).
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
 *
 * `stale` marks the one refusal that saving again cannot fix: the row changed
 * on the server after this page loaded it. The page offers a reload for it and
 * never retries or overwrites on the operator's behalf.
 */
export const supplierWriteError = (
  err: unknown,
  relationship: { supplier: number | null; supplier_name?: string },
  index: number,
  suppliers: Supplier[]
): { detail: string; stale: boolean } => {
  const label = relationshipLabel(relationship, index, suppliers);
  if (isStaleSupplierLink(err)) {
    return { detail: `${label} — ${staleSupplierLinkMessage(err)}`, stale: true };
  }
  return {
    detail: `${label} — ${supplierFieldErrors(err) ?? extractErrorMessage(err, 'please try again.')}`,
    stale: false,
  };
};

/**
 * One row as an entry of the atomic batch: the same offered fields and
 * `version` a single-row write sends, plus the row's `id` on an update. The
 * batch names the item once, so no entry carries it.
 */
export const relationshipBatchEntry = (
  relationship: SupplierRelationship,
  loaded?: ItemSupplier
): ItemSupplierBatchEntry => ({
  ...(relationship.id === undefined ? {} : { id: relationship.id }),
  ...relationshipPayload(relationship, undefined, loaded),
});

/**
 * A refused atomic batch, reported against the rows it names.
 *
 * The batch writes nothing when it refuses, so every row the operator changed
 * is still theirs to fix. A stale refusal names the one row whose copy is out
 * of date (`error.details.id`); a validation refusal carries one error map per
 * entry (`error.details.links`), and each non-empty one is reported against its
 * row the way a single-row rejection would be.
 */
export const supplierBatchError = (
  err: unknown,
  rows: { relationship: SupplierRelationship; index: number }[],
  suppliers: Supplier[]
): { detail: string; stale: boolean } => {
  const error = (err as { response?: { data?: { error?: { details?: unknown } } } })?.response
    ?.data?.error;

  if (isStaleSupplierLink(err)) {
    const staleId = (error?.details as { id?: unknown } | undefined)?.id;
    const row = rows.find(({ relationship }) => relationship.id === staleId) ?? rows[0];
    return supplierWriteError(err, row.relationship, row.index, suppliers);
  }

  const perEntry = (error?.details as { links?: unknown } | undefined)?.links;
  if (Array.isArray(perEntry)) {
    const parts = rows.flatMap(({ relationship, index }, position) => {
      const entryErrors = perEntry[position];
      if (!entryErrors || typeof entryErrors !== 'object' || Object.keys(entryErrors).length === 0) {
        return [];
      }
      return [
        supplierWriteError({ response: { data: entryErrors } }, relationship, index, suppliers)
          .detail,
      ];
    });
    if (parts.length > 0) {
      return { detail: parts.join(' '), stale: false };
    }
  }

  return { detail: extractErrorMessage(err, 'please try again.'), stale: false };
};
