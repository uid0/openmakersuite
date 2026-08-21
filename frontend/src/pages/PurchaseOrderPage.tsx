/**
 * Purchase Order Management Page
 * View and manage purchase orders, including setting expected shipment dates for line items.
 *
 * The `mark-delivered` flow patches the page from the API response — see
 * docs/REACTIVE_MUTATIONS.md. The initial "Loading purchase order…"
 * placeholder is reserved for the first fetch and route changes; a
 * successful mark-delivered submit must never flip the page back into
 * that state.
 */
import { Button, Group, Paper, Text } from '@mantine/core';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import {
  AddPurchaseOrderLinePayload,
  OrderingAdapter,
  OrderPadExport,
  purchaseOrderAPI,
  PurchaseOrderFreightTerms,
  PurchaseOrderLineCandidate,
  PurchaseOrderPaymentSchedule,
  PurchaseOrderPaymentTerms,
  PurchaseOrderPriority,
  serializedComponentsAPI,
  SerializedTrackingMode,
  sigAPI,
  workOrderAPI,
} from '../services/api';
import { SIG, WorkOrder } from '../types';
import '../styles/PurchaseOrderPage.css';
import {
  OwningGroupIdentity,
  workOrderDetailsLabel,
  WorkOrderIdentity,
  workOrderOptionLabel,
} from '../utils/associations';
import { formatDateOnly, formatYmd, utcYmd, ymdToUtcDateTime } from '../utils/dates';
import { confirmAction, promptInput, showError, showSuccess } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';
import {
  freightTermsLabel,
  paymentScheduleSummary,
  paymentTermsLabel,
  PO_FREIGHT_TERMS_OPTIONS,
  PO_PAYMENT_TERMS_OPTIONS,
  PO_PRIORITY_OPTIONS,
  priorityLabel,
} from '../utils/purchaseOrderTerms';
import { parseSerialNumbers } from '../utils/serializedComponents';

interface PurchaseOrderItem {
  id: string;
  item_type: 'inventory_item' | 'asset' | 'freeform' | null;
  description: string | null;
  item_details: {
    id: string;
    name: string;
    sku: string;
    is_serialized?: boolean;
    serial_tracking_mode?: SerializedTrackingMode;
  } | null;
  asset_details: {
    id: string;
    name: string;
    asset_tag: string;
    location_name: string | null;
  } | null;
  // Kit lines (op-8n0). Both come from the LINE payload; the breakdown is
  // never fetched live from the kit, because what matters when receiving is
  // what this line will credit.
  is_kit_line?: boolean;
  kit_components?: Array<{
    component: string;
    component_name: string;
    component_sku: string;
    quantity_per_kit: number;
    quantity: number;
  }> | null;
  quantity_ordered: number;
  quantity_received: number;
  quantity_pending: number;
  is_fully_received: boolean;
  unit_cost_ordered: string;
  unit_cost_actual: string | null;
  estimated_cost: string;
  actual_cost: string | null;
  expected_shipment_date: string | null;
  notes: string;
  is_voided: boolean;
  voided_at: string | null;
  void_reason: string;
  // Who this line was bought for (op-bu80 / op-shb9). Both editable here.
  work_order: string | null;
  work_order_details: WorkOrderIdentity | null;
  owning_group: number | null;
  owning_group_details: OwningGroupIdentity | null;
}

interface PurchaseOrderAttachment {
  id: number;
  file: string;
  file_url: string | null;
  file_name: string | null;
  description: string;
  uploaded_by: number | null;
  uploaded_by_name: string | null;
  uploaded_at: string;
}

interface PurchaseOrder {
  id: string;
  po_number: string;
  supplier_details: string;
  // Selects the adapter-aware order-pad affordances (op-svpq): Amazon "Open
  // cart" vs HD Supply / generic download+copy. Read-only, from the supplier.
  supplier_ordering_adapter: OrderingAdapter | null;
  // The purchase/pricing agreement this order was placed under (op-yoos).
  // Optional — most orders are placed at list price.
  supplier_agreement: number | null;
  supplier_agreement_details: { id: number; name: string } | null;
  // Who the whole order was placed for (op-shb9). Attribution only — the
  // material bridge and the receiving ledger read the lines, not these.
  work_order: string | null;
  work_order_details: WorkOrderIdentity | null;
  owning_group: number | null;
  owning_group_details: OwningGroupIdentity | null;
  status: string;
  status_label: string;
  // Header terms (op-bwo9), all editable here. `order_date` is a datetime that
  // carries a business *day* — the server derives `payment_schedule` from its
  // UTC date — so it is read and written as a day (see `utcYmd`).
  order_date: string;
  priority: PurchaseOrderPriority;
  payment_terms: PurchaseOrderPaymentTerms | '';
  freight_terms: PurchaseOrderFreightTerms | '';
  // Derived, read-only: the single payment these terms imply.
  payment_schedule: PurchaseOrderPaymentSchedule | null;
  expected_delivery_date: string | null;
  supplier_order_number: string;
  sales_order_number: string;
  attachments: PurchaseOrderAttachment[];
  items: PurchaseOrderItem[];
  estimated_total: string;
  voided_at: string | null;
  voided_by_username: string | null;
  void_reason: string;
}

/**
 * What KIND of thing a line orders (op-49th) — deliberately distinct from
 * op-shb9's "Ordered For" column, which is the job/committee the line was
 * bought *for*.
 *
 * Caveat: a line raised from a reorder request is indistinguishable from a
 * hand-added inventory line on the wire (the serializer carries no reorder FK),
 * so it reads as "Inventory item" here. A real "Reorder" badge needs a backend
 * `reorder_request` field on the line first.
 */
const ITEM_TYPE_LABELS: Record<NonNullable<PurchaseOrderItem['item_type']>, string> = {
  inventory_item: 'Inventory item',
  asset: 'Asset',
  freeform: 'Freeform',
};

const getItemTypeLabel = (item: PurchaseOrderItem): string =>
  (item.item_type && ITEM_TYPE_LABELS[item.item_type]) || '—';

const getItemNameAndSku = (item: PurchaseOrderItem): { itemName: string; itemSku: string } => {
  if (item.item_type === 'asset') {
    return {
      itemName: item.asset_details?.name || 'Unknown Asset',
      itemSku: item.asset_details?.asset_tag || '—',
    };
  }
  if (item.item_type === 'freeform') {
    return { itemName: item.description || 'Unknown Item', itemSku: '—' };
  }
  return {
    itemName: item.item_details?.name || 'Unknown Item',
    itemSku: item.item_details?.sku || '—',
  };
};

interface SelectOption {
  value: string;
  label: string;
}

/**
 * Options for an association picker, with the currently-attached target kept
 * selectable even when it is not in the fetched list (op-shb9) — the pickers
 * offer open/in-progress jobs and the viewer's own committees, so an order
 * tagged with a finished job or another committee's SIG would otherwise show
 * blank and be silently cleared by an unrelated edit.
 */
const withCurrentOption = (
  available: SelectOption[],
  currentValue: string,
  currentLabel: string,
): SelectOption[] =>
  currentValue && !available.some((option) => option.value === currentValue)
    ? [{ value: currentValue, label: currentLabel }, ...available]
    : available;

