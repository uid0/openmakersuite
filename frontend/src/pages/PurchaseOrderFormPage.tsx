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
  ReorderDataAsset,
  ReorderDataItem,
  ReorderDataSupplier,
} from '../services/api';
import '../styles/PurchaseOrderFormPage.css';

interface SelectedItem extends ReorderDataItem {
  selected: boolean;
  quantity: number;
  unit_cost_override?: string;
  expected_shipment_date?: string;
}

interface SelectedAsset extends ReorderDataAsset {
  selected: boolean;
  quantity: number;
  unit_cost: string;
}

interface FreeformItem {
  id: string;
  description: string;
  quantity: number;
  unit_cost: string;
}

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

  // Line items state
  const [selectedItems, setSelectedItems] = useState<SelectedItem[]>([]);
  const [selectedAssets, setSelectedAssets] = useState<SelectedAsset[]>([]);
  const [freeformItems, setFreeformItems] = useState<FreeformItem[]>([]);

  // Manual product entry state
  const [productSearchTerm, setProductSearchTerm] = useState('');
  const [productBarcode, setProductBarcode] = useState('');
  const [searchingProduct, setSearchingProduct] = useState(false);

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
      setError(err.response?.data?.detail || err.message || 'Failed to load reorder data');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadReorderData();
  }, [loadReorderData]);

  // When a supplier is selected, populate items and assets
  useEffect(() => {
    if (selectedSupplierId) {
      const supplier = suppliers.find((s) => s.id === selectedSupplierId);
      if (supplier) {
        // Initialize items with all selected by default
        setSelectedItems(
          supplier.items.map((item) => ({
            ...item,
            selected: true,
            quantity: item.suggested_quantity,
          }))
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
        // Clear freeform items when changing supplier
        setFreeformItems([]);
      }
    } else {
      setSelectedItems([]);
      setSelectedAssets([]);
      setFreeformItems([]);
    }
  }, [selectedSupplierId, suppliers]);

  const selectedSupplier = suppliers.find((s) => s.id === selectedSupplierId);

  // Toggle item selection
  const toggleItemSelection = (itemSupplierId: number) => {
    setSelectedItems((prev) =>
      prev.map((item) =>
        item.item_supplier_id === itemSupplierId ? { ...item, selected: !item.selected } : item
      )
    );
  };

  // Update item quantity
  const updateItemQuantity = (itemSupplierId: number, quantity: number) => {
    setSelectedItems((prev) =>
      prev.map((item) =>
        item.item_supplier_id === itemSupplierId ? { ...item, quantity: Math.max(1, quantity) } : item
      )
    );
  };

  // Update item price override
  const updateItemPrice = (itemSupplierId: number, unitCostOverride: string) => {
    setSelectedItems((prev) =>
      prev.map((item) =>
        item.item_supplier_id === itemSupplierId ? { ...item, unit_cost_override: unitCostOverride } : item
      )
    );
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
  const handleProductSearch = async () => {
    if (!selectedSupplierId) {
      setError('Please select a supplier first');
      return;
    }

    setSearchingProduct(true);
    setError(null);

    try {
      let inventoryItem;
      let itemId: string;

      // Try barcode lookup first if barcode is provided
      if (productBarcode.trim()) {
        try {
          const lookupResponse = await inventoryAPI.lookupByCode(productBarcode.trim().toUpperCase());
          if (lookupResponse.data.type === 'item') {
            itemId = lookupResponse.data.id;
          } else {
            setError('Barcode does not match an inventory item');
            setSearchingProduct(false);
            return;
          }
        } catch (err: any) {
          setError(err.response?.data?.error || 'Barcode not found');
          setSearchingProduct(false);
          return;
        }
      } else if (productSearchTerm.trim()) {
        // Search by name or SKU
        const searchResponse = await inventoryAPI.listItems({ search: productSearchTerm.trim() });
        const items = searchResponse.data.results;
        if (items.length === 0) {
          setError('No products found matching your search');
          setSearchingProduct(false);
          return;
        }
        if (items.length > 1) {
          setError(`Multiple products found. Please be more specific or use barcode scan.`);
          setSearchingProduct(false);
          return;
        }
        itemId = items[0].id;
      } else {
        setError('Please enter a product name/SKU or scan a barcode');
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
      const newItem: SelectedItem = {
        item_supplier_id: matchingItemSupplier.id,
        item_id: inventoryItem.id,
        item_name: inventoryItem.name,
        item_sku: inventoryItem.sku || '',
        current_stock: inventoryItem.current_stock || 0,
        minimum_stock: inventoryItem.minimum_stock || 0,
        reorder_quantity: inventoryItem.reorder_quantity || 0,
        suggested_quantity: matchingItemSupplier.quantity_per_package || 1,
        unit_cost: matchingItemSupplier.unit_cost?.toString() || '0',
        package_cost: matchingItemSupplier.package_cost?.toString() || null,
        quantity_per_package: matchingItemSupplier.quantity_per_package || 1,
        lead_time_days: matchingItemSupplier.average_lead_time || 7,
        supplier_sku: matchingItemSupplier.supplier_sku || '',
        supplier_url: matchingItemSupplier.supplier_url || '',
        is_primary: matchingItemSupplier.is_primary || false,
        line_total: (parseFloat(matchingItemSupplier.unit_cost?.toString() || '0') * (matchingItemSupplier.quantity_per_package || 1)).toString(),
        selected: true,
        quantity: matchingItemSupplier.quantity_per_package || 1,
      };

      setSelectedItems((prev) => [...prev, newItem]);
      setProductSearchTerm('');
      setProductBarcode('');
    } catch (err: any) {
      console.error('Error searching for product:', err);
      setError(err.response?.data?.error || err.response?.data?.detail || err.message || 'Failed to find product');
    } finally {
      setSearchingProduct(false);
    }
  };

  // Calculate totals
  const itemsTotal = selectedItems
    .filter((item) => item.selected)
    .reduce((sum, item) => {
      const unitCost = item.unit_cost_override ? parseFloat(item.unit_cost_override) : parseFloat(item.unit_cost);
      return sum + (isNaN(unitCost) ? 0 : unitCost) * item.quantity;
    }, 0);

  const assetsTotal = selectedAssets
    .filter((asset) => asset.selected && asset.unit_cost)
    .reduce((sum, asset) => sum + parseFloat(asset.unit_cost || '0') * asset.quantity, 0);

  const freeformTotal = freeformItems
    .filter((item) => item.description && item.unit_cost)
    .reduce((sum, item) => sum + parseFloat(item.unit_cost || '0') * item.quantity, 0);

  const grandTotal = itemsTotal + assetsTotal + freeformTotal;

  const selectedItemCount = selectedItems.filter((item) => item.selected).length;
  const selectedAssetCount = selectedAssets.filter((asset) => asset.selected).length;
  const freeformItemCount = freeformItems.filter((item) => item.description && item.unit_cost).length;
  const totalLineItems = selectedItemCount + selectedAssetCount + freeformItemCount;

  // Validate form
  const canSubmit =
    selectedSupplierId &&
    totalLineItems > 0 &&
    !submitting &&
    // Check that all selected assets have unit_cost
    selectedAssets.filter((a) => a.selected).every((a) => a.unit_cost && parseFloat(a.unit_cost) > 0) &&
    // Check that all freeform items have description and unit_cost
    freeformItems.every(
      (item) =>
        !item.description || (item.description && item.unit_cost && parseFloat(item.unit_cost) >= 0)
    );

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
        ...(expectedDeliveryDate && { expected_delivery_date: expectedDeliveryDate }),
        ...(notes && { notes }),
      };

      const response = await purchaseOrderAPI.createOrder(orderData);
      const orderId = response.data.id;

      // Navigate to the created order
      navigate(`/purchasing/orders/${orderId}`);
    } catch (err: any) {
      console.error('Error creating purchase order:', err);
      setError(err.response?.data?.detail || err.response?.data?.error || err.message || 'Failed to create purchase order');
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
                      <span className="stat-value">{formatCurrency(supplier.estimated_total)}</span>
                      <span className="stat-label">estimated total</span>
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
        </section>

        {selectedSupplier && (
          <>
            {/* Inventory Items */}
            <section className="form-section items-section">
              <h2>2. Inventory Items ({selectedItems.length})</h2>
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
                            />
                          </td>
                          <td>
                            <div className="item-info">
                              <div className="item-name-row">
                                <span className="item-name">{item.item_name}</span>
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
                            <input
                              type="number"
                              min="1"
                              value={item.quantity}
                              onChange={(e) =>
                                updateItemQuantity(item.item_supplier_id, parseInt(e.target.value, 10) || 1)
                              }
                              disabled={!item.selected}
                            />
                          </td>
                          <td className="col-cost">
                            <input
                              type="number"
                              min="0"
                              step="0.01"
                              placeholder={item.unit_cost}
                              value={item.unit_cost_override || ''}
                              onChange={(e) => updateItemPrice(item.item_supplier_id, e.target.value)}
                              disabled={!item.selected}
                              className="input-cost"
                            />
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
                              ? formatCurrency(
                                  (item.unit_cost_override ? parseFloat(item.unit_cost_override) : parseFloat(item.unit_cost)) * item.quantity
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
                                  handleProductSearch();
                                }
                              }}
                              className="input-barcode"
                              style={{ flex: 1 }}
                            />
                            <button
                              type="button"
                              onClick={handleProductSearch}
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
                <div className="form-field">
                  <label htmlFor="expected-delivery">Expected Delivery Date</label>
                  <input
                    id="expected-delivery"
                    type="date"
                    value={expectedDeliveryDate}
                    onChange={(e) => setExpectedDeliveryDate(e.target.value)}
                  />
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

            {/* Order Summary */}
            <section className="form-section summary-section">
              <div className="order-summary">
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
                  <span>{formatCurrency(grandTotal)}</span>
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

