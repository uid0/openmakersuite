/**
 * Purchase Order Form Page
 * Create new purchase orders with supplier selection, item population,
 * asset support, and freeform line items.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  CreatePurchaseOrderData,
  CreatePurchaseOrderItem,
  inventoryAPI,
  purchaseOrderAPI,
  PurchaseOrderFreightTerms,
  PurchaseOrderPaymentTerms,
  PurchaseOrderPriority,
  ReorderDataAsset,
  ReorderDataItem,
  ReorderDataKit,
  ReorderDataSupplier,
  scannerAPI,
  sigAPI,
  supplierAgreementAPI,
  workOrderAPI,
} from '../services/api';
import { SIG, SupplierAgreement, WorkOrder } from '../types';
import '../styles/PurchaseOrderFormPage.css';
import { workOrderOptionLabel } from '../utils/associations';
import { utcYmd, ymdToUtcDateTime } from '../utils/dates';
import { extractErrorMessage } from '../utils/extractErrorMessage';
import {
  derivePaymentSchedule,
  paymentScheduleSummary,
  PO_FREIGHT_TERMS_OPTIONS,
  PO_PAYMENT_TERMS_OPTIONS,
  PO_PRIORITY_OPTIONS,
} from '../utils/purchaseOrderTerms';

interface SelectedItem extends ReorderDataItem {
  selected: boolean;
  quantity: number;
  cases: number;
  unit_cost_override?: string;
  // Per-case cost, kept in sync with unit_cost_override (case = unit × qpp).
  // Display/edit convenience only — submit always uses the per-unit value.
  case_cost_override?: string;
  expected_shipment_date?: string;
}

interface SelectedAsset extends ReorderDataAsset {
  selected: boolean;
  quantity: number;
  unit_cost: string;
}

/**
 * A kit line on the cart (op-8n0). `quantity` counts KITS, never component
 * units — a kit IS the package, which is why there is deliberately no
 * cases/units triangle here the way there is on SelectedItem.
 */
interface SelectedKit extends ReorderDataKit {
  selected: boolean;
  quantity: number;
  unit_cost_override?: string;
  expanded: boolean;
}

interface FreeformItem {
  id: string;
  description: string;
  quantity: number;
  unit_cost: string;
}

// Normalize a derived cost to a clean string, stripping IEEE-754 float noise
// (e.g. 39.959999999999994 -> "39.96") without cent-level rounding. Six
// decimals is well beyond cents, so paired unit/case costs stay consistent
// even when the case size doesn't divide evenly (avoids cent drift on submit).
const formatCostValue = (value: number): string => {
  if (!Number.isFinite(value)) return '';
  return String(parseFloat(value.toFixed(6)));
};

/**
 * Did the operator actually TYPE a price, and is it a real one?
 *
 * `>= 0`, not `> 0` (op-9m2v). A vendor donating an asset states a price of
 * zero, and the asset guard's `parseFloat(cost) > 0` refused to let that line
 * onto an order at all — with the button silently disabled and nothing saying
 * why — while the freeform guard on the same screen already accepted it, and
 * the server refuses only a MISSING cost. An empty box is still not a price:
 * this is "a number was entered and it is not negative", never truthiness.
 */
const hasTypedPrice = (cost: string | null | undefined): boolean => {
  if (cost === null || cost === undefined || cost.trim() === '') return false;
  const amount = parseFloat(cost);
  return Number.isFinite(amount) && amount >= 0;
};