/** The work-order + committee picker pair, used at order and line level. */
const AssociationPickers: React.FC<{
  idPrefix: string;
  workOrderOptions: SelectOption[];
  committeeOptions: SelectOption[];
  workOrderValue: string;
  committeeValue: string;
  onWorkOrderChange: (value: string) => void;
  onCommitteeChange: (value: string) => void;
  disabled?: boolean;
}> = ({
  idPrefix,
  workOrderOptions,
  committeeOptions,
  workOrderValue,
  committeeValue,
  onWorkOrderChange,
  onCommitteeChange,
  disabled,
}) => (
  <>
    <label htmlFor={`${idPrefix}-work-order`}>
      Work Order
      <select
        id={`${idPrefix}-work-order`}
        value={workOrderValue}
        onChange={(e) => onWorkOrderChange(e.target.value)}
        disabled={disabled}
      >
        <option value="">No work order</option>
        {workOrderOptions.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
    <label htmlFor={`${idPrefix}-committee`}>
      Committee
      <select
        id={`${idPrefix}-committee`}
        value={committeeValue}
        onChange={(e) => onCommitteeChange(e.target.value)}
        disabled={disabled}
      >
        <option value="">No committee</option>
        {committeeOptions.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  </>
);

const PurchaseOrderPage: React.FC = () => {
  const { orderId } = useParams<{ orderId: string }>();
  const [order, setOrder] = useState<PurchaseOrder | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingItemId, setEditingItemId] = useState<string | null>(null);
  const [shipmentDate, setShipmentDate] = useState<string>('');
  const [editingCostItemId, setEditingCostItemId] = useState<string | null>(null);
  const [lineCost, setLineCost] = useState<string>('');
  const [saving, setSaving] = useState(false);
  // Dedicated in-flight flag for the draft→sent / sent→confirmed hero
  // transitions so their disabled state is independent of the line-item
  // `saving` flag used elsewhere on the page.
  const [transitioning, setTransitioning] = useState(false);
  const [voidingItemId, setVoidingItemId] = useState<string | null>(null);
  const [voidReason, setVoidReason] = useState<string>('');
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isStaff, setIsStaff] = useState(false);
  const [markingDelivered, setMarkingDelivered] = useState(false);
  const [deliveryDate, setDeliveryDate] = useState<string>('');
  const [deliveryTracking, setDeliveryTracking] = useState<string>('');
  const [deliveryCarrier, setDeliveryCarrier] = useState<string>('');
  const [receivingItems, setReceivingItems] = useState(false);
  const [receiveQuantities, setReceiveQuantities] = useState<Record<string, string>>({});
  // Per-serialized-line captured serial numbers (one per line / comma-separated),
  // keyed by purchase-order line id.
  const [serialInputs, setSerialInputs] = useState<Record<string, string>>({});
  const [receiveDeliveryDate, setReceiveDeliveryDate] = useState<string>('');
  const [receiveNotes, setReceiveNotes] = useState<string>('');
  const [editingMetadata, setEditingMetadata] = useState(false);
  const [metadataSupplierOrderNumber, setMetadataSupplierOrderNumber] = useState('');
  const [metadataSalesOrderNumber, setMetadataSalesOrderNumber] = useState('');
  const [metadataExpectedDelivery, setMetadataExpectedDelivery] = useState('');
  // Header terms (op-bwo9), edited alongside the rest of the order details.
  // `metadataOrderDate` is a 'YYYY-MM-DD' day; empty terms mean "not agreed".
  const [metadataOrderDate, setMetadataOrderDate] = useState('');
  const [metadataPriority, setMetadataPriority] = useState<PurchaseOrderPriority>('normal');
  const [metadataPaymentTerms, setMetadataPaymentTerms] = useState<PurchaseOrderPaymentTerms | ''>(
    '',
  );
  const [metadataFreightTerms, setMetadataFreightTerms] = useState<PurchaseOrderFreightTerms | ''>(
    '',
  );
  // Order-level associations (op-shb9), edited alongside the other order
  // metadata. Empty string means "no association" in both pickers.
  const [metadataWorkOrder, setMetadataWorkOrder] = useState('');
  const [metadataCommittee, setMetadataCommittee] = useState('');
  // Per-line associations. `editingAssociationItemId` is the line whose cell is
  // currently a pair of pickers; the two draft values back those pickers.
  const [editingAssociationItemId, setEditingAssociationItemId] = useState<string | null>(null);
  const [lineWorkOrder, setLineWorkOrder] = useState('');
  const [lineCommittee, setLineCommittee] = useState('');
  // Options for every association picker on this page.
  const [workOrders, setWorkOrders] = useState<WorkOrder[]>([]);
  const [sigs, setSigs] = useState<SIG[]>([]);
  const [attachmentDescription, setAttachmentDescription] = useState('');
  const [attachmentFile, setAttachmentFile] = useState<File | null>(null);
  const [uploadingAttachment, setUploadingAttachment] = useState(false);
  // Order export (adapter-aware, op-svpq) — last-fetched payload drives the
  // missing-/invalid-SKU warnings and the Amazon multi-cart panel;
  // `exportingOrderPad` disables the buttons while a fetch is in flight.
  const [orderPad, setOrderPad] = useState<OrderPadExport | null>(null);
  const [exportingOrderPad, setExportingOrderPad] = useState(false);
  // Add-a-line entry (oms-po-add-item). One field takes whatever the operator
  // types or the scanner emits — item name, item SKU, package or unit barcode,
  // or the vendor's SKU — and Enter submits it, so a scan (a fast burst of
  // characters ending in Enter) completes the add without touching the mouse.
  // `addLineCandidates` is populated only when the server refuses to guess
  // between several matches; `addLineNotice` reports what actually matched.
  const [addLineIdentifier, setAddLineIdentifier] = useState('');
  const [addingLine, setAddingLine] = useState(false);
  const [addLineError, setAddLineError] = useState<string | null>(null);
  const [addLineCandidates, setAddLineCandidates] = useState<PurchaseOrderLineCandidate[]>([]);
  const [addLineNotice, setAddLineNotice] = useState<string | null>(null);
  // The scanner loop has to stay mouse-free, so the caret goes back into the
  // entry field the moment an add settles — after a scan-and-Enter, and after a
  // click on one of the ambiguity candidates, whose button disappears with the
  // list that held it. Done in an effect rather than in `submitAddLine` so it
  // runs after the re-render that re-enables the controls.
  //
  // `select()`, not a bare `focus()`: a refusal deliberately leaves the typed
  // text in place so the operator can correct it, and a scanner delivers a
  // burst plus an Enter. Appending that burst onto the old text would turn the
  // next scan into a bogus identifier and a guaranteed second refusal, so the
  // text stays visible but the next scan overwrites it.
  const addLineInputRef = useRef<HTMLInputElement>(null);
  const addLineWasInFlight = useRef(false);
  useEffect(() => {
    if (addLineWasInFlight.current && !addingLine) {
      addLineInputRef.current?.focus();
      addLineInputRef.current?.select();
    }
    addLineWasInFlight.current = addingLine;
  }, [addingLine]);

  // First scan of the session needs no mouse either, so the field takes focus
  // as soon as the control exists. `preventScroll` rather than the `autoFocus`
  // attribute: the control sits below the header, details and attachments, so
  // letting the browser scroll it into view would land every draft order —
  // including one opened just to check its supplier or dates — at Line Items.
  const canAddLine = isAuthenticated && order?.status === 'draft';
  useEffect(() => {
    if (canAddLine) {
      addLineInputRef.current?.focus({ preventScroll: true });
    }
  }, [canAddLine]);

  const loadOrder = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await purchaseOrderAPI.getOrder(orderId!);
      setOrder(response.data);
    } catch (err: any) {
      console.error('Error loading purchase order:', err);
      setError(extractErrorMessage(err, 'Failed to load purchase order'));
    } finally {
      setLoading(false);
    }
  }, [orderId]);

  useEffect(() => {
    // Check if user is authenticated
    const token = localStorage.getItem('token');
    setIsAuthenticated(!!token);
    setIsStaff(
      localStorage.getItem('is_staff') === 'true' ||
        localStorage.getItem('is_superuser') === 'true',
    );

    if (orderId) {
      loadOrder();
    }
  }, [orderId, loadOrder]);

  // Association picker options (op-shb9). Only fetched for signed-in users —
  // the page is publicly readable, and an anonymous visitor edits nothing.
  // Neither list is required to read the order, so a failure just empties that
  // picker instead of erroring the page.
  useEffect(() => {
    if (!isAuthenticated) return;

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
  }, [isAuthenticated]);

  // Base option lists for every association picker on the page. The
  // currently-attached target is grafted on per picker (see withCurrentOption).
  const workOrderOptions: SelectOption[] = workOrders.map((workOrder) => ({
    value: workOrder.id,
    label: workOrderOptionLabel(workOrder),
  }));
  const committeeOptions: SelectOption[] = sigs.map((sig) => ({
    value: String(sig.id),
    label: sig.name,
  }));

  const handleEditShipmentDate = (item: PurchaseOrderItem) => {
    setEditingItemId(item.id);
    setShipmentDate(item.expected_shipment_date || '');
  };

  const handleCancelEdit = () => {
    setEditingItemId(null);
    setShipmentDate('');
  };

  const handleEditLineCost = (item: PurchaseOrderItem) => {
    setEditingCostItemId(item.id);
    // Calculate line cost from unit_cost_actual if available, otherwise from estimated_cost
    if (item.unit_cost_actual && item.quantity_received > 0) {
      const calculatedLineCost = parseFloat(item.unit_cost_actual) * item.quantity_received;
      setLineCost(calculatedLineCost.toFixed(2));
    } else if (item.actual_cost) {
      setLineCost(parseFloat(item.actual_cost).toFixed(2));
    } else {
      // Use estimated cost as starting point
      setLineCost(parseFloat(item.estimated_cost).toFixed(2));
    }
  };

  const handleCancelEditCost = () => {
    setEditingCostItemId(null);
    setLineCost('');
  };

  const handleSaveLineCost = async (itemId: string, item: PurchaseOrderItem) => {
    const lineCostValue = parseFloat(lineCost);
    if (isNaN(lineCostValue) || lineCostValue < 0) {
      showError('Please enter a valid line cost (must be a positive number)');
      return;
    }

    try {
      setSaving(true);
      await purchaseOrderAPI.updateLineItem(orderId!, itemId, {
        line_cost: lineCostValue,
      });
      await loadOrder(); // Reload to get updated data
      setEditingCostItemId(null);
      setLineCost('');
    } catch (err: any) {
      showError(extractErrorMessage(err, 'Failed to update line cost'));
      console.error('Error updating line cost:', err);
    } finally {
      setSaving(false);
    }
  };

  // Per-line associations (op-shb9): "this line is for job X / committee Y".
  // Set after the fact as often as at order time — which job the parts were for
  // is frequently identified once they arrive.
  const handleEditAssociation = (item: PurchaseOrderItem) => {
    setEditingAssociationItemId(item.id);
    setLineWorkOrder(item.work_order || '');
    setLineCommittee(item.owning_group ? String(item.owning_group) : '');
  };

  const handleCancelEditAssociation = () => {
    setEditingAssociationItemId(null);
    setLineWorkOrder('');
    setLineCommittee('');
  };

  const handleSaveAssociation = async (itemId: string) => {
    try {
      setSaving(true);
      await purchaseOrderAPI.updateLineItem(orderId!, itemId, {
        work_order: lineWorkOrder || null,
        owning_group: lineCommittee ? Number(lineCommittee) : null,
      });
      await loadOrder();
      handleCancelEditAssociation();
    } catch (err: any) {
      showError(extractErrorMessage(err, 'Failed to update line associations'));
      console.error('Error updating line associations:', err);
    } finally {
      setSaving(false);
    }
  };

  const handleSaveShipmentDate = async (itemId: string) => {
    try {
      setSaving(true);
      await purchaseOrderAPI.updateLineItem(orderId!, itemId, {
        expected_shipment_date: shipmentDate || undefined,
      });
      await loadOrder(); // Reload to get updated data
      setEditingItemId(null);
      setShipmentDate('');
    } catch (err: any) {
      showError(extractErrorMessage(err, 'Failed to update shipment date'));
      console.error('Error updating shipment date:', err);
    } finally {
      setSaving(false);
    }
  };

  const handleVoidItem = (itemId: string) => {
    if (!voidReason.trim()) {
      showError('Please provide a reason for voiding this line item');
      return;
    }

    confirmAction(
      'Void line item?',
      'Are you sure you want to void this line item? This will also mark the item as discontinued from this supplier.',
      async () => {
        try {
          setSaving(true);
          await purchaseOrderAPI.voidLineItem(orderId!, itemId, voidReason);
          await loadOrder(); // Reload to get updated data
          setVoidingItemId(null);
          setVoidReason('');
        } catch (err: any) {
          showError(extractErrorMessage(err, 'Failed to void line item'));
          console.error('Error voiding line item:', err);
        } finally {
          setSaving(false);
        }
      },
      { labels: { confirm: 'Void', cancel: 'Cancel' }, color: 'red' },
    );
  };

  const handleVoidOrder = async () => {
    const reason = await promptInput(
      'Void purchase order',
      'Reason for voiding (optional)',
      undefined,
      { placeholder: 'e.g. supplier rejected all line items' },
    );
    if (reason === null) return;

    confirmAction(
      'Void this purchase order?',
      'This will void the PO and cascade to all non-voided line items. This cannot be undone.',
      async () => {
        try {
          setSaving(true);
          await purchaseOrderAPI.voidOrder(orderId!, reason);
          showSuccess('Purchase order voided');
          await loadOrder();
        } catch (err: any) {
          showError(extractErrorMessage(err, 'Failed to void purchase order'));
          console.error('Error voiding purchase order:', err);
        } finally {
          setSaving(false);
        }
      },
      { labels: { confirm: 'Void PO', cancel: 'Cancel' }, color: 'red' },
    );
  };

  const canVoidOrder = (po: PurchaseOrder) =>
    isStaff && po.status !== 'voided' && po.status !== 'received';

  const canSendToSupplier = (po: PurchaseOrder) => isAuthenticated && po.status === 'draft';

  const canConfirmOrder = (po: PurchaseOrder) => isAuthenticated && po.status === 'sent';

  const handleSendToSupplier = async () => {
    if (transitioning) return;
    try {
      setTransitioning(true);
      await purchaseOrderAPI.sendToSupplier(orderId!);
      await loadOrder();
      showSuccess('Purchase order sent to supplier');
    } catch (err: any) {
      showError(extractErrorMessage(err, 'Failed to send purchase order to supplier'));
      console.error('Error sending purchase order to supplier:', err);
    } finally {
      setTransitioning(false);
    }
  };

  const handleConfirmOrder = async () => {
    if (transitioning) return;
    try {
      setTransitioning(true);
      await purchaseOrderAPI.confirmOrder(orderId!);
      await loadOrder();
      showSuccess('Purchase order confirmed');
    } catch (err: any) {
      showError(extractErrorMessage(err, 'Failed to confirm purchase order'));
      console.error('Error confirming purchase order:', err);
    } finally {
      setTransitioning(false);
    }
  };

  // Fetch the adapter-appropriate order export for this PO (op-svpq). Shared by
  // every export affordance (CSV download, copy block, Amazon cart); also
  // refreshes the missing-/invalid-SKU warnings so the operator sees which lines
  // still need a usable supplier part number before they order.
  const fetchOrderPad = async (): Promise<OrderPadExport | null> => {
    try {
      setExportingOrderPad(true);
      const response = await purchaseOrderAPI.exportOrder(orderId!);
      setOrderPad(response.data);
      return response.data;
    } catch (err: any) {
      showError(extractErrorMessage(err, 'Failed to build order pad'));
      console.error('Error building order pad:', err);
      return null;
    } finally {
      setExportingOrderPad(false);
    }
  };

  const handleDownloadOrderPad = async () => {
    const pad = await fetchOrderPad();
    if (!pad || pad.csv == null) return;
    const blob = new Blob([pad.csv], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = pad.filename ?? 'order.csv';
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  };

  const handleCopyOrderPad = async () => {
    const pad = await fetchOrderPad();
    if (!pad || pad.text == null) return;
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(pad.text);
        showSuccess('Order pad copied to clipboard');
      } catch {
        showError('Could not copy order pad to clipboard');
      }
    } else {
      showError('Clipboard is not available in this browser');
    }
  };

  // Amazon adapter (op-svpq): the export returns add-to-cart URL(s) rather than a
  // file. Open the first cart in a new tab; when the PO is chunked across
  // several carts (Amazon caps cart-URL length) the per-chunk buttons rendered
  // below let the operator open each one.
  const openAmazonCart = (url: string) => {
    window.open(url, '_blank', 'noopener,noreferrer');
  };

  const handleOpenAmazonCart = async () => {
    const pad = await fetchOrderPad();
    if (!pad) return;
    const urls = pad.cart_urls ?? [];
    if (urls.length === 0) {
      showError('No Amazon cart could be built — check the lines have valid ASINs.');
      return;
    }
    openAmazonCart(urls[0]);
  };

  const handleOpenMarkDelivered = () => {
    const today = formatYmd(new Date());
    setDeliveryDate(today);
    setDeliveryTracking('');
    setDeliveryCarrier('');
    setMarkingDelivered(true);
  };

  const handleCancelMarkDelivered = () => {
    setMarkingDelivered(false);
    setDeliveryDate('');
    setDeliveryTracking('');
    setDeliveryCarrier('');
  };

  const handleSubmitMarkDelivered = async () => {
    if (!deliveryDate) {
      showError('Please select a delivery date');
      return;
    }
    if (saving) return;

    try {
      setSaving(true);
      const response = await purchaseOrderAPI.markDelivered(orderId!, {
        delivery_date: deliveryDate,
        tracking_number: deliveryTracking || undefined,
        carrier: deliveryCarrier || undefined,
      });
      if (response.data && typeof response.data === 'object' && response.data.id) {
        setOrder(response.data as PurchaseOrder);
      }
      handleCancelMarkDelivered();
      showSuccess('Purchase order marked as delivered');
    } catch (err: any) {
      showError(extractErrorMessage(err, 'Failed to mark purchase order as delivered'));
      console.error('Error marking delivered:', err);
    } finally {
      setSaving(false);
    }
  };

  const canMarkDelivered = (po: PurchaseOrder) =>
    isAuthenticated && ['sent', 'confirmed', 'partially_received'].includes(po.status);

  const canReceiveItems = (po: PurchaseOrder) =>
    isAuthenticated && ['sent', 'confirmed', 'partially_received'].includes(po.status);

  const getReceivableItems = (po: PurchaseOrder) =>
    po.items.filter((item) => !item.is_voided && !item.is_fully_received);

  const handleOpenReceiveItems = () => {
    if (!order) return;
    const initialQuantities: Record<string, string> = {};
    getReceivableItems(order).forEach((item) => {
      initialQuantities[item.id] = String(item.quantity_pending);
    });
    setReceiveQuantities(initialQuantities);
    setReceiveDeliveryDate(formatYmd(new Date()));
    setReceiveNotes('');
    setReceivingItems(true);
  };

  const handleCancelReceiveItems = () => {
    setReceivingItems(false);
    setReceiveQuantities({});
    setSerialInputs({});
    setReceiveDeliveryDate('');
    setReceiveNotes('');
  };

  const handleReceiveQuantityChange = (itemId: string, value: string) => {
    setReceiveQuantities((prev) => ({ ...prev, [itemId]: value }));
  };

  const handleSerialInputChange = (itemId: string, value: string) => {
    setSerialInputs((prev) => ({ ...prev, [itemId]: value }));
  };

  // Serialized line + the qty being received on it, for the serial-capture UI.
  const isSerializedLine = (item: PurchaseOrderItem): boolean =>
    Boolean(item.item_details?.is_serialized && item.item_details?.id);

  const receiveQtyFor = (item: PurchaseOrderItem): number => {
    const raw = receiveQuantities[item.id];
    if (raw === undefined || raw.trim() === '') return 0;
    const n = Number.parseInt(raw, 10);
    return Number.isNaN(n) || n <= 0 ? 0 : n;
  };

  const handleSubmitReceiveItems = async () => {
    if (!order || saving) return;

    const lines: { purchase_order_item: number; quantity_received: number }[] = [];
    // Serialized lines: one SerializedComponent per captured serial, created
    // against this PO line (provenance) once the receipt is recorded.
    const serialPlan: { item: PurchaseOrderItem; serials: string[] }[] = [];
    for (const item of getReceivableItems(order)) {
      const raw = receiveQuantities[item.id];
      if (raw === undefined || raw.trim() === '') continue;
      const quantity = Number.parseInt(raw, 10);
      if (Number.isNaN(quantity) || quantity <= 0) continue;
      if (quantity > item.quantity_pending) {
        showError(
          `Cannot receive ${quantity} of ${getItemNameAndSku(item).itemName}; ` +
            `only ${item.quantity_pending} pending`,
        );
        return;
      }
      if (isSerializedLine(item)) {
        const serials = parseSerialNumbers(serialInputs[item.id] ?? '');
        if (serials.length !== quantity) {
          showError(
            `Enter ${quantity} unique serial number${quantity === 1 ? '' : 's'} for ` +
              `${getItemNameAndSku(item).itemName} (got ${serials.length}).`,
          );
          return;
        }
        serialPlan.push({ item, serials });
      }
      lines.push({ purchase_order_item: Number(item.id), quantity_received: quantity });
    }

    if (lines.length === 0) {
      showError('Enter a quantity for at least one item to receive');
      return;
    }

    try {
      setSaving(true);
      const response = await purchaseOrderAPI.receiveItems(orderId!, {
        items: lines,
        delivery_date: receiveDeliveryDate || undefined,
        receipt_notes: receiveNotes || undefined,
      });
      if (response.data && typeof response.data === 'object' && response.data.id) {
        setOrder(response.data as PurchaseOrder);
      }

      // Record the individual serialized units against their PO line.
      let serialsCreated = 0;
      let serialsFailed = 0;
      for (const { item, serials } of serialPlan) {
        const itemId = item.item_details?.id;
        if (!itemId) continue;
        const results = await Promise.allSettled(
          serials.map((serial_number) =>
            serializedComponentsAPI.create({
              item: itemId,
              serial_number,
              provenance_purchase_order_item: Number(item.id),
            }),
          ),
        );
        for (const r of results) {
          if (r.status === 'fulfilled') serialsCreated += 1;
          else serialsFailed += 1;
        }
      }

      handleCancelReceiveItems();
      if (serialsFailed > 0) {
        showError(
          `Items received, but ${serialsFailed} serialized unit` +
            `${serialsFailed === 1 ? '' : 's'} could not be recorded ` +
            '(duplicate serial or permission). Add them from the item page.',
        );
      } else if (serialsCreated > 0) {
        showSuccess(
          `Items received; recorded ${serialsCreated} serialized unit` +
            `${serialsCreated === 1 ? '' : 's'}.`,
        );
      } else {
        showSuccess('Items received');
      }
    } catch (err: any) {
      showError(extractErrorMessage(err, 'Failed to receive items'));
      console.error('Error receiving items:', err);
    } finally {
      setSaving(false);
    }
  };

  const handleStartEditMetadata = () => {
    if (!order) return;
    setMetadataSupplierOrderNumber(order.supplier_order_number || '');
    setMetadataSalesOrderNumber(order.sales_order_number || '');
    setMetadataExpectedDelivery(order.expected_delivery_date || '');
    setMetadataOrderDate(utcYmd(order.order_date));
    setMetadataPriority(order.priority || 'normal');
    setMetadataPaymentTerms(order.payment_terms || '');
    setMetadataFreightTerms(order.freight_terms || '');
    setMetadataWorkOrder(order.work_order || '');
    setMetadataCommittee(order.owning_group ? String(order.owning_group) : '');
    setEditingMetadata(true);
  };

  const handleCancelEditMetadata = () => {
    setEditingMetadata(false);
    setMetadataSupplierOrderNumber('');
    setMetadataSalesOrderNumber('');
    setMetadataExpectedDelivery('');
    setMetadataOrderDate('');
    setMetadataPriority('normal');
    setMetadataPaymentTerms('');
    setMetadataFreightTerms('');
    setMetadataWorkOrder('');
    setMetadataCommittee('');
  };

  const handleSaveMetadata = async () => {
    try {
      setSaving(true);
      await purchaseOrderAPI.updateOrder(orderId!, {
        supplier_order_number: metadataSupplierOrderNumber,
        sales_order_number: metadataSalesOrderNumber,
        expected_delivery_date: metadataExpectedDelivery || null,
        // Header terms (op-bwo9). The order date is edited as a day but the
        // field is a datetime, so send midday UTC — the day the operator
        // picked is the day the payment schedule is derived from. A cleared
        // date would leave the order without one, so it is only sent when set.
        ...(metadataOrderDate && { order_date: ymdToUtcDateTime(metadataOrderDate) }),
        priority: metadataPriority,
        // '' is a real value for both terms fields: "not agreed yet".
        payment_terms: metadataPaymentTerms,
        freight_terms: metadataFreightTerms,
        // Empty picker means "no association" — send null to clear it.
        work_order: metadataWorkOrder || null,
        owning_group: metadataCommittee ? Number(metadataCommittee) : null,
      });
      await loadOrder();
      setEditingMetadata(false);
      showSuccess('Purchase order details updated');
    } catch (err: any) {
      showError(extractErrorMessage(err, 'Failed to update purchase order details'));
      console.error('Error updating PO metadata:', err);
    } finally {
      setSaving(false);
    }
  };

  /**
   * Add one line from a typed or scanned identifier (oms-po-add-item).
   *
   * The server owns every rule — draft-only, does-this-supplier-supply-it, and
   * what to do when the item is already on the order — so this handler only
   * routes the answers: a 409 carries the candidate set to choose from, any
   * other failure is a message to show. The success path patches the page from
   * the response's full purchase order rather than re-running the initial
   * loader (docs/REACTIVE_MUTATIONS.md), so the operator's scroll position and
   * the next scan's focus survive the add.
   */
  const submitAddLine = async (payload: AddPurchaseOrderLinePayload) => {
    try {
      setAddingLine(true);
      setAddLineError(null);
      const response = await purchaseOrderAPI.addLineItem(orderId!, payload);
      const { created, line_item: lineItem, match, purchase_order: refreshed } = response.data;

      setOrder(refreshed);
      setAddLineIdentifier('');
      setAddLineCandidates([]);

      const itemName = lineItem?.item_details?.name || match?.item?.name || 'Item';
      const matchedBy = match ? ` (matched on ${match.match_label} ${match.matched_value})` : '';
      setAddLineNotice(
        created
          ? `Added ${itemName} × ${lineItem.quantity_ordered}${matchedBy}`
          : `${itemName} was already on this order — quantity is now ${lineItem.quantity_ordered}${matchedBy}`,
      );
      showSuccess(created ? `Added ${itemName}` : `Updated ${itemName}`);
    } catch (err: any) {
      const data = err?.response?.data;
      setAddLineNotice(null);
      if (data?.code === 'ambiguous' && Array.isArray(data.candidates)) {
        setAddLineCandidates(data.candidates);
      } else {
        setAddLineCandidates([]);
      }
      setAddLineError(
        typeof data?.error === 'string'
          ? data.error
          : extractErrorMessage(err, 'Failed to add line item'),
      );
    } finally {
      setAddingLine(false);
    }
  };

  const handleAddLineSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    // The field stays focused (and therefore submittable) while a request is in
    // flight, so a second Enter must not start a second add.
    if (addingLine) {
      return;
    }
    const identifier = addLineIdentifier.trim();
    if (!identifier) {
      setAddLineError('Type or scan an item name, SKU, barcode, or supplier SKU.');
      return;
    }
    void submitAddLine({ identifier });
  };

  const handleUploadAttachment = async () => {
    if (!attachmentFile) {
      showError('Please choose a file to upload');
      return;
    }

    try {
      setUploadingAttachment(true);
      await purchaseOrderAPI.uploadAttachment(orderId!, attachmentFile, attachmentDescription);
      await loadOrder();
      setAttachmentFile(null);
      setAttachmentDescription('');
      showSuccess('Attachment uploaded');
    } catch (err: any) {
      showError(extractErrorMessage(err, 'Failed to upload attachment'));
      console.error('Error uploading attachment:', err);
    } finally {
      setUploadingAttachment(false);
    }
  };

  const handleDeleteAttachment = (attachment: PurchaseOrderAttachment) => {
    confirmAction(
      'Delete attachment?',
      `Remove "${attachment.file_name || attachment.description || 'this attachment'}" from the purchase order? This cannot be undone.`,
      async () => {
        try {
          setSaving(true);
          await purchaseOrderAPI.deleteAttachment(orderId!, attachment.id);
          await loadOrder();
          showSuccess('Attachment deleted');
        } catch (err: any) {
          showError(extractErrorMessage(err, 'Failed to delete attachment'));
          console.error('Error deleting attachment:', err);
        } finally {
          setSaving(false);
        }
      },
      { labels: { confirm: 'Delete', cancel: 'Cancel' }, color: 'red' },
    );
  };

  const formatDate = (dateString: string | null) =>
    formatDateOnly(dateString, { year: 'numeric', month: 'short', day: 'numeric' });

  const formatCurrency = (value: string | null) => {
    if (!value) return '—';
    const num = parseFloat(value);
    if (Number.isNaN(num)) return '—';
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 2,
    }).format(num);
  };

  if (loading) {
    return (
      <WorkspacePage
        testId="purchase-order-page"
        hero={{ eyebrow: 'Purchasing · Order', title: 'Purchase order', description: 'Loading…' }}
      >
        <Paper withBorder p="md">
          <Text c="dimmed">Loading purchase order…</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  if (error || !order) {
    return (
      <WorkspacePage
        testId="purchase-order-page"
        hero={{
          eyebrow: 'Purchasing · Order',
          title: 'Purchase order',
          description: error || 'Not found.',
        }}
      >
        <Paper withBorder p="md" radius="md" bg="red.0" c="red.9">
          <Text>{error || 'Purchase order not found'}</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  const receivableItems = getReceivableItems(order);

  // Detail-page running total (op-r1sg). Summed from the rendered lines rather
  // than read off order.estimated_total so it stays live when a line is cost-
  // edited or voided in place; the two should agree, which makes the footer a
  // free sanity check on the stored create-time snapshot.
  //
  // Voided lines are excluded (matching the backend's effective_estimated_total)
  // and each line contributes exactly what its "Line Cost" cell displays —
  // actual where known, estimated otherwise. Where those diverge, the footer
  // also spells out the pure estimated sum, which is what estimated_total holds.
  const activeItems = order.items.filter((item) => !item.is_voided);
  const sumLines = (pick: (item: PurchaseOrderItem) => string | null) =>
    activeItems.reduce((total, item) => total + (parseFloat(pick(item) || '0') || 0), 0);
  const lineCostTotal = sumLines((item) => item.actual_cost || item.estimated_cost);
  const estimatedLineTotal = sumLines((item) => item.estimated_cost);
  const totalsDiffer = lineCostTotal.toFixed(2) !== estimatedLineTotal.toFixed(2);

  // Status-gated hero affordances: Send (draft→sent) and Confirm (sent→confirmed)
  // surface the existing PO lifecycle transitions alongside receive/mark-delivered.
  const heroActions: React.ReactNode[] = [];
  if (canSendToSupplier(order)) {
    heroActions.push(
      <Button key="send-to-supplier" onClick={handleSendToSupplier} disabled={transitioning}>
        Send to Supplier
      </Button>,
    );
  }
  if (canConfirmOrder(order)) {
    heroActions.push(
      <Button key="confirm-order" onClick={handleConfirmOrder} disabled={transitioning}>
        Confirm
      </Button>,
    );
  }
  // Order export: turn this PO's lines into a vendor-ready order artifact whose
  // shape follows the supplier's ordering adapter (op-svpq) — an Amazon add-to-
  // cart link, an HD Supply Part#,Qty CSV, or the generic part#,qty pad. Login-
  // gated (like the API) and only offered when there's a non-voided line to
  // order.
  if (isAuthenticated && order.items.some((item) => !item.is_voided)) {
    const adapter = order.supplier_ordering_adapter;
    if (adapter === 'amazon') {
      heroActions.push(
        <Button
          key="open-amazon-cart"
          variant="default"
          onClick={handleOpenAmazonCart}
          disabled={exportingOrderPad}
        >
          Open Amazon cart
        </Button>,
      );
    } else if (adapter === 'hdsupply') {
      heroActions.push(
        <Button
          key="download-order-pad"
          variant="default"
          onClick={handleDownloadOrderPad}
          disabled={exportingOrderPad}
        >
          Download for HD Supply
        </Button>,
        <Button
          key="copy-order-pad"
          variant="default"
          onClick={handleCopyOrderPad}
          disabled={exportingOrderPad}
        >
          Copy order pad
        </Button>,
      );
    } else {
      heroActions.push(
        <Button
          key="download-order-pad"
          variant="default"
          onClick={handleDownloadOrderPad}
          disabled={exportingOrderPad}
        >
          Download order pad (CSV)
        </Button>,
        <Button
          key="copy-order-pad"
          variant="default"
          onClick={handleCopyOrderPad}
          disabled={exportingOrderPad}
        >
          Copy order pad
        </Button>,
      );
    }
  }
  if (canReceiveItems(order) && !markingDelivered && !receivingItems) {
    heroActions.push(
      <Button key="receive-items" onClick={handleOpenReceiveItems}>
        Receive items
      </Button>,
      <Button key="mark-delivered" variant="default" onClick={handleOpenMarkDelivered}>
        Mark as delivered
      </Button>,
    );
  }

  return (
    <WorkspacePage
      testId="purchase-order-page"
      hero={{
        eyebrow: `Purchasing · ${order.supplier_details}`,
        title: `PO ${order.po_number}`,
        description: order.status_label,
        action: heroActions.length > 0 ? <Group gap="sm">{heroActions}</Group> : undefined,
      }}
    >
      <div className="purchase-order-page">
        {orderPad && orderPad.missing_sku.length > 0 && (
          <Paper
            withBorder
            p="sm"
            radius="md"
            bg="yellow.0"
            c="yellow.9"
            mb="md"
            data-testid="order-pad-missing-sku-warning"
          >
            <Text size="sm">
              {orderPad.missing_sku.length}{' '}
              {orderPad.missing_sku.length === 1 ? 'line has' : 'lines have'} no supplier
              part number — fix the item supplier before ordering.
            </Text>
          </Paper>
        )}
        {orderPad && orderPad.invalid_sku.length > 0 && (
          <Paper
            withBorder
            p="sm"
            radius="md"
            bg="red.0"
            c="red.9"
            mb="md"
            data-testid="order-pad-invalid-sku-warning"
          >
            <Text size="sm">
              {orderPad.invalid_sku.length}{' '}
              {orderPad.invalid_sku.length === 1 ? 'item has' : 'items have'}{' '}
              {orderPad.adapter === 'amazon' ? 'an invalid ASIN' : 'an invalid part number'} —
              fix the item supplier part number before ordering.
            </Text>
          </Paper>
        )}
        {orderPad?.adapter === 'amazon' && (orderPad.cart_urls?.length ?? 0) > 1 && (
          <Paper withBorder p="sm" radius="md" mb="md" data-testid="amazon-cart-chunks">
            <Text size="sm" mb="xs">
              This order is split across {orderPad.cart_urls!.length} Amazon carts (Amazon
              limits cart-URL length). Open each:
            </Text>
            <Group gap="sm">
              {orderPad.cart_urls!.map((url, index) => (
                <Button
                  key={`amazon-cart-${index}`}
                  size="xs"
                  variant="light"
                  onClick={() => openAmazonCart(url)}
                >
                  Open cart {index + 1}/{orderPad.cart_urls!.length}
                </Button>
              ))}
            </Group>
          </Paper>
        )}
        <div className="po-status">
          {canVoidOrder(order) && (
            <button
              type="button"
              className="btn-danger void-po-button"
              onClick={handleVoidOrder}
              disabled={saving}
            >
              Void PO
            </button>
          )}
        </div>

      {order.status === 'voided' && (
        <section className="po-voided-banner" aria-label="Voided purchase order">
          <span className="status-badge status-voided">VOIDED</span>
          {order.void_reason && (
            <p className="void-reason">
              <strong>Reason:</strong> {order.void_reason}
            </p>
          )}
          {order.voided_by_username && (
            <p className="void-meta">
              Voided by {order.voided_by_username}
              {order.voided_at && ` on ${formatDate(order.voided_at)}`}
            </p>
          )}
        </section>
      )}

      {markingDelivered && (
        <section className="mark-delivered-panel" aria-label="Mark purchase order as delivered">
          <h2>Mark as Delivered</h2>
          <div className="mark-delivered-fields">
            <label htmlFor="mark-delivered-date">
              Delivery Date
              <input
                id="mark-delivered-date"
                type="date"
                value={deliveryDate}
                onChange={(e) => setDeliveryDate(e.target.value)}
                required
              />
            </label>
            <label htmlFor="mark-delivered-tracking">
              Tracking Number (optional)
              <input
                id="mark-delivered-tracking"
                type="text"
                value={deliveryTracking}
                onChange={(e) => setDeliveryTracking(e.target.value)}
                placeholder="e.g. 1Z999AA10123456784"
              />
            </label>
            <label htmlFor="mark-delivered-carrier">
              Carrier (optional)
              <input
                id="mark-delivered-carrier"
                type="text"
                value={deliveryCarrier}
                onChange={(e) => setDeliveryCarrier(e.target.value)}
                placeholder="e.g. UPS"
              />
            </label>
          </div>
          <div className="mark-delivered-actions">
            <button
              type="button"
              className="btn-primary"
              onClick={handleSubmitMarkDelivered}
              disabled={saving || !deliveryDate}
            >
              {saving ? 'Saving…' : 'Confirm Delivery'}
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={handleCancelMarkDelivered}
              disabled={saving}
            >
              Cancel
            </button>
          </div>
        </section>
      )}

      {receivingItems && (
        <section className="receive-items-panel" aria-label="Receive purchase order items">
          <h2>Receive Items</h2>
          <div className="receive-items-fields">
            <label htmlFor="receive-delivery-date">
              Delivery Date (optional)
              <input
                id="receive-delivery-date"
                type="date"
                value={receiveDeliveryDate}
                onChange={(e) => setReceiveDeliveryDate(e.target.value)}
              />
            </label>
            <label htmlFor="receive-notes">
              Receipt Notes (optional)
              <input
                id="receive-notes"
                type="text"
                value={receiveNotes}
                onChange={(e) => setReceiveNotes(e.target.value)}
                placeholder="e.g. Partial shipment, backorder to follow"
              />
            </label>
          </div>
          {receivableItems.length === 0 ? (
            <p className="no-data">All line items have already been received.</p>
          ) : (
            <table className="items-table receive-items-table">
              <thead>
                <tr>
                  <th>Item</th>
                  <th>SKU</th>
                  <th>Pending</th>
                  <th>Receive Qty</th>
                </tr>
              </thead>
              <tbody>
                {receivableItems.map((item) => {
                  const { itemName, itemSku } = getItemNameAndSku(item);
                  const serialized = isSerializedLine(item);
                  const qty = receiveQtyFor(item);
                  const serialCount = serialized
                    ? parseSerialNumbers(serialInputs[item.id] ?? '').length
                    : 0;
                  return (
                    <React.Fragment key={item.id}>
                      <tr>
                        <td>
                          {itemName}
                          {serialized && (
                            <span className="receive-serial-tag"> · serialized</span>
                          )}
                        </td>
                        <td>{itemSku}</td>
                        <td>{item.quantity_pending}</td>
                        <td>
                          <input
                            type="number"
                            min="0"
                            max={item.quantity_pending}
                            step="1"
                            className="receive-items-qty-input"
                            value={receiveQuantities[item.id] ?? ''}
                            onChange={(e) => handleReceiveQuantityChange(item.id, e.target.value)}
                            disabled={saving}
                            aria-label={`Receive quantity for ${itemName}`}
                          />
                        </td>
                      </tr>
                      {/* Live consequence row (op-8n0). Driven ENTIRELY by
                          local state and the line's own snapshot, so it updates
                          as the quantity is typed and the operator sees what
                          receiving will do BEFORE committing. Mirrors the
                          serialized disclosure sub-row directly below. */}
                      {item.is_kit_line && qty > 0 && (item.kit_components?.length ?? 0) > 0 && (
                        <tr
                          className="receive-serial-row"
                          data-testid={`receive-kit-consequence-${item.id}`}
                        >
                          <td colSpan={4}>
                            Receiving {qty} {qty === 1 ? 'kit' : 'kits'} adds{' '}
                            {(item.kit_components ?? []).reduce(
                              (sum, component) => sum + component.quantity_per_kit * qty,
                              0
                            )}{' '}
                            units across {(item.kit_components ?? []).length} items
                          </td>
                        </tr>
                      )}
                      {serialized && qty > 0 && (
                        <tr className="receive-serial-row">
                          <td colSpan={4}>
                            <label htmlFor={`serials-${item.id}`}>
                              Serial numbers for {itemName} — one per line ({serialCount}/{qty})
                            </label>
                            <textarea
                              id={`serials-${item.id}`}
                              className="receive-serials-input"
                              rows={Math.min(Math.max(qty, 2), 8)}
                              value={serialInputs[item.id] ?? ''}
                              onChange={(e) => handleSerialInputChange(item.id, e.target.value)}
                              disabled={saving}
                              placeholder={'SN-0001\nSN-0002'}
                              aria-label={`Serial numbers for ${itemName}`}
                            />
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          )}
          <div className="receive-items-actions">
            <button
              type="button"
              className="btn-primary"
              onClick={handleSubmitReceiveItems}
              disabled={saving || receivableItems.length === 0}
            >
              {saving ? 'Saving…' : 'Confirm Receipt'}
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={handleCancelReceiveItems}
              disabled={saving}
            >
              Cancel
            </button>
          </div>
        </section>
      )}

      <div className="po-info">
        <div className="info-item">
          <span className="info-label">Date Ordered:</span>
          {/* The business day, not the viewer's local rendering of the stored
              instant — it is the day `payment_schedule` is derived from. */}
          <span className="info-value">{formatDate(utcYmd(order.order_date))}</span>
        </div>
        <div className="info-item">
          <span className="info-label">Date Promised:</span>
          <span className="info-value">{formatDate(order.expected_delivery_date)}</span>
        </div>
        <div className="info-item">
          <span className="info-label">Priority:</span>
          <span className="info-value">{priorityLabel(order.priority)}</span>
        </div>
        <div className="info-item">
          <span className="info-label">Payment Terms:</span>
          <span className="info-value">{paymentTermsLabel(order.payment_terms)}</span>
        </div>
        <div className="info-item">
          <span className="info-label">Freight:</span>
          <span className="info-value">{freightTermsLabel(order.freight_terms)}</span>
        </div>
        <div className="info-item">
          <span className="info-label">Supplier Order #:</span>
          <span className="info-value">{order.supplier_order_number || '—'}</span>
        </div>
        <div className="info-item">
          <span className="info-label">Sales Order #:</span>
          <span className="info-value">{order.sales_order_number || '—'}</span>
        </div>
        <div className="info-item">
          <span className="info-label">Agreement:</span>
          <span className="info-value">{order.supplier_agreement_details?.name || '—'}</span>
        </div>
        <div className="info-item">
          <span className="info-label">Work Order:</span>
          <span className="info-value">{workOrderDetailsLabel(order.work_order_details)}</span>
        </div>
        <div className="info-item">
          <span className="info-label">Committee:</span>
          <span className="info-value">{order.owning_group_details?.name || '—'}</span>
        </div>
        <div className="info-item">
          <span className="info-label">Estimated Total:</span>
          <span className="info-value">{formatCurrency(order.estimated_total)}</span>
        </div>
        {/* Derived from the terms above (op-bwo9) — never stored, so voiding a
            line moves it. Read-only: change the terms to change the payment. */}
        <div className="info-item">
          <span className="info-label">Payment schedule:</span>
          <span className="info-value">{paymentScheduleSummary(order.payment_schedule)}</span>
        </div>
      </div>

      {isAuthenticated && (
        <section className="po-metadata" aria-label="Purchase order details">
          <div className="po-metadata-header">
            <h2>Order Details</h2>
            {!editingMetadata && (
              <button
                type="button"
                className="btn-secondary"
                onClick={handleStartEditMetadata}
              >
                Edit Details
              </button>
            )}
          </div>
          {editingMetadata ? (
            <div className="po-metadata-edit">
              <label htmlFor="metadata-supplier-order">
                Supplier Order Number
                <input
                  id="metadata-supplier-order"
                  type="text"
                  value={metadataSupplierOrderNumber}
                  onChange={(e) => setMetadataSupplierOrderNumber(e.target.value)}
                  placeholder="Order number assigned by supplier"
                  maxLength={128}
                />
              </label>
              <label htmlFor="metadata-sales-order">
                Sales Order Number
                <input
                  id="metadata-sales-order"
                  type="text"
                  value={metadataSalesOrderNumber}
                  onChange={(e) => setMetadataSalesOrderNumber(e.target.value)}
                  placeholder="Sales order reference"
                  maxLength={128}
                />
              </label>
              {/* Header terms (op-bwo9). `order_date` is editable because an
                  order is often entered after it was placed. */}
              <label htmlFor="metadata-order-date">
                Date Ordered
                <input
                  id="metadata-order-date"
                  type="date"
                  value={metadataOrderDate}
                  onChange={(e) => setMetadataOrderDate(e.target.value)}
                />
              </label>
              <label htmlFor="metadata-expected-delivery">
                Date Promised (expected delivery)
                <input
                  id="metadata-expected-delivery"
                  type="date"
                  value={metadataExpectedDelivery}
                  onChange={(e) => setMetadataExpectedDelivery(e.target.value)}
                />
              </label>
              <label htmlFor="metadata-priority">
                Priority
                <select
                  id="metadata-priority"
                  value={metadataPriority}
                  onChange={(e) => setMetadataPriority(e.target.value as PurchaseOrderPriority)}
                  disabled={saving}
                >
                  {PO_PRIORITY_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <label htmlFor="metadata-payment-terms">
                Payment Terms
                <select
                  id="metadata-payment-terms"
                  value={metadataPaymentTerms}
                  onChange={(e) =>
                    setMetadataPaymentTerms(e.target.value as PurchaseOrderPaymentTerms | '')
                  }
                  disabled={saving}
                >
                  <option value="">Not agreed</option>
                  {PO_PAYMENT_TERMS_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <label htmlFor="metadata-freight-terms">
                Freight Terms
                <select
                  id="metadata-freight-terms"
                  value={metadataFreightTerms}
                  onChange={(e) =>
                    setMetadataFreightTerms(e.target.value as PurchaseOrderFreightTerms | '')
                  }
                  disabled={saving}
                >
                  <option value="">Not agreed</option>
                  {PO_FREIGHT_TERMS_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              {/* Order-level associations (op-shb9). Attribution only — they
                  change no cost and bill no committee. */}
              <AssociationPickers
                idPrefix="metadata"
                workOrderOptions={withCurrentOption(
                  workOrderOptions,
                  metadataWorkOrder,
                  workOrderDetailsLabel(order.work_order_details),
                )}
                committeeOptions={withCurrentOption(
                  committeeOptions,
                  metadataCommittee,
                  order.owning_group_details?.name || '',
                )}
                workOrderValue={metadataWorkOrder}
                committeeValue={metadataCommittee}
                onWorkOrderChange={setMetadataWorkOrder}
                onCommitteeChange={setMetadataCommittee}
                disabled={saving}
              />
              <div className="po-metadata-actions">
                <button
                  type="button"
                  className="btn-primary"
                  onClick={handleSaveMetadata}
                  disabled={saving}
                >
                  {saving ? 'Saving…' : 'Save Details'}
                </button>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={handleCancelEditMetadata}
                  disabled={saving}
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : null}
        </section>
      )}

      <section className="po-attachments" aria-label="Purchase order attachments">
        <h2>Attachments</h2>
        {order.attachments.length === 0 ? (
          <p className="no-data">No attachments yet.</p>
        ) : (
          <ul className="attachments-list">
            {order.attachments.map((attachment) => (
              <li key={attachment.id} className="attachment-item">
                <div className="attachment-meta">
                  {attachment.file_url ? (
                    <a
                      href={attachment.file_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="attachment-link"
                    >
                      {attachment.file_name || 'Download'}
                    </a>
                  ) : (
                    <span>{attachment.file_name || 'Attachment'}</span>
                  )}
                  {attachment.description && (
                    <span className="attachment-description">— {attachment.description}</span>
                  )}
                  <span className="attachment-uploader">
                    {attachment.uploaded_by_name
                      ? `Uploaded by ${attachment.uploaded_by_name}`
                      : 'Uploaded'}{' '}
                    on {formatDate(attachment.uploaded_at)}
                  </span>
                </div>
                {isStaff && (
                  <button
                    type="button"
                    className="btn-danger"
                    onClick={() => handleDeleteAttachment(attachment)}
                    disabled={saving}
                  >
                    Delete
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}

        {isAuthenticated && (
          <div className="attachment-upload">
            <h3>Upload Attachment</h3>
            <label htmlFor="attachment-file">
              File
              <input
                id="attachment-file"
                type="file"
                onChange={(e) => setAttachmentFile(e.target.files?.[0] || null)}
                disabled={uploadingAttachment}
              />
            </label>
            <label htmlFor="attachment-description">
              Description (optional)
              <input
                id="attachment-description"
                type="text"
                value={attachmentDescription}
                onChange={(e) => setAttachmentDescription(e.target.value)}
                placeholder="e.g. Sales order from supplier"
                maxLength={500}
                disabled={uploadingAttachment}
              />
            </label>
            <button
              type="button"
              className="btn-primary"
              onClick={handleUploadAttachment}
              disabled={uploadingAttachment || !attachmentFile}
            >
              {uploadingAttachment ? 'Uploading…' : 'Upload'}
            </button>
          </div>
        )}
      </section>

      <section className="po-items">
        <h2>Line Items</h2>

        {/* Add-a-line entry (oms-po-add-item). Offered ONLY on a draft the
            viewer can edit — once an order has gone to the supplier, what it
            contains is a matter of record, so the control is absent rather
            than present-and-disabled. The server enforces the same rule; this
            is only the affordance. */}
        {isAuthenticated && order.status === 'draft' && (
          <form className="po-add-line" onSubmit={handleAddLineSubmit}>
            <label htmlFor="po-add-line-identifier">
              Add an item
              <input
                id="po-add-line-identifier"
                type="text"
                ref={addLineInputRef}
                value={addLineIdentifier}
                onChange={(e) => {
                  setAddLineIdentifier(e.target.value);
                  setAddLineError(null);
                }}
                placeholder="Scan a barcode, or type a name, SKU, or supplier SKU"
                // readOnly, not disabled: disabling the focused field blurs it,
                // and the next scan's character burst would land nowhere.
                readOnly={addingLine}
                autoComplete="off"
              />
            </label>
            <button type="submit" className="btn-primary" disabled={addingLine}>
              {addingLine ? 'Adding…' : 'Add to order'}
            </button>
            <p className="po-add-line-hint">
              A scanner works here: the scan lands in the field and its Enter submits it.
            </p>

            {addLineNotice && (
              <p className="po-add-line-notice" role="status">
                {addLineNotice}
              </p>
            )}

            {addLineError && (
              <p className="po-add-line-error" role="alert">
                {addLineError}
              </p>
            )}

            {/* Every row says WHY it came up (`match_label` / `matched_value`),
                which is the whole point on the choose-one path: a cross-vendor
                match resolves through another vendor's listing, so without it
                nothing on screen would contain what the operator scanned. */}
            {addLineCandidates.length > 0 && (
              <ul className="po-add-line-candidates">
                {addLineCandidates.map((candidate) => {
                  const onOrder = candidate.already_on_order;
                  // A voided line cannot be grown — the server refuses it with
                  // `line_voided` — so this row is a dead end, not a choice.
                  const voided = Boolean(onOrder?.is_voided);
                  return (
                    <li key={candidate.item_supplier}>
                      <span className="po-add-line-candidate-name">{candidate.item.name}</span>
                      <span className="po-add-line-candidate-meta">
                        {candidate.item.sku} · supplier SKU {candidate.supplier_sku || '—'} ·
                        matched on {candidate.match_label} {candidate.matched_value}
                        {onOrder && !voided
                          ? ` · already on this order (${onOrder.quantity_ordered})`
                          : ''}
                        {voided ? ' · voided on this order — restore or remove that line first' : ''}
                      </span>
                      {voided ? null : (
                        // `btn-primary`, the same class this form's "Add to
                        // order" submit uses. Not `btn-edit`: that resolves to
                        // `background: none` on this page while a global
                        // `color: white` wins the cascade, so these buttons
                        // rendered white-on-white — invisible in both the dev
                        // server and a production build. Clickable-if-you-guess
                        // is not a shop-floor control, and the choose-one list
                        // is worthless if the operator cannot see the choices.
                        // Reusing the page's proven submit class rather than
                        // adding a third button appearance is also the right
                        // design: this button adds an item, like that one does.
                        <button
                          type="button"
                          className="btn-primary"
                          disabled={addingLine}
                          onClick={() => submitAddLine({ item_supplier: candidate.item_supplier })}
                        >
                          Add {candidate.item.name}
                        </button>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </form>
        )}

        <table className="items-table">
          <thead>
            <tr>
              <th>Item</th>
              <th>SKU</th>
              <th>Quantity Ordered</th>
              <th>Quantity Received</th>
              <th>Unit Cost</th>
              <th>Line Cost</th>
              <th>Expected Shipment Date</th>
              <th>Ordered For</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {order.items.length === 0 ? (
              <tr>
                <td colSpan={10} className="no-data">
                  No line items found
                </td>
              </tr>
            ) : (
              order.items.map((item) => {
                const { itemName, itemSku } = getItemNameAndSku(item);

                return (
                <tr key={item.id} className={item.is_voided ? 'voided-item' : ''}>
                  <td>
                    {itemName}
                    <div>
                      <span className="item-type-badge" data-testid="line-item-type">
                        {getItemTypeLabel(item)}
                      </span>
                    </div>
                    {item.item_type === 'asset' && item.asset_details?.location_name && (
                      <div style={{ fontSize: '0.875rem', color: '#64748b', marginTop: '0.25rem' }}>
                        Location: {item.asset_details.location_name}
                      </div>
                    )}
                    {/* Kit breakdown (op-8n0). Rendered from THIS LINE's
                        payload — never a live kit fetch — so what is shown is
                        what receiving this line will credit. */}
                    {item.is_kit_line && (item.kit_components?.length ?? 0) > 0 && (
                      <div
                        style={{ fontSize: '0.875rem', color: '#64748b', marginTop: '0.25rem' }}
                        data-testid={`line-kit-breakdown-${item.id}`}
                      >
                        <span className="item-type-badge">kit</span>{' '}
                        Contains:{' '}
                        {(item.kit_components ?? [])
                          .map(
                            (component) =>
                              `${component.component_name} x${component.quantity}`
                          )
                          .join(', ')}
                      </div>
                    )}
                  </td>
                  <td>{itemSku}</td>
                  <td>{item.quantity_ordered}</td>
                  <td>{item.quantity_received}</td>
                  <td>
                    {item.unit_cost_actual 
                      ? formatCurrency(item.unit_cost_actual) 
                      : formatCurrency(item.unit_cost_ordered)}
                    {item.unit_cost_actual && (
                      <div style={{ fontSize: '0.75rem', color: '#64748b', marginTop: '0.25rem' }}>
                        (ordered: {formatCurrency(item.unit_cost_ordered)})
                      </div>
                    )}
                  </td>
                  <td>
                    {editingCostItemId === item.id ? (
                      <div className="edit-line-cost">
                        <input
                          type="number"
                          step="0.01"
                          min="0"
                          value={lineCost}
                          onChange={(e) => setLineCost(e.target.value)}
                          disabled={saving || item.is_voided}
                          className="cost-input"
                          placeholder="Enter total line cost"
                        />
                        <div className="calculated-unit-cost" style={{ fontSize: '0.75rem', color: '#64748b', marginTop: '0.25rem' }}>
                          Unit cost: {item.quantity_ordered > 0 
                            ? formatCurrency((parseFloat(lineCost || '0') / item.quantity_ordered).toFixed(4))
                            : '—'}
                        </div>
                        <div className="edit-actions">
                          <button
                            onClick={() => handleSaveLineCost(item.id, item)}
                            disabled={saving}
                            className="btn-save"
                          >
                            {saving ? 'Saving...' : 'Save'}
                          </button>
                          <button
                            onClick={handleCancelEditCost}
                            disabled={saving}
                            className="btn-cancel"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="line-cost-display">
                        <span>
                          {item.actual_cost 
                            ? formatCurrency(item.actual_cost)
                            : formatCurrency(item.estimated_cost)}
                        </span>
                        {!item.is_voided && isAuthenticated && (
                          <button
                            onClick={() => handleEditLineCost(item)}
                            className="btn-edit"
                            title="Edit line cost"
                            style={{ marginLeft: '0.5rem' }}
                          >
                            ✏️
                          </button>
                        )}
                      </div>
                    )}
                  </td>
                  <td>
                    {editingItemId === item.id ? (
                      <div className="edit-shipment-date">
                        <input
                          type="date"
                          value={shipmentDate}
                          onChange={(e) => setShipmentDate(e.target.value)}
                          disabled={saving || item.is_voided}
                          className="date-input"
                        />
                        <div className="edit-actions">
                          <button
                            onClick={() => handleSaveShipmentDate(item.id)}
                            disabled={saving}
                            className="btn-save"
                          >
                            {saving ? 'Saving...' : 'Save'}
                          </button>
                          <button
                            onClick={handleCancelEdit}
                            disabled={saving}
                            className="btn-cancel"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="shipment-date-display">
                        <span>{formatDate(item.expected_shipment_date)}</span>
                        {!item.is_voided && isAuthenticated && (
                          <button
                            onClick={() => handleEditShipmentDate(item)}
                            className="btn-edit"
                            title="Edit shipment date"
                          >
                            ✏️
                          </button>
                        )}
                      </div>
                    )}
                  </td>
                  <td>
                    {editingAssociationItemId === item.id ? (
                      <div className="edit-association">
                        <AssociationPickers
                          idPrefix={`line-${item.id}`}
                          workOrderOptions={withCurrentOption(
                            workOrderOptions,
                            lineWorkOrder,
                            workOrderDetailsLabel(item.work_order_details),
                          )}
                          committeeOptions={withCurrentOption(
                            committeeOptions,
                            lineCommittee,
                            item.owning_group_details?.name || '',
                          )}
                          workOrderValue={lineWorkOrder}
                          committeeValue={lineCommittee}
                          onWorkOrderChange={setLineWorkOrder}
                          onCommitteeChange={setLineCommittee}
                          disabled={saving || item.is_voided}
                        />
                        <div className="edit-actions">
                          <button
                            onClick={() => handleSaveAssociation(item.id)}
                            disabled={saving}
                            className="btn-save"
                          >
                            {saving ? 'Saving...' : 'Save'}
                          </button>
                          <button
                            onClick={handleCancelEditAssociation}
                            disabled={saving}
                            className="btn-cancel"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="association-display">
                        <span className="association-work-order">
                          {workOrderDetailsLabel(item.work_order_details)}
                        </span>
                        <span className="association-committee">
                          {item.owning_group_details?.name || '—'}
                        </span>
                        {!item.is_voided && isAuthenticated && (
                          <button
                            onClick={() => handleEditAssociation(item)}
                            className="btn-edit"
                            title="Edit work order and committee"
                            // The pencil glyph is the button's content, so it
                            // would otherwise be its whole accessible name.
                            aria-label="Edit work order and committee"
                          >
                            ✏️
                          </button>
                        )}
                      </div>
                    )}
                  </td>
                  <td>
                    {item.is_voided ? (
                      <div className="voided-status">
                        <span className="status-badge status-voided">Voided</span>
                        {item.void_reason && (
                          <div style={{ fontSize: '0.75rem', color: '#64748b', marginTop: '0.25rem' }}>
                            {item.void_reason}
                          </div>
                        )}
                      </div>
                    ) : (
                      <span className="status-badge status-active">Active</span>
                    )}
                  </td>
                  <td>
                    {isAuthenticated ? (
                      voidingItemId === item.id ? (
                        <div className="void-item-form">
                          <textarea
                            value={voidReason}
                            onChange={(e) => setVoidReason(e.target.value)}
                            placeholder="Reason for voiding (e.g., item discontinued by supplier)"
                            disabled={saving}
                            rows={3}
                            style={{ width: '100%', marginBottom: '0.5rem' }}
                          />
                          <div className="edit-actions">
                            <button
                              onClick={() => handleVoidItem(item.id)}
                              disabled={saving || !voidReason.trim()}
                              className="btn-void"
                            >
                              {saving ? 'Voiding...' : 'Confirm Void'}
                            </button>
                            <button
                              onClick={() => {
                                setVoidingItemId(null);
                                setVoidReason('');
                              }}
                              disabled={saving}
                              className="btn-cancel"
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="item-actions">
                          {!item.is_voided && editingItemId !== item.id && (
                            <>
                              <button
                                onClick={() => handleEditShipmentDate(item)}
                                className="btn-edit-item"
                              >
                                Edit Shipment Date
                              </button>
                              {item.quantity_received === 0 && (
                                <button
                                  onClick={() => setVoidingItemId(item.id)}
                                  className="btn-void-item"
                                  style={{ marginLeft: '0.5rem' }}
                                >
                                  Void Item
                                </button>
                              )}
                            </>
                          )}
                        </div>
                      )
                    ) : (
                      <span className="view-only-note">View only</span>
                    )}
                  </td>
                </tr>
                );
              })
            )}
          </tbody>
          {order.items.length > 0 && (
            <tfoot>
              <tr className="items-total-row">
                <td colSpan={5} className="items-total-label">
                  Total
                </td>
                <td className="items-total-value" data-testid="po-items-total">
                  {formatCurrency(lineCostTotal.toFixed(2))}
                  {totalsDiffer && (
                    <div className="items-total-estimated">
                      (estimated: {formatCurrency(estimatedLineTotal.toFixed(2))})
                    </div>
                  )}
                </td>
                <td colSpan={4} />
              </tr>
            </tfoot>
          )}
        </table>
      </section>
      </div>
    </WorkspacePage>
  );
};

export default PurchaseOrderPage;

