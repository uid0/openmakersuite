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
import React, { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import {
  OrderingAdapter,
  OrderPadExport,
  purchaseOrderAPI,
  serializedComponentsAPI,
  SerializedTrackingMode,
} from '../services/api';
import '../styles/PurchaseOrderPage.css';
import { formatDateOnly, formatYmd } from '../utils/dates';
import { confirmAction, promptInput, showError, showSuccess } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';
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
  status: string;
  status_label: string;
  order_date: string;
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
  const [attachmentDescription, setAttachmentDescription] = useState('');
  const [attachmentFile, setAttachmentFile] = useState<File | null>(null);
  const [uploadingAttachment, setUploadingAttachment] = useState(false);
  // Order export (adapter-aware, op-svpq) — last-fetched payload drives the
  // missing-/invalid-SKU warnings and the Amazon multi-cart panel;
  // `exportingOrderPad` disables the buttons while a fetch is in flight.
  const [orderPad, setOrderPad] = useState<OrderPadExport | null>(null);
  const [exportingOrderPad, setExportingOrderPad] = useState(false);

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
    setEditingMetadata(true);
  };

  const handleCancelEditMetadata = () => {
    setEditingMetadata(false);
    setMetadataSupplierOrderNumber('');
    setMetadataSalesOrderNumber('');
    setMetadataExpectedDelivery('');
  };

  const handleSaveMetadata = async () => {
    try {
      setSaving(true);
      await purchaseOrderAPI.updateOrder(orderId!, {
        supplier_order_number: metadataSupplierOrderNumber,
        sales_order_number: metadataSalesOrderNumber,
        expected_delivery_date: metadataExpectedDelivery || null,
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
          <span className="info-label">Order Date:</span>
          <span className="info-value">{formatDate(order.order_date)}</span>
        </div>
        <div className="info-item">
          <span className="info-label">Expected Delivery:</span>
          <span className="info-value">{formatDate(order.expected_delivery_date)}</span>
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
          <span className="info-label">Estimated Total:</span>
          <span className="info-value">{formatCurrency(order.estimated_total)}</span>
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
              <label htmlFor="metadata-expected-delivery">
                Expected Delivery Date
                <input
                  id="metadata-expected-delivery"
                  type="date"
                  value={metadataExpectedDelivery}
                  onChange={(e) => setMetadataExpectedDelivery(e.target.value)}
                />
              </label>
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
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {order.items.length === 0 ? (
              <tr>
                <td colSpan={9} className="no-data">
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
                    {item.item_type === 'asset' && item.asset_details?.location_name && (
                      <div style={{ fontSize: '0.875rem', color: '#64748b', marginTop: '0.25rem' }}>
                        Location: {item.asset_details.location_name}
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
        </table>
      </section>
      </div>
    </WorkspacePage>
  );
};

export default PurchaseOrderPage;