const PurchaseOrderFormPage: React.FC = () => {
  const navigate = useNavigate();

  // Data state
  const [suppliers, setSuppliers] = useState<ReorderDataSupplier[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Form state
  const [selectedSupplierId, setSelectedSupplierId] = useState<number | null>(null);
  const [expectedDeliveryDate, setExpectedDeliveryDate] = useState('');
  const [notes, setNotes] = useState('');

  // Header terms (op-bwo9). `orderDate` is left blank on purpose: an order
  // placed now takes the server's own timestamp, and a value is only sent when
  // the operator backdates an order entered after the fact.
  const [orderDate, setOrderDate] = useState('');
  const [priority, setPriority] = useState<PurchaseOrderPriority>('normal');
  const [paymentTerms, setPaymentTerms] = useState<PurchaseOrderPaymentTerms | ''>('');
  const [freightTerms, setFreightTerms] = useState<PurchaseOrderFreightTerms | ''>('');

  // Optional purchase/pricing agreement this order is placed under (op-yoos).
  // Loaded per supplier — only that supplier's active agreements are offered.
  const [agreements, setAgreements] = useState<SupplierAgreement[]>([]);
  const [selectedAgreementId, setSelectedAgreementId] = useState<number | null>(null);

  // Order-level association (op-shb9): "this order is for job X" / "…on behalf
  // of committee Y". Both optional and independent of the supplier, so the
  // options are loaded once. Per-line association is done on the detail page.
  const [workOrders, setWorkOrders] = useState<WorkOrder[]>([]);
  const [sigs, setSigs] = useState<SIG[]>([]);
  const [selectedWorkOrderId, setSelectedWorkOrderId] = useState<string>('');
  const [selectedCommitteeId, setSelectedCommitteeId] = useState<number | null>(null);

  // Line items state
  const [selectedItems, setSelectedItems] = useState<SelectedItem[]>([]);
  const [selectedAssets, setSelectedAssets] = useState<SelectedAsset[]>([]);
  const [selectedKits, setSelectedKits] = useState<SelectedKit[]>([]);
  const [freeformItems, setFreeformItems] = useState<FreeformItem[]>([]);

  // Manual product entry state
  const [productSearchTerm, setProductSearchTerm] = useState('');
  const [productBarcode, setProductBarcode] = useState('');
  const [searchingProduct, setSearchingProduct] = useState(false);
  const [debouncedSearchTerm, setDebouncedSearchTerm] = useState('');

  // Submission state
  const [submitting, setSubmitting] = useState(false);

  const loadReorderData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await purchaseOrderAPI.getReorderData();
      setSuppliers(response.data.suppliers);
    } catch (err: any) {
      console.error('Error loading reorder data:', err);
      setError(extractErrorMessage(err, '') || err.message || 'Failed to load reorder data');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadReorderData();
  }, [loadReorderData]);

  // Options for the order-level association pickers (op-shb9). Only unfinished
  // jobs are offered — you do not raise a PO for a closed work order. Neither
  // list is required to create an order, so a failure just leaves that picker
  // empty rather than blocking the form.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [openWos, activeWos, mySigs] = await Promise.allSettled([
        workOrderAPI.listWorkOrders({ status: 'open' }),
        workOrderAPI.listWorkOrders({ status: 'in_progress' }),
        sigAPI.listMySIGs(),
      ]);
      if (cancelled) return;
      const results = (settled: PromiseSettledResult<any>): WorkOrder[] =>
        settled.status === 'fulfilled' ? (settled.value?.data?.results ?? []) : [];
      setWorkOrders([...results(openWos), ...results(activeWos)]);
      setSigs(mySigs.status === 'fulfilled' ? (mySigs.value?.data?.results ?? []) : []);
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  // When a supplier is selected, populate items and assets
  useEffect(() => {
    if (selectedSupplierId) {
      const supplier = suppliers.find((s) => s.id === selectedSupplierId);
      if (supplier) {
        // Initialize items with all selected by default
        setSelectedItems(
          supplier.items.map((item) => {
            const qpp = item.quantity_per_package || 1;
            // For case-packed items, round suggested units up to whole cases
            // so the user always orders at least the suggested coverage.
            const cases = qpp > 1 ? Math.max(1, Math.ceil(item.suggested_quantity / qpp)) : item.suggested_quantity;
            const quantity = qpp > 1 ? cases * qpp : item.suggested_quantity;
            return {
              ...item,
              selected: true,
              quantity,
              cases,
            };
          })
        );
        // Initialize assets with none selected
        setSelectedAssets(
          supplier.assets.map((asset) => ({
            ...asset,
            selected: false,
            quantity: 1,
            unit_cost: '',
          }))
        );
        // Kits (op-8n0). ``?? []`` is load-bearing: every existing PO-form
        // test builds a supplier fixture without this key, and the backend only
        // started sending it with this feature.
        //
        // Default selection follows whatever the kit currently IS: something
        // inside it is low -> checked, like an item; nothing low -> unchecked,
        // like an asset.
        setSelectedKits(
          (supplier.kits ?? []).map((kit) => ({
            ...kit,
            selected: (kit.low_component_count ?? 0) > 0,
            quantity: 1,
            expanded: false,
          }))
        );
        // Clear freeform items when changing supplier
        setFreeformItems([]);
      }
    } else {
      setSelectedItems([]);
      setSelectedAssets([]);
      setSelectedKits([]);
      setFreeformItems([]);
    }
  }, [selectedSupplierId, suppliers]);

  // Load the selected supplier's active purchase/pricing agreements (op-yoos).
  // Changing supplier always clears the previously picked agreement — an
  // agreement belongs to exactly one supplier and the backend rejects a
  // mismatch.
  useEffect(() => {
    setSelectedAgreementId(null);

    if (!selectedSupplierId) {
      setAgreements([]);
      return;
    }

    let cancelled = false;
    (async () => {
      try {
        const response = await supplierAgreementAPI.listBySupplier(selectedSupplierId);
        if (!cancelled) {
          setAgreements(response?.data?.results ?? []);
        }
      } catch (err) {
        // A missing agreement list must never block creating an order.
        console.error('Error loading supplier agreements:', err);
        if (!cancelled) setAgreements([]);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [selectedSupplierId]);

  // Debounce search term for automatic search
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearchTerm(productSearchTerm);
    }, 500); // 500ms debounce delay

    return () => clearTimeout(timer);
  }, [productSearchTerm]);

  // Auto-search when debounced term changes (and barcode is empty)
  useEffect(() => {
    if (debouncedSearchTerm.trim() && !productBarcode.trim() && selectedSupplierId && !searchingProduct) {
      handleProductSearch(debouncedSearchTerm, '');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearchTerm, selectedSupplierId]);

  // Note: Barcode search is triggered on Enter key or blur event in the input field
  // This is handled in the onKeyDown and onBlur handlers below

  const selectedSupplier = suppliers.find((s) => s.id === selectedSupplierId);
  const selectedAgreement = agreements.find((a) => a.id === selectedAgreementId);

  // Toggle item selection
  const toggleItemSelection = (itemSupplierId: number) => {
    setSelectedItems((prev) =>
      prev.map((item) =>
        item.item_supplier_id === itemSupplierId ? { ...item, selected: !item.selected } : item
      )
    );
  };

  // Update item quantity (units). For case-packed items, also re-derive cases
  // as ceil(units / qpp) so the two stay consistent.
  const updateItemQuantity = (itemSupplierId: number, quantity: number) => {
    setSelectedItems((prev) =>
      prev.map((item) => {
        if (item.item_supplier_id !== itemSupplierId) return item;
        const units = Math.max(1, quantity);
        const qpp = item.quantity_per_package || 1;
        const cases = qpp > 1 ? Math.max(1, Math.ceil(units / qpp)) : units;
        return { ...item, quantity: units, cases };
      })
    );
  };

  // Update item case count. Drives quantity (units) = cases * qpp.
  const updateItemCases = (itemSupplierId: number, cases: number) => {
    setSelectedItems((prev) =>
      prev.map((item) => {
        if (item.item_supplier_id !== itemSupplierId) return item;
        const qpp = item.quantity_per_package || 1;
        const newCases = Math.max(1, cases);
        return { ...item, cases: newCases, quantity: newCases * qpp };
      })
    );
  };

  // Update the per-unit cost override. For case-packed items, keep the paired
  // case-cost field in sync (case = unit × qpp). The per-unit value the user
  // typed is stored verbatim (full precision) and remains the source of truth
  // for submit.
  const updateItemUnitCost = (itemSupplierId: number, unitCostOverride: string) => {
    setSelectedItems((prev) =>
      prev.map((item) => {
        if (item.item_supplier_id !== itemSupplierId) return item;
        const qpp = item.quantity_per_package || 1;
        const parsed = parseFloat(unitCostOverride);
        const caseCost =
          unitCostOverride.trim() === '' || Number.isNaN(parsed)
            ? ''
            : formatCostValue(parsed * qpp);
        return { ...item, unit_cost_override: unitCostOverride, case_cost_override: caseCost };
      })
    );
  };

  // Update the per-case cost. Derives the per-unit cost (unit = case / qpp) at
  // full precision — the per-unit value is what submit sends, so no cent drift
  // is introduced by rounding the case cost to display precision.
  const updateItemCaseCost = (itemSupplierId: number, caseCostOverride: string) => {
    setSelectedItems((prev) =>
      prev.map((item) => {
        if (item.item_supplier_id !== itemSupplierId) return item;
        const qpp = item.quantity_per_package || 1;
        const parsed = parseFloat(caseCostOverride);
        const unitCost =
          caseCostOverride.trim() === '' || Number.isNaN(parsed)
            ? ''
            : formatCostValue(parsed / qpp);
        return { ...item, case_cost_override: caseCostOverride, unit_cost_override: unitCost };
      })
    );
  };

  // Per-case cost placeholder: the supplier's saved package cost when present,
  // otherwise derived from the saved unit cost (unit × qpp).
  const caseCostPlaceholderFor = (item: SelectedItem): string => {
    if (item.package_cost != null && item.package_cost !== '') return item.package_cost;
    const qpp = item.quantity_per_package || 1;
    // `item.unit_cost` is null when nobody recorded one, and `|| '0'` used to
    // turn that into a placeholder of "0" — a price suggestion for a price the
    // system does not have (op-9m2v). No suggestion is the honest placeholder.
    if (item.unit_cost == null || item.unit_cost === '') return '';
    const unit = parseFloat(item.unit_cost);
    return Number.isNaN(unit) ? '' : formatCostValue(unit * qpp);
  };

  // Update item expected shipment date
  const updateItemShipmentDate = (itemSupplierId: number, expectedShipmentDate: string) => {
    setSelectedItems((prev) =>
      prev.map((item) =>
        item.item_supplier_id === itemSupplierId ? { ...item, expected_shipment_date: expectedShipmentDate } : item
      )
    );
  };

  // Toggle asset selection
  const toggleAssetSelection = (assetId: string) => {
    setSelectedAssets((prev) =>
      prev.map((asset) => (asset.id === assetId ? { ...asset, selected: !asset.selected } : asset))
    );
  };

  // Update asset
  const updateAsset = (assetId: string, updates: Partial<SelectedAsset>) => {
    setSelectedAssets((prev) =>
      prev.map((asset) => (asset.id === assetId ? { ...asset, ...updates } : asset))
    );
  };

  // Add freeform item
  const addFreeformItem = () => {
    setFreeformItems((prev) => [
      ...prev,
      {
        id: crypto.randomUUID(),
        description: '',
        quantity: 1,
        unit_cost: '',
      },
    ]);
  };

  // Update freeform item
  const updateFreeformItem = (id: string, updates: Partial<FreeformItem>) => {
    setFreeformItems((prev) =>
      prev.map((item) => (item.id === id ? { ...item, ...updates } : item))
    );
  };

  // Remove freeform item
  const removeFreeformItem = (id: string) => {
    setFreeformItems((prev) => prev.filter((item) => item.id !== id));
  };

  // Handle product search or barcode scan
  const handleProductSearch = async (searchTerm?: string, barcode?: string) => {
    if (!selectedSupplierId) {
      setError('Please select a supplier first');
      return;
    }

    // Use provided parameters or state values
    const termToSearch = searchTerm !== undefined ? searchTerm : productSearchTerm;
    const barcodeToSearch = barcode !== undefined ? barcode : productBarcode;

    // Don't search if both are empty
    if (!termToSearch.trim() && !barcodeToSearch.trim()) {
      return;
    }

    setSearchingProduct(true);
    setError(null);

    try {
      let inventoryItem;
      let itemId: string;

      // Try barcode lookup first if barcode is provided
      if (barcodeToSearch.trim()) {
        try {
          const lookupResponse = await scannerAPI.dispatch(barcodeToSearch.trim());
          if (lookupResponse.data.target_type === 'inventory_item' && lookupResponse.data.target_id) {
            itemId = lookupResponse.data.target_id;
          } else {
            setError('Barcode does not match an inventory item');
            setSearchingProduct(false);
            return;
          }
        } catch (err: any) {
          setError(extractErrorMessage(err, 'Barcode not found'));
          setSearchingProduct(false);
          return;
        }
      } else if (termToSearch.trim()) {
        // Search by name or SKU
        const searchResponse = await inventoryAPI.listItems({ search: termToSearch.trim() });
        const items = searchResponse.data.results;
        if (items.length === 0) {
          // Don't show error for empty results during auto-search, only clear the field if it was manual
          if (searchTerm === undefined) {
            // This was an auto-search, silently fail
            setSearchingProduct(false);
            return;
          }
          setError('No products found matching your search');
          setSearchingProduct(false);
          return;
        }
        if (items.length > 1) {
          // For auto-search, don't show error for multiple results - user can continue typing
          if (searchTerm === undefined) {
            setSearchingProduct(false);
            return;
          }
          setError(`Multiple products found. Please be more specific or use barcode scan.`);
          setSearchingProduct(false);
          return;
        }
        itemId = items[0].id;
      } else {
        setSearchingProduct(false);
        return;
      }

      // Get the inventory item details
      const itemResponse = await inventoryAPI.getItem(itemId);
      inventoryItem = itemResponse.data;

      // Get item suppliers for this item
      const suppliersResponse = await inventoryAPI.getItemSuppliers(itemId);
      const itemSuppliers = suppliersResponse.data.results;

      // Find the item_supplier for the selected supplier
      const matchingItemSupplier = itemSuppliers.find(
        (is: any) => is.supplier === selectedSupplierId && is.is_active && !(is as any).is_discontinued
      );

      if (!matchingItemSupplier) {
        setError(
          `This product is not available from the selected supplier. Please add it as a freeform item or select a different supplier.`
        );
        setSearchingProduct(false);
        return;
      }

      // Check if item is already in the list
      const existingItem = selectedItems.find(
        (item) => item.item_supplier_id === matchingItemSupplier.id
      );

      if (existingItem) {
        setError('This item is already in the list');
        setSearchingProduct(false);
        return;
      }

      // Add the item to selectedItems
      const qpp = matchingItemSupplier.quantity_per_package || 1;
      const newItem: SelectedItem = {
        item_supplier_id: matchingItemSupplier.id,
        item_id: inventoryItem.id,
        item_name: inventoryItem.name,
        item_sku: inventoryItem.sku || '',
        current_stock: inventoryItem.current_stock || 0,
        minimum_stock: inventoryItem.minimum_stock || 0,
        reorder_quantity: inventoryItem.reorder_quantity || 0,
        suggested_quantity: qpp,
        // `?? null`, never `|| '0'`: a link with no price on file is not a
        // free one, and the server now REFUSES a line it cannot price rather
        // than storing the zero this used to prefill (op-9m2v). `0` here is a
        // vendor that charges nothing, so it must survive.
        unit_cost: matchingItemSupplier.unit_cost?.toString() ?? null,
        unit_cost_state:
          matchingItemSupplier.unit_cost == null ? 'not_recorded' : 'known',
        unit_cost_detail:
          matchingItemSupplier.unit_cost == null
            ? `No price is recorded for ${inventoryItem.name} from this supplier. `
              + 'Enter a unit cost below, or record one on the supplier link.'
            : null,
        package_cost: matchingItemSupplier.package_cost?.toString() ?? null,
        quantity_per_package: qpp,
        lead_time_days: matchingItemSupplier.average_lead_time || 7,
        supplier_sku: matchingItemSupplier.supplier_sku || '',
        supplier_url: matchingItemSupplier.supplier_url || '',
        is_primary: matchingItemSupplier.is_primary || false,
        line_total:
          matchingItemSupplier.unit_cost == null
            ? null
            : (parseFloat(matchingItemSupplier.unit_cost.toString()) * qpp).toString(),
        selected: true,
        quantity: qpp,
        cases: 1,
      };

      setSelectedItems((prev) => [...prev, newItem]);
      setProductSearchTerm('');
      setProductBarcode('');
      setDebouncedSearchTerm(''); // Clear debounced term after successful add
    } catch (err: any) {
      console.error('Error searching for product:', err);
      // Only show error if this was a manual search (not auto-search)
      if (searchTerm !== undefined || barcode !== undefined) {
        setError(err.response?.data?.error || extractErrorMessage(err, '') || err.message || 'Failed to find product');
      }
    } finally {
      setSearchingProduct(false);
    }
  };

  // The price a line will actually be ordered at: what the operator typed, or
  // what the supplier link records, or NOTHING (op-9m2v). `null` is the third
  // answer and it is not zero — `parseFloat(null)` is NaN and the old total
  // folded that in as 0, so an unpriced line quietly made the order look
  // cheaper than it would be. The server refuses such a line outright now, so
  // the form has to see it too, and say so before the operator submits.
  const effectiveUnitCost = (line: {
    unit_cost: string | null;
    unit_cost_override?: string;
  }): number | null => {
    const raw =
      line.unit_cost_override && line.unit_cost_override.trim() !== ''
        ? line.unit_cost_override
        : line.unit_cost;
    if (raw === null || raw === undefined || raw === '') return null;
    const parsed = parseFloat(raw);
    return Number.isNaN(parsed) ? null : parsed;
  };

  // Calculate totals. Only the lines we can price are summed; the ones we
  // cannot are COUNTED, so a partial total says so rather than looking whole.
  const itemsTotal = selectedItems
    .filter((item) => item.selected)
    .reduce((sum, item) => {
      const unitCost = effectiveUnitCost(item);
      return unitCost === null ? sum : sum + unitCost * item.quantity;
    }, 0);

  const assetsTotal = selectedAssets
    .filter((asset) => asset.selected && asset.unit_cost)
    .reduce((sum, asset) => sum + parseFloat(asset.unit_cost || '0') * asset.quantity, 0);

  const freeformTotal = freeformItems
    .filter((item) => item.description && item.unit_cost)
    .reduce((sum, item) => sum + parseFloat(item.unit_cost || '0') * item.quantity, 0);

  const kitsTotal = selectedKits
    .filter((kit) => kit.selected)
    .reduce((sum, kit) => {
      const unitCost = effectiveUnitCost(kit);
      return unitCost === null ? sum : sum + unitCost * kit.quantity;
    }, 0);

  const grandTotal = itemsTotal + assetsTotal + freeformTotal + kitsTotal;

  // The lines this order cannot price, and what the operator does about each.
  // Derived on every render from the same rule the total uses, so the warning
  // and the number can never disagree.
  const unpricedLines = [
    ...selectedItems
      .filter((item) => item.selected && effectiveUnitCost(item) === null)
      .map((item) => ({
        key: `item-${item.item_supplier_id}`,
        name: item.item_name,
        detail: item.unit_cost_detail,
      })),
    ...selectedKits
      .filter((kit) => kit.selected && effectiveUnitCost(kit) === null)
      .map((kit) => ({ key: `kit-${kit.item_supplier_id}`, name: kit.name, detail: null })),
  ];

  const selectedItemCount = selectedItems.filter((item) => item.selected).length;
  const selectedAssetCount = selectedAssets.filter((asset) => asset.selected).length;
  const freeformItemCount = freeformItems.filter((item) => item.description && item.unit_cost).length;
  const selectedKitCount = selectedKits.filter((kit) => kit.selected).length;
  const totalLineItems =
    selectedItemCount + selectedAssetCount + freeformItemCount + selectedKitCount;

  // The double-order guard (op-8n0), DERIVED on every render and never stored.
  // Selecting a kit while its component rows stay checked orders everything
  // twice — and those rows are checked by default BECAUSE they are low, which
  // is the same reason the kit was suggested. So this fires every time the
  // feature works as intended, and has to be visible rather than clever.
  //
  // Deliberately NOT auto-deselected: unchecking the kit would then have to
  // re-check them to stay symmetric (unexplainable), and buying a kit plus a
  // spare black cartridge is a legitimate order.
  const componentIdsInSelectedKits = new Set(
    selectedKits
      .filter((kit) => kit.selected)
      .flatMap((kit) => kit.components.map((component) => component.id))
  );
  const conflictingItems = selectedItems.filter(
    (item) => item.selected && componentIdsInSelectedKits.has(item.item_id)
  );
  // Every component of every kit, selected or not — this drives the permanent
  // "in kit" chip, which stays visible while the kit is unchecked.
  const componentIdsInAnyKit = new Set(
    selectedKits.flatMap((kit) => kit.components.map((component) => component.id))
  );

  const deselectConflictingItems = () => {
    const conflicting = new Set(conflictingItems.map((item) => item.item_id));
    setSelectedItems((prev) =>
      prev.map((item) => (conflicting.has(item.item_id) ? { ...item, selected: false } : item))
    );
  };

  const toggleKit = (kitId: string) => {
    setSelectedKits((prev) =>
      prev.map((kit) => (kit.id === kitId ? { ...kit, selected: !kit.selected } : kit))
    );
  };

  const updateKitQuantity = (kitId: string, quantity: number) => {
    setSelectedKits((prev) =>
      prev.map((kit) => (kit.id === kitId ? { ...kit, quantity: Math.max(1, quantity) } : kit))
    );
  };

  const toggleKitExpanded = (kitId: string) => {
    setSelectedKits((prev) =>
      prev.map((kit) => (kit.id === kitId ? { ...kit, expanded: !kit.expanded } : kit))
    );
  };

  // The payment this cart implies, recomputed on every render from the running
  // total and the chosen terms (op-uc0o). The order does not exist yet, so
  // there is no server `payment_schedule` to read — this mirrors the same rule
  // the API will apply the moment it is created. A blank order date means
  // "now", which is the day the server would stamp.
  const livePaymentSchedule = derivePaymentSchedule({
    orderDate: orderDate || utcYmd(new Date()),
    expectedDeliveryDate,
    paymentTerms,
    amount: grandTotal,
  });

  // Validate form
  const canSubmit =
    selectedSupplierId &&
    totalLineItems > 0 &&
    !submitting &&
    // Check that all selected assets have unit_cost
    selectedAssets.filter((a) => a.selected).every((a) => hasTypedPrice(a.unit_cost)) &&
    // No inventory or kit line may go out at a price nobody has stated. The
    // server refuses one; refusing here first means the operator is told which
    // line and what to do about it, instead of a 400 after the fact (op-9m2v).
    unpricedLines.length === 0 &&
    // Check that all freeform items have description and unit_cost
    freeformItems.every((item) => !item.description || hasTypedPrice(item.unit_cost));

  // Submit order
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit || !selectedSupplierId) return;

    setSubmitting(true);
    setError(null);

    try {
      const items: CreatePurchaseOrderItem[] = [];

      // Add selected inventory items
      selectedItems
        .filter((item) => item.selected)
        .forEach((item) => {
          const itemData: CreatePurchaseOrderItem = {
            item_supplier_id: item.item_supplier_id,
            quantity: item.quantity,
          };
          // For case-packed items, send the case count explicitly so the
          // backend records exactly what the user entered (no ceil rounding).
          if ((item.quantity_per_package || 1) > 1) {
            itemData.order_in_packages = item.cases;
          }
          // Add unit_cost override if provided
          if (item.unit_cost_override && item.unit_cost_override.trim()) {
            const overrideCost = parseFloat(item.unit_cost_override);
            if (!isNaN(overrideCost) && overrideCost >= 0) {
              itemData.unit_cost = overrideCost;
            }
          }
          // Add expected_shipment_date if provided
          if (item.expected_shipment_date && item.expected_shipment_date.trim()) {
            itemData.expected_shipment_date = item.expected_shipment_date;
          }
          items.push(itemData);
        });

      // Add selected kits (op-8n0). One line per kit, quantity in KITS. A kit
      // already has an ItemSupplier, so this is an ordinary inventory line as
      // far as the backend is concerned — the decomposition happens on receipt.
      selectedKits
        .filter((kit) => kit.selected)
        .forEach((kit) => {
          const kitData: CreatePurchaseOrderItem = {
            item_supplier_id: kit.item_supplier_id,
            quantity: kit.quantity,
          };
          if (kit.unit_cost_override && kit.unit_cost_override.trim()) {
            const overrideCost = parseFloat(kit.unit_cost_override);
            if (!isNaN(overrideCost) && overrideCost >= 0) {
              kitData.unit_cost = overrideCost;
            }
          }
          items.push(kitData);
        });

      // Add selected assets
      selectedAssets
        .filter((asset) => asset.selected)
        .forEach((asset) => {
          items.push({
            asset_id: asset.id,
            quantity: asset.quantity,
            unit_cost: parseFloat(asset.unit_cost),
          });
        });

      // Add freeform items
      freeformItems
        .filter((item) => item.description && item.unit_cost)
        .forEach((item) => {
          items.push({
            description: item.description,
            quantity: item.quantity,
            unit_cost: parseFloat(item.unit_cost),
          });
        });

      const orderData: CreatePurchaseOrderData = {
        supplier: selectedSupplierId,
        items,
        ...(selectedAgreementId && { supplier_agreement: selectedAgreementId }),
        ...(selectedWorkOrderId && { work_order: selectedWorkOrderId }),
        ...(selectedCommitteeId && { owning_group: selectedCommitteeId }),
        ...(expectedDeliveryDate && { expected_delivery_date: expectedDeliveryDate }),
        // Header terms (op-bwo9). `order_date` is a datetime, so a backdate
        // goes out as midday UTC — the day picked is the day the server
        // derives the payment schedule from. Left off entirely when blank so
        // an order placed now keeps the server's own timestamp.
        ...(orderDate && { order_date: ymdToUtcDateTime(orderDate) }),
        priority,
        ...(paymentTerms && { payment_terms: paymentTerms }),
        ...(freightTerms && { freight_terms: freightTerms }),
        ...(notes && { notes }),
      };

      const response = await purchaseOrderAPI.createOrder(orderData);
      const orderId = response.data.id;

      // Navigate to the created order
      navigate(`/purchasing/orders/${orderId}`);
    } catch (err: any) {
      console.error('Error creating purchase order:', err);
      setError(extractErrorMessage(err, '') || err.response?.data?.error || err.message || 'Failed to create purchase order');
      setSubmitting(false);
    }
  };

  const formatCurrency = (value: number | string) => {
    const num = typeof value === 'string' ? parseFloat(value) : value;
    if (isNaN(num)) return '—';
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
    }).format(num);
  };

  /**
   * A line figure, or an em dash where no price is on file.
   *
   * Explicit about the absence rather than leaning on `formatCurrency`'s NaN
   * branch: `parseFloat(null)` happening to be NaN is what these cells relied
   * on, and it stops being true the moment someone parses the field
   * differently. `null` is "we do not know", `0` is a vendor that charges
   * nothing, and the two must not look alike (op-9m2v).
   */
  const lineMoney = (amount: number | null): string =>
    amount === null ? '—' : formatCurrency(amount);

  if (loading) {
    return (
      <div className="po-form-page">
        <div className="loading">Loading reorder data...</div>
      </div>
    );
  }

  return (
    <div className="po-form-page">
      <header className="po-form-header">
        <div>
          <h1>Create Purchase Order</h1>
          <p className="po-form-subtitle">
            Select a supplier to auto-populate items needing reorder
          </p>
        </div>
      </header>

      {error && (
        <div className="error-message">
          <p>{error}</p>
          <button onClick={() => setError(null)}>Dismiss</button>
        </div>
      )}

      <form onSubmit={handleSubmit}>
        {/* Supplier Selection */}
        <section className="form-section supplier-section">
          <h2>1. Select Supplier</h2>
          {suppliers.length === 0 ? (
            <p className="no-data">No suppliers with items needing reorder.</p>
          ) : (
            <div className="supplier-cards">
              {suppliers.map((supplier) => (
                <button
                  key={supplier.id}
                  type="button"
                  className={`supplier-card ${selectedSupplierId === supplier.id ? 'selected' : ''}`}
                  onClick={() => setSelectedSupplierId(supplier.id)}
                >
                  <div className="supplier-card-header">
                    <h3>{supplier.name}</h3>
                    <span className="supplier-type">{supplier.supplier_type}</span>
                  </div>
                  <div className="supplier-card-stats">
                    <div className="stat">
                      <span className="stat-value">{supplier.total_items}</span>
                      <span className="stat-label">items to reorder</span>
                    </div>
                    <div className="stat">
                      <span className="stat-value">{supplier.assets.length}</span>
                      <span className="stat-label">assets available</span>
                    </div>
                    <div className="stat">
                      {/* A "+" marks a total the server could only compute
                          part of: `unpriced_item_count` lines carry no price
                          on file, and base summed each of them as $0.00
                          (op-9m2v). */}
                      <span className="stat-value">
                        {formatCurrency(supplier.estimated_total)}
                        {(supplier.unpriced_item_count ?? 0) > 0 ? ' +' : ''}
                      </span>
                      <span className="stat-label">
                        {(supplier.unpriced_item_count ?? 0) > 0
                          ? `estimated total (${supplier.unpriced_item_count} unpriced)`
                          : 'estimated total'}
                      </span>
                    </div>
                    <div className="stat">
                      <span className="stat-value">{Math.round(supplier.avg_lead_time)} days</span>
                      <span className="stat-label">avg lead time</span>
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}

          {/* Optional purchase/pricing agreement (op-yoos). Only rendered when
              the chosen supplier actually has active agreements on file. */}
          {selectedSupplier && agreements.length > 0 && (
            <div className="supplier-agreement-picker">
              <label htmlFor="supplier-agreement">Purchase / pricing agreement (optional)</label>
              <select
                id="supplier-agreement"
                value={selectedAgreementId ?? ''}
                onChange={(e) =>
                  setSelectedAgreementId(e.target.value ? Number(e.target.value) : null)
                }
              >
                <option value="">No agreement</option>
                {agreements.map((agreement) => (
                  <option key={agreement.id} value={agreement.id}>
                    {agreement.name}
                  </option>
                ))}
              </select>
              {selectedAgreement?.notes && (
                <p className="agreement-notes">{selectedAgreement.notes}</p>
              )}
            </div>
          )}
        </section>

        {selectedSupplier && (
          <>
            {/* Kits (op-8n0) — deliberately placed BEFORE Inventory Items.
                Scrolling five cartridge rows first means the operator has
                already decided before learning the bundle exists. No test
                asserts section headings, so the renumber below is free. */}
            <section className="form-section kits-section" data-testid="po-kits-section">
              <h2>2. Kits ({selectedKits.length})</h2>
              {selectedKits.length === 0 ? (
                <p className="no-data">No kits from this supplier.</p>
              ) : (
                <div className="items-table-container">
                  <table className="items-table" data-testid="po-kits-table">
                    <thead>
                      <tr>
                        <th className="col-checkbox" />
                        <th>Kit</th>
                        <th>Supplier SKU</th>
                        <th>Kits</th>
                        <th>Unit cost</th>
                        <th>Line total</th>
                        <th>Contents</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedKits.map((kit) => {
                        const unitCost = effectiveUnitCost(kit);
                        const lineTotal = unitCost === null ? null : unitCost * kit.quantity;
                        const units = kit.components.reduce(
                          (sum, component) => sum + component.quantity_per_kit * kit.quantity,
                          0
                        );
                        return (
                          <React.Fragment key={kit.id}>
                            <tr data-testid={`po-kit-row-${kit.id}`}>
                              <td className="col-checkbox">
                                <input
                                  type="checkbox"
                                  checked={kit.selected}
                                  onChange={() => toggleKit(kit.id)}
                                  aria-label={`Order ${kit.name}`}
                                  data-testid={`po-kit-checkbox-${kit.id}`}
                                />
                              </td>
                              <td>
                                <div className="item-name">{kit.name}</div>
                                {/* Live summary, recomputed as the quantity is
                                    typed — the operator sees the consequence
                                    before committing. */}
                                <div
                                  className="item-meta"
                                  data-testid={`po-kit-summary-${kit.id}`}
                                >
                                  {kit.quantity} {kit.quantity === 1 ? 'kit' : 'kits'} &rarr;{' '}
                                  {units} units across {kit.components.length} items
                                </div>
                              </td>
                              <td>{kit.supplier_sku || '—'}</td>
                              <td>
                                {/* Counts KITS, never component units. There is
                                    deliberately no cases triangle: a kit IS the
                                    package. */}
                                <input
                                  type="number"
                                  min={1}
                                  value={kit.quantity}
                                  onChange={(e) =>
                                    updateKitQuantity(kit.id, parseInt(e.target.value, 10) || 1)
                                  }
                                  aria-label={`Number of ${kit.name} kits`}
                                  data-testid={`po-kit-quantity-${kit.id}`}
                                />
                              </td>
                              <td>
                                <input
                                  type="number"
                                  step="0.01"
                                  min="0"
                                  // `?? ''` after the null: a kit with no
                                  // price on file gets an EMPTY box the
                                  // operator must fill, not a "0" they can
                                  // accept by reflex (op-9m2v).
                                  value={kit.unit_cost_override ?? kit.unit_cost ?? ''}
                                  placeholder={kit.unit_cost === null ? 'no price on file' : ''}
                                  onChange={(e) =>
                                    setSelectedKits((prev) =>
                                      prev.map((row) =>
                                        row.id === kit.id
                                          ? { ...row, unit_cost_override: e.target.value }
                                          : row
                                      )
                                    )
                                  }
                                  aria-label={`Unit cost for ${kit.name}`}
                                />
                              </td>
                              <td data-testid={`po-kit-total-${kit.id}`}>
                                {lineMoney(lineTotal)}
                              </td>
                              <td>
                                {/* Expandable, NOT a tooltip: a 5x5 table is
                                    unreachable on touch and invisible to screen
                                    readers as tabular data. */}
                                <button
                                  type="button"
                                  className="link-button"
                                  onClick={() => toggleKitExpanded(kit.id)}
                                  aria-expanded={kit.expanded}
                                  data-testid={`po-kit-expand-${kit.id}`}
                                >
                                  {kit.expanded ? 'Hide' : `Show ${kit.components.length}`}
                                </button>
                              </td>
                            </tr>
                            {kit.expanded && (
                              <tr data-testid={`po-kit-breakdown-${kit.id}`}>
                                <td colSpan={7}>
                                  <table className="items-table nested-table">
                                    <thead>
                                      <tr>
                                        <th>Component</th>
                                        <th>Per kit</th>
                                        <th>You get</th>
                                      </tr>
                                    </thead>
                                    <tbody>
                                      {kit.components.map((component) => (
                                        <tr key={component.id}>
                                          <td>
                                            {component.name}
                                            {component.is_low && (
                                              <span className="badge badge-warning"> Low</span>
                                            )}
                                          </td>
                                          <td>{component.quantity_per_kit}</td>
                                          <td
                                            data-testid={`po-kit-yousget-${kit.id}-${component.id}`}
                                          >
                                            +{component.quantity_per_kit * kit.quantity}
                                          </td>
                                        </tr>
                                      ))}
                                    </tbody>
                                  </table>
                                </td>
                              </tr>
                            )}
                          </React.Fragment>
                        );
                      })}
                    </tbody>
                    <tfoot>
                      <tr>
                        <td colSpan={5} className="subtotal-label">
                          Kits subtotal
                        </td>
                        <td className="subtotal-value" data-testid="po-kits-subtotal">
                          {formatCurrency(kitsTotal)}
                        </td>
                        <td />
                      </tr>
                    </tfoot>
                  </table>
                </div>
              )}
            </section>

            {/* Inventory Items */}
            <section className="form-section items-section">
              <h2>3. Inventory Items ({selectedItems.length})</h2>
              {/* Double-order guard (op-8n0). Derived, never stored. */}
              {conflictingItems.length > 0 && (
                <div className="alert alert-warning" data-testid="po-kit-conflict-banner">
                  <p>
                    <strong>Already in a selected kit:</strong>{' '}
                    {conflictingItems.map((item) => item.item_name).join(', ')}. Ordering both
                    buys {conflictingItems.length === 1 ? 'it' : 'them'} twice.
                  </p>
                  <button
                    type="button"
                    onClick={deselectConflictingItems}
                    data-testid="po-kit-conflict-deselect"
                  >
                    Deselect {conflictingItems.length}{' '}
                    {conflictingItems.length === 1 ? 'item' : 'items'}
                  </button>
                </div>
              )}
              {selectedItems.some((it) => it.quantity_per_package > 1) && (
                <p className="case-help-text">
                  When ordering items by the case, enter the number of cases. We compute units
                  automatically from the supplier&apos;s case size.
                </p>
              )}
              {selectedItems.length === 0 ? (
                <p className="no-data">No low-stock items from this supplier.</p>
              ) : (
                <div className="items-table-container">
                  <table className="items-table">
                    <thead>
                      <tr>
                        <th className="col-checkbox">
                          <input
                            type="checkbox"
                            checked={selectedItems.every((item) => item.selected)}
                            onChange={(e) => {
                              const checked = e.target.checked;
                              setSelectedItems((prev) =>
                                prev.map((item) => ({ ...item, selected: checked }))
                              );
                            }}
                          />
                        </th>
                        <th>Item</th>
                        <th className="col-stock">Stock</th>
                        <th className="col-quantity">Quantity</th>
                        <th className="col-cost">Unit Cost</th>
                        <th className="col-lead">Lead Time</th>
                        <th className="col-shipment">Expected Shipment</th>
                        <th className="col-total">Line Total</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedItems.map((item) => (
                        <tr
                          key={item.item_supplier_id}
                          className={item.selected ? 'row-selected' : 'row-unselected'}
                        >
                          <td className="col-checkbox">
                            <input
                              type="checkbox"
                              checked={item.selected}
                              onChange={() => toggleItemSelection(item.item_supplier_id)}
                              // Named so the row is addressable — the kit
                              // conflict banner (op-8n0) talks about these rows
                              // by item name, and an unlabelled checkbox left
                              // that unreachable to a screen reader.
                              aria-label={`Select ${item.item_name}`}
                              data-testid={`po-item-checkbox-${item.item_id}`}
                            />
                          </td>
                          <td>
                            <div className="item-info">
                              <div className="item-name-row">
                                <span className="item-name">{item.item_name}</span>
                                {/* Permanent "in kit" chip (op-8n0): stays
                                    visible even when the kit is UNCHECKED, so
                                    the overlap is discoverable before the
                                    conflict banner ever fires. */}
                                {componentIdsInAnyKit.has(item.item_id) && (
                                  <span
                                    className="badge badge-info"
                                    title="This item is also inside a kit from this supplier"
                                    data-testid={`po-item-in-kit-${item.item_id}`}
                                  >
                                    in kit
                                  </span>
                                )}
                                {item.has_active_reorder_request && (
                                  <span className="reorder-badge" title="This item has an active reorder request">
                                    Reorder Request
                                  </span>
                                )}
                              </div>
                              <span className="item-sku">{item.supplier_sku}</span>
                            </div>
                          </td>
                          <td className="col-stock">
                            <span className={item.current_stock <= 0 ? 'stock-critical' : 'stock-low'}>
                              {item.current_stock} / {item.minimum_stock}
                            </span>
                          </td>
                          <td className="col-quantity">
                            {item.quantity_per_package > 1 ? (
                              <div className="case-qty-group">
                                <label className="case-qty-field">
                                  <span className="case-qty-label">Cases</span>
                                  <input
                                    type="number"
                                    min="1"
                                    value={item.cases}
                                    onChange={(e) =>
                                      updateItemCases(item.item_supplier_id, parseInt(e.target.value, 10) || 1)
                                    }
                                    disabled={!item.selected}
                                    aria-label={`Cases for ${item.item_name}`}
                                  />
                                </label>
                                <label className="case-qty-field">
                                  <span className="case-qty-label">Units</span>
                                  <input
                                    type="number"
                                    min="1"
                                    value={item.quantity}
                                    onChange={(e) =>
                                      updateItemQuantity(item.item_supplier_id, parseInt(e.target.value, 10) || 1)
                                    }
                                    disabled={!item.selected}
                                    aria-label={`Units for ${item.item_name}`}
                                  />
                                </label>
                                <div
                                  className="case-qty-summary"
                                  data-testid={`case-summary-${item.item_supplier_id}`}
                                >
                                  {item.cases} cases × {item.quantity_per_package} units/case ={' '}
                                  {item.quantity} units @{' '}
                                  {lineMoney(effectiveUnitCost(item))}
                                  /unit ={' '}
                                  {lineMoney(
                                    effectiveUnitCost(item) === null
                                      ? null
                                      : (effectiveUnitCost(item) as number) * item.quantity
                                  )}
                                </div>
                              </div>
                            ) : (
                              <input
                                type="number"
                                min="1"
                                value={item.quantity}
                                onChange={(e) =>
                                  updateItemQuantity(item.item_supplier_id, parseInt(e.target.value, 10) || 1)
                                }
                                disabled={!item.selected}
                                aria-label={`Quantity for ${item.item_name}`}
                              />
                            )}
                          </td>
                          <td className="col-cost">
                            {item.quantity_per_package > 1 ? (
                              <div className="case-cost-group">
                                {/*
                                  step="any" (not 0.01) on both inputs: a case cost that
                                  doesn't divide evenly by qpp derives a sub-cent per-unit
                                  value (kept at full precision on purpose). step="0.01"
                                  would mark that field :invalid and block form submission.
                                */}
                                <label className="case-cost-field">
                                  <span className="case-cost-label">Cost / unit</span>
                                  <input
                                    type="number"
                                    min="0"
                                    step="any"
                                    placeholder={item.unit_cost ?? 'no price on file'}
                                    value={item.unit_cost_override || ''}
                                    onChange={(e) =>
                                      updateItemUnitCost(item.item_supplier_id, e.target.value)
                                    }
                                    disabled={!item.selected}
                                    className="input-cost"
                                    aria-label={`Cost per unit for ${item.item_name}`}
                                    title="Cost per individual unit (used for inventory valuation; line total = unit cost × units ordered). Editing this updates cost per case."
                                  />
                                </label>
                                <label className="case-cost-field">
                                  <span className="case-cost-label">Cost / case</span>
                                  <input
                                    type="number"
                                    min="0"
                                    step="any"
                                    placeholder={caseCostPlaceholderFor(item)}
                                    value={item.case_cost_override || ''}
                                    onChange={(e) =>
                                      updateItemCaseCost(item.item_supplier_id, e.target.value)
                                    }
                                    disabled={!item.selected}
                                    className="input-cost"
                                    aria-label={`Cost per case for ${item.item_name}`}
                                    title="Cost per case (= unit cost × units per case). Editing this updates cost per unit."
                                  />
                                </label>
                              </div>
                            ) : (
                              <input
                                type="number"
                                min="0"
                                step="0.01"
                                placeholder={item.unit_cost ?? 'no price on file'}
                                value={item.unit_cost_override || ''}
                                onChange={(e) => updateItemUnitCost(item.item_supplier_id, e.target.value)}
                                disabled={!item.selected}
                                className="input-cost"
                                aria-label={`Unit cost for ${item.item_name}`}
                                title="Cost per individual unit (we use this for inventory valuation; total = unit cost × units ordered)."
                              />
                            )}
                          </td>
                          <td className="col-lead">{item.lead_time_days} days</td>
                          <td className="col-shipment">
                            <input
                              type="date"
                              value={item.expected_shipment_date || ''}
                              onChange={(e) => updateItemShipmentDate(item.item_supplier_id, e.target.value)}
                              disabled={!item.selected}
                              className="input-date"
                            />
                          </td>
                          <td className="col-total">
                            {item.selected
                              ? lineMoney(
                                  effectiveUnitCost(item) === null
                                    ? null
                                    : (effectiveUnitCost(item) as number) * item.quantity
                                )
                              : '—'}
                          </td>
                        </tr>
                      ))}
                      {/* Blank line for manual product entry */}
                      <tr className="row-add-product">
                        <td colSpan={2}>
                          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                            <input
                              type="text"
                              placeholder="Search by product name or SKU..."
                              value={productSearchTerm}
                              onChange={(e) => {
                                setProductSearchTerm(e.target.value);
                                setProductBarcode('');
                              }}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') {
                                  e.preventDefault();
                                  handleProductSearch();
                                }
                              }}
                              className="input-search"
                              style={{ flex: 1 }}
                            />
                            <span style={{ margin: '0 4px' }}>or</span>
                            <input
                              type="text"
                              placeholder="Scan barcode..."
                              value={productBarcode}
                              onChange={(e) => {
                                setProductBarcode(e.target.value);
                                setProductSearchTerm('');
                              }}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') {
                                  e.preventDefault();
                                  handleProductSearch('', productBarcode);
                                }
                              }}
                              onBlur={() => {
                                // Auto-search when barcode field loses focus (typical after scanning)
                                if (productBarcode.trim() && selectedSupplierId && !searchingProduct) {
                                  handleProductSearch('', productBarcode);
                                }
                              }}
                              className="input-barcode"
                              style={{ flex: 1 }}
                            />
                            <button
                              type="button"
                              onClick={() => handleProductSearch(productSearchTerm, productBarcode)}
                              disabled={searchingProduct || (!productSearchTerm.trim() && !productBarcode.trim())}
                              className="btn-add-product"
                            >
                              {searchingProduct ? 'Searching...' : 'Add'}
                            </button>
                          </div>
                        </td>
                        <td colSpan={6}></td>
                      </tr>
                    </tbody>
                    <tfoot>
                      <tr>
                        <td colSpan={7} className="subtotal-label">
                          Items Subtotal ({selectedItemCount} items)
                        </td>
                        <td className="subtotal-value">{formatCurrency(itemsTotal)}</td>
                      </tr>
                    </tfoot>
                  </table>
                </div>
              )}
            </section>

            {/* Assets Section */}
            <section className="form-section assets-section">
              <h2>3. Assets ({selectedSupplier.assets.length})</h2>
              {selectedSupplier.assets.length === 0 ? (
                <p className="no-data">No assets from this manufacturer.</p>
              ) : (
                <div className="items-table-container">
                  <table className="items-table">
                    <thead>
                      <tr>
                        <th className="col-checkbox">Include</th>
                        <th>Asset</th>
                        <th className="col-quantity">Quantity</th>
                        <th className="col-cost">Unit Cost</th>
                        <th className="col-total">Line Total</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedAssets.map((asset) => (
                        <tr
                          key={asset.id}
                          className={asset.selected ? 'row-selected' : 'row-unselected'}
                        >
                          <td className="col-checkbox">
                            <input
                              type="checkbox"
                              checked={asset.selected}
                              onChange={() => toggleAssetSelection(asset.id)}
                            />
                          </td>
                          <td>
                            <div className="item-info">
                              <span className="item-name">{asset.name}</span>
                              <span className="item-sku">{asset.asset_tag || asset.serial_number}</span>
                            </div>
                          </td>
                          <td className="col-quantity">
                            <input
                              type="number"
                              min="1"
                              value={asset.quantity}
                              onChange={(e) =>
                                updateAsset(asset.id, {
                                  quantity: Math.max(1, parseInt(e.target.value, 10) || 1),
                                })
                              }
                              disabled={!asset.selected}
                            />
                          </td>
                          <td className="col-cost">
                            <input
                              type="number"
                              min="0"
                              step="0.01"
                              placeholder="0.00"
                              value={asset.unit_cost}
                              onChange={(e) => updateAsset(asset.id, { unit_cost: e.target.value })}
                              disabled={!asset.selected}
                              className={asset.selected && !asset.unit_cost ? 'input-error' : ''}
                            />
                          </td>
                          <td className="col-total">
                            {asset.selected && asset.unit_cost
                              ? formatCurrency(parseFloat(asset.unit_cost) * asset.quantity)
                              : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                    <tfoot>
                      <tr>
                        <td colSpan={4} className="subtotal-label">
                          Assets Subtotal ({selectedAssetCount} assets)
                        </td>
                        <td className="subtotal-value">{formatCurrency(assetsTotal)}</td>
                      </tr>
                    </tfoot>
                  </table>
                </div>
              )}
            </section>

            {/* Freeform Items */}
            <section className="form-section freeform-section">
              <div className="section-header">
                <h2>4. Additional Items</h2>
                <button type="button" className="btn-add" onClick={addFreeformItem}>
                  + Add Item
                </button>
              </div>
              {freeformItems.length === 0 ? (
                <p className="no-data">No additional items. Click "Add Item" to add freeform line items.</p>
              ) : (
                <div className="items-table-container">
                  <table className="items-table">
                    <thead>
                      <tr>
                        <th>Description</th>
                        <th className="col-quantity">Quantity</th>
                        <th className="col-cost">Unit Cost</th>
                        <th className="col-total">Line Total</th>
                        <th className="col-actions">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {freeformItems.map((item) => (
                        <tr key={item.id} className="row-selected">
                          <td>
                            <input
                              type="text"
                              placeholder="Item description..."
                              value={item.description}
                              onChange={(e) =>
                                updateFreeformItem(item.id, { description: e.target.value })
                              }
                              className="input-description"
                            />
                          </td>
                          <td className="col-quantity">
                            <input
                              type="number"
                              min="1"
                              value={item.quantity}
                              onChange={(e) =>
                                updateFreeformItem(item.id, {
                                  quantity: Math.max(1, parseInt(e.target.value, 10) || 1),
                                })
                              }
                            />
                          </td>
                          <td className="col-cost">
                            <input
                              type="number"
                              min="0"
                              step="0.01"
                              placeholder="0.00"
                              value={item.unit_cost}
                              onChange={(e) =>
                                updateFreeformItem(item.id, { unit_cost: e.target.value })
                              }
                            />
                          </td>
                          <td className="col-total">
                            {item.description && item.unit_cost
                              ? formatCurrency(parseFloat(item.unit_cost) * item.quantity)
                              : '—'}
                          </td>
                          <td className="col-actions">
                            <button
                              type="button"
                              className="btn-remove"
                              onClick={() => removeFreeformItem(item.id)}
                            >
                              Remove
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                    <tfoot>
                      <tr>
                        <td colSpan={3} className="subtotal-label">
                          Additional Items Subtotal ({freeformItemCount} items)
                        </td>
                        <td className="subtotal-value">{formatCurrency(freeformTotal)}</td>
                        <td></td>
                      </tr>
                    </tfoot>
                  </table>
                </div>
              )}
            </section>

            {/* Order Details */}
            <section className="form-section details-section">
              <h2>5. Order Details</h2>
              <div className="details-grid">
                {/* Header terms (op-bwo9). Blank "Date Ordered" means the order
                    is being placed now; fill it in to backdate one entered
                    after the fact. */}
                <div className="form-field">
                  <label htmlFor="order-date">Date Ordered</label>
                  <input
                    id="order-date"
                    type="date"
                    value={orderDate}
                    onChange={(e) => setOrderDate(e.target.value)}
                  />
                  <span className="field-help-text">Leave blank for today.</span>
                </div>
                <div className="form-field">
                  <label htmlFor="expected-delivery">Date Promised (expected delivery)</label>
                  <input
                    id="expected-delivery"
                    type="date"
                    value={expectedDeliveryDate}
                    onChange={(e) => setExpectedDeliveryDate(e.target.value)}
                  />
                </div>
                <div className="form-field">
                  <label htmlFor="order-priority">Priority</label>
                  <select
                    id="order-priority"
                    value={priority}
                    onChange={(e) => setPriority(e.target.value as PurchaseOrderPriority)}
                  >
                    {PO_PRIORITY_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="form-field">
                  <label htmlFor="order-payment-terms">Payment Terms</label>
                  <select
                    id="order-payment-terms"
                    value={paymentTerms}
                    onChange={(e) =>
                      setPaymentTerms(e.target.value as PurchaseOrderPaymentTerms | '')
                    }
                  >
                    <option value="">Not agreed</option>
                    {PO_PAYMENT_TERMS_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="form-field">
                  <label htmlFor="order-freight-terms">Freight Terms</label>
                  <select
                    id="order-freight-terms"
                    value={freightTerms}
                    onChange={(e) =>
                      setFreightTerms(e.target.value as PurchaseOrderFreightTerms | '')
                    }
                  >
                    <option value="">Not agreed</option>
                    {PO_FREIGHT_TERMS_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="form-field notes-field">
                  <label htmlFor="notes">Notes</label>
                  <textarea
                    id="notes"
                    placeholder="Optional notes for this order..."
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    rows={3}
                  />
                </div>
              </div>
            </section>

            {/* Associations (op-shb9). Order-level only — individual lines are
                associated from the order's detail page once it exists. */}
            <section className="form-section associations-section">
              <h2>6. Associations</h2>
              <p className="associations-help-text">
                Optional. Records who this order was placed for; it does not change stock,
                pricing, or what a committee is billed.
              </p>
              <div className="associations-grid">
                <div className="form-field">
                  <label htmlFor="order-work-order">Work Order</label>
                  <select
                    id="order-work-order"
                    value={selectedWorkOrderId}
                    onChange={(e) => setSelectedWorkOrderId(e.target.value)}
                  >
                    <option value="">No work order</option>
                    {workOrders.map((workOrder) => (
                      <option key={workOrder.id} value={workOrder.id}>
                        {workOrderOptionLabel(workOrder)}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="form-field">
                  <label htmlFor="order-committee">Committee</label>
                  <select
                    id="order-committee"
                    value={selectedCommitteeId ?? ''}
                    onChange={(e) =>
                      setSelectedCommitteeId(e.target.value ? Number(e.target.value) : null)
                    }
                  >
                    <option value="">No committee</option>
                    {sigs.map((sig) => (
                      <option key={sig.id} value={sig.id}>
                        {sig.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            </section>

            {/* Order Summary */}
            <section className="form-section summary-section">
              <div className="order-summary">
                <div className="summary-row">
                  <span>Kits ({selectedKitCount})</span>
                  <span data-testid="po-summary-kits-total">{formatCurrency(kitsTotal)}</span>
                </div>
                <div className="summary-row">
                  <span>Inventory Items ({selectedItemCount})</span>
                  <span>{formatCurrency(itemsTotal)}</span>
                </div>
                <div className="summary-row">
                  <span>Assets ({selectedAssetCount})</span>
                  <span>{formatCurrency(assetsTotal)}</span>
                </div>
                <div className="summary-row">
                  <span>Additional Items ({freeformItemCount})</span>
                  <span>{formatCurrency(freeformTotal)}</span>
                </div>
                <div className="summary-row summary-total">
                  <span>Grand Total ({totalLineItems} line items)</span>
                  <span>
                    {formatCurrency(grandTotal)}
                    {unpricedLines.length > 0 ? ' +' : ''}
                  </span>
                </div>
                {/* The order cannot be created while a line carries no price,
                    so the block names each one and what to do about it rather
                    than leaving a disabled button unexplained (op-9m2v). The
                    server refuses the same lines; this just gets there first. */}
                {unpricedLines.length > 0 && (
                  <div className="summary-row summary-warning" data-testid="po-unpriced-warning">
                    <span>
                      {unpricedLines.length} line
                      {unpricedLines.length === 1 ? '' : 's'} with no price on file
                    </span>
                    <span>
                      {unpricedLines.map((line) => line.name).join(', ')} — enter a unit cost
                      above, or record one on the supplier link, before creating this order.
                    </span>
                  </div>
                )}
                {/* What the order will look like the moment it is created
                    (op-uc0o): every new order starts as a draft, and the
                    payment follows the terms above and the running total. */}
                <div className="summary-row">
                  <span>Status</span>
                  <span data-testid="live-status">Draft</span>
                </div>
                <div className="summary-row">
                  <span>Payment schedule</span>
                  <span data-testid="live-payment-schedule">
                    {paymentScheduleSummary(livePaymentSchedule)}
                  </span>
                </div>
              </div>
            </section>

            {/* Submit */}
            <div className="form-actions">
              <button
                type="button"
                className="btn-cancel"
                onClick={() => navigate('/purchasing/orders')}
              >
                Cancel
              </button>
              <button type="submit" className="btn-submit" disabled={!canSubmit}>
                {submitting ? 'Creating...' : 'Create Purchase Order'}
              </button>
            </div>
          </>
        )}
      </form>
    </div>
  );
};

export default PurchaseOrderFormPage;

