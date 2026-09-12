-- Rows a kit edit may have rewritten through KitSerializer._apply_supplier_terms.
-- READ ONLY. Nothing here rewrites a row: what to do about a hit is the
-- captain's call, not this branch's.
--
-- Three separate questions, because the three columns the defect touched leave
-- three different amounts of evidence behind.

-- (1) THE PRIMARY FLAG — bounded, never identified.
-- A demotion overwrites a boolean and leaves no trace, so no query can say
-- "this flag was moved". What a query CAN give is the population at risk: kits
-- with more than one supplier link, where the primary one is also the most
-- recently written one. That is the SHAPE a silent promotion leaves; it is also
-- the shape an ordinary deliberate edit leaves, so this is a shortlist to ask
-- an operator about, NOT a list of corrupted rows.
SELECT
    i.id            AS kit_id,
    i.sku           AS kit_sku,
    i.name          AS kit_name,
    s.name          AS primary_supplier,
    isup.updated_at AS primary_last_written,
    (SELECT max(o.updated_at)
       FROM inventory_itemsupplier o
      WHERE o.item_id = i.id AND o.id <> isup.id) AS newest_sibling_written,
    (SELECT count(*)
       FROM inventory_itemsupplier o
      WHERE o.item_id = i.id) AS link_count
FROM inventory_inventoryitem i
JOIN inventory_itemsupplier isup ON isup.item_id = i.id AND isup.is_primary
JOIN inventory_supplier s ON s.id = isup.supplier_id
WHERE i.is_kit
  AND (SELECT count(*) FROM inventory_itemsupplier o WHERE o.item_id = i.id) > 1
  AND isup.updated_at >= COALESCE(
        (SELECT max(o.updated_at) FROM inventory_itemsupplier o
          WHERE o.item_id = i.id AND o.id <> isup.id),
        isup.updated_at)
ORDER BY isup.updated_at DESC;

-- (2) THE PACK SIZE — identifiable, because PriceHistory recorded it.
-- The reset to 1 landed before #1063 removed the setdefault. A kit link
-- standing at 1 today whose own price history once recorded a larger pack size
-- is a row the reset reached.
SELECT
    i.id        AS kit_id,
    i.sku       AS kit_sku,
    s.name      AS supplier,
    isup.quantity_per_package AS pack_size_now,
    max(ph.quantity_per_package) AS pack_size_previously_recorded,
    max(ph.recorded_at)          AS last_history_row
FROM inventory_itemsupplier isup
JOIN inventory_inventoryitem i ON i.id = isup.item_id AND i.is_kit
JOIN inventory_supplier s ON s.id = isup.supplier_id
JOIN inventory_pricehistory ph ON ph.item_supplier_id = isup.id
WHERE isup.quantity_per_package = 1
GROUP BY i.id, i.sku, s.name, isup.quantity_per_package
HAVING max(ph.quantity_per_package) > 1
ORDER BY last_history_row DESC;

-- (3) CANDIDATE FABRICATED PRICE ROWS — review the trace.
-- A reset pack size re-derived the case price and filed a PriceHistory row for
-- a price nobody quoted. Candidate rows sit at pack size 1 with unit_cost =
-- package_cost, immediately after a row at a larger pack size on the same link.
-- That trace cannot prove whether the change was deliberate, so every result
-- needs operator review.
WITH ordered_history AS (
    SELECT
        ph.*,
        lag(ph.quantity_per_package) OVER (
            PARTITION BY ph.item_supplier_id
            ORDER BY ph.recorded_at, ph.id
        ) AS previous_quantity_per_package
    FROM inventory_pricehistory ph
)
SELECT
    i.sku            AS kit_sku,
    s.name           AS supplier,
    ph.id            AS candidate_price_history_id,
    ph.recorded_at,
    ph.unit_cost,
    ph.package_cost,
    ph.quantity_per_package,
    ph.change_type
FROM ordered_history ph
JOIN inventory_itemsupplier isup ON isup.id = ph.item_supplier_id
JOIN inventory_inventoryitem i ON i.id = isup.item_id AND i.is_kit
JOIN inventory_supplier s ON s.id = isup.supplier_id
WHERE ph.quantity_per_package = 1
  AND ph.unit_cost IS NOT NULL
  AND ph.unit_cost = ph.package_cost
  AND ph.previous_quantity_per_package > 1
ORDER BY ph.recorded_at DESC;
