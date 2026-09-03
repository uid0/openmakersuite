/**
 * QR Code Scan Page
 * Shows item details and allows users to submit reorder requests
 * - Non-logged users: Simple reorder → thanks page
 * - Logged users: Supplier selection with cost optimization
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { checklistsAPI, inventoryAPI, reorderAPI } from '../services/api';
import '../styles/ScanPage.css';
import { Checklist, InventoryItem, ItemSupplier } from '../types';
import { formatDateOnly } from '../utils/dates';
import { promptInput, showError } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';
import { reorderQuantityLabel } from '../utils/packaging';
import {
  alternativeSupplierNamesText,
  chosenSupplierName,
  publicSupplierChoiceNote,
  supplierChoiceNote,
} from '../utils/supplierChoice';

/** The page's one phrasing for a pack size it cannot count with. */
const PACK_SIZE_UNKNOWN = '— (case size unknown)';

/**
 * Base units in this vendor's package, or null when the row records none we can
 * multiply by. The web face of `inventory.services.pack_size`: a
 * `quantity_per_package` of 0 is `PACK_SIZE_RECORDED_ZERO` — a box holding no
 * units — and this form is the FIRST place that state's prescribed operator
 * action (correct the row, or buy from a vendor that records one) reaches a
 * human, so {@link packSizeRefusal} words it.
 */
const packSizeOf = (supplier: ItemSupplier): number | null => {
  const units = supplier.quantity_per_package;
  return typeof units === 'number' && units >= 1 ? units : null;
};

/** The cause and the remedy — a refusal an operator cannot act on is not a fix. */
const packSizeRefusal = (supplier: ItemSupplier): string =>
  `${supplier.supplier_name} records a pack size of ${supplier.quantity_per_package} — ` +
  'a box holding no units — so the number of units in a package is unknown and no ' +
  'reorder can be sized from it. Correct "Quantity per Package" on this supplier ' +
  'relationship, or choose a different supplier that records one.';

/** The page's one phrasing for a price this vendor has not recorded. */
const PRICE_UNKNOWN = '— (no price on file)';

/**
 * What this row charges, or null when it records nothing we can multiply by.
 *
 * The web face of `inventory.services.pricing` (op-9m2v): a recorded `"0.00"` is
 * a KNOWN price — a makerspace runs on donated stock — and only a genuine
 * absence is null. `parseFloat(cost || '0')` cannot tell the two apart and
 * turned "nobody priced this" into a confident $0.00 on a member-facing screen.
 *
 * Takes numbers as well as strings because this page reads both wire types:
 * `supplier.unit_cost` is a `DecimalField` and arrives as `"0.00"`, while
 * `item.unit_cost` is a property-backed `ReadOnlyField` and arrives as `0`.
 */
const priceOf = (cost: string | number | null | undefined): number | null => {
  if (cost === null || cost === undefined) return null;
  const amount = typeof cost === 'number' ? cost : parseFloat(cost);
  return Number.isFinite(amount) ? amount : null;
};

/** A price as money, or the page's phrasing for its absence. */
const money = (cost: string | number | null | undefined): string => {
  const amount = priceOf(cost);
  return amount === null ? PRICE_UNKNOWN : `$${amount.toFixed(2)}`;
};

/**
 * What the member can do about a price nobody recorded.
 *
 * Informational, NOT a refusal: an unpriced request is still a legitimate
 * request — unlike an unknown pack size, which cannot be sized at all. But a
 * blank the reader cannot act on is not a fix either, so it says who to ask.
 */
const priceUnknownNote = (supplier: ItemSupplier): string =>
  `${supplier.supplier_name} has no package cost on file, so this request cannot ` +
  'be costed. It can still be submitted — add a package cost to that supplier ' +
  'relationship if you need an estimate first.';

const ScanPage: React.FC = () => {
  const { itemId } = useParams<{ itemId: string }>();
  const navigate = useNavigate();

  // Authentication state
  const [isLoggedIn] = useState<boolean>(() => !!localStorage.getItem('token'));

  // Data state
  const [item, setItem] = useState<InventoryItem | null>(null);
  const [suppliers, setSuppliers] = useState<ItemSupplier[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [checklists, setChecklists] = useState<Checklist[]>([]);

  // Form state
  const [requestedBy, setRequestedBy] = useState('');
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  // Enhanced form state (logged in users)
  const [selectedSupplier, setSelectedSupplier] = useState<ItemSupplier | null>(null);
  const [packageQuantity, setPackageQuantity] = useState<number>(1);
  const [totalUnits, setTotalUnits] = useState<number>(0);
  const [estimatedCost, setEstimatedCost] = useState<number | null>(0);
  const [estimatedLeadTime, setEstimatedLeadTime] = useState<number>(0);

  // Update calculations when supplier or quantity changes
  const updateCalculations = useCallback((supplier: ItemSupplier, packages: number) => {
    // An unrecorded pack size is not the number 0: it yields no unit count at
    // all, and the form refuses rather than reporting one.
    const packSize = packSizeOf(supplier);
    const units = packSize === null ? 0 : packages * packSize;
    const packagePrice = priceOf(supplier.package_cost);
    const cost = packagePrice === null ? null : packages * packagePrice;

    setTotalUnits(units);
    setEstimatedCost(cost);
    setEstimatedLeadTime(supplier.average_lead_time);
  }, []);

  const loadChecklists = useCallback(async () => {
    if (!itemId) return;
    try {
      const checklistsResponse = await inventoryAPI.getItemChecklists(itemId);
      setChecklists(checklistsResponse.data);
    } catch (err: any) {
      console.error('Error loading checklists:', err);
    }
  }, [itemId]);

  const loadItem = useCallback(async () => {
    try {
      setLoading(true);
      const itemResponse = await inventoryAPI.getItem(itemId!);
      setItem(itemResponse.data);

      // Load supplier data for logged in users
      if (isLoggedIn) {
        const suppliersResponse = await inventoryAPI.getItemSuppliers(itemId!);
        const supplierList = suppliersResponse.data.results;
        setSuppliers(supplierList);

        // Find the most cost-effective supplier (lowest unit cost)
        if (supplierList.length > 0) {
          const bestSupplier = supplierList
            .filter(s => s.is_active && s.unit_cost)
            .sort((a, b) => parseFloat(a.unit_cost!) - parseFloat(b.unit_cost!))[0];

          if (bestSupplier) {
            setSelectedSupplier(bestSupplier);
            setPackageQuantity(1);
            updateCalculations(bestSupplier, 1);
          }
        }
      }

      setError(null);
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to load item'));
      console.error('Error loading item:', err);
    } finally {
      setLoading(false);
    }
  }, [itemId, isLoggedIn, updateCalculations]);

  useEffect(() => {
    if (itemId) {
      loadItem();
      loadChecklists();
    }
  }, [itemId, loadItem, loadChecklists]);

  const handleStartChecklist = async (checklistId: string) => {
    try {
      // If logged in, use the username from localStorage; otherwise prompt
      let userName: string | undefined;
      if (isLoggedIn) {
        userName = localStorage.getItem('username') || undefined;
      } else {
        const promptResult = await promptInput('Start checklist', 'Enter your name (optional)');
        userName = promptResult || undefined;
      }
      const completion = await checklistsAPI.startChecklist(checklistId, userName);
      navigate(`/checklist/${checklistId}/complete/${completion.data.id}`);
    } catch (err: any) {
      showError(extractErrorMessage(err, 'Failed to start checklist'));
    }
  };

  // Auto-submit reorder for non-logged users (only if no pending request exists)
  //
  // KNOWN DEFECT, pre-existing and deliberately out of scope for op-3xsp: the
  // catch below clears `submitting`, which is itself a dependency, so a failed
  // submit re-enters this effect for as long as the page is open. Measured in
  // jsdom against a rejection delayed 5 ms: 19 calls to `reorderAPI.createRequest`
  // in 150 ms. Latching it to one attempt was tried and reverted — an anonymous
  // visitor has no manual submit path (`handleSubmitReorder` returns early on
  // `!isLoggedIn` and the form below is `isLoggedIn`-gated), so a latch trades
  // the retry storm for a silently dropped reorder. Which of the two is worse
  // is a product decision this change is not authorised to make.
  useEffect(() => {
    const autoSubmitReorder = async () => {
      if (!isLoggedIn && item && !submitting && !submitted) {
        // Check if item already has a pending reorder request
        if (item.has_pending_reorder) {
          // Don't auto-submit, just set submitted to show the existing request message
          setSubmitted(true);
          return;
        }

        try {
          setSubmitting(true);
          await reorderAPI.createRequest({
            item: item.id,
            quantity: item.reorder_quantity,
            requested_by: 'Anonymous',
            request_notes: 'Auto-submitted via QR scan',
            priority: item.needs_reorder ? 'high' : 'normal',
          });

          // Redirect to thanks page immediately
          navigate('/thanks');
        } catch (err: any) {
          console.error('Error auto-submitting reorder:', err);
          // On error, show the form so user can manually submit
          setSubmitting(false);
        }
      }
    };

    autoSubmitReorder();
  }, [isLoggedIn, item, submitting, submitted, navigate]);

  // Handle supplier selection change
  const handleSupplierChange = (supplierId: number) => {
    const supplier = suppliers.find(s => s.id === supplierId);
    if (supplier) {
      setSelectedSupplier(supplier);
      updateCalculations(supplier, packageQuantity);
    }
  };

  // Handle package quantity change
  const handlePackageQuantityChange = (quantity: number) => {
    setPackageQuantity(quantity);
    if (selectedSupplier) {
      updateCalculations(selectedSupplier, quantity);
    }
  };

  const handleSubmitReorder = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!item || !isLoggedIn) return;

    // Logged in user: Use supplier selection and package quantities
    if (!selectedSupplier) {
      showError('Please select a supplier.');
      return;
    }

    // Never POST a quantity derived from a pack size we do not have: `packages
    // * 0` would discard the package count the operator typed and file a
    // 0-unit request. Refuse, naming the remedy `pack_size.py` prescribes.
    if (packSizeOf(selectedSupplier) === null) {
      showError(packSizeRefusal(selectedSupplier));
      return;
    }

    try {
      setSubmitting(true);

      await reorderAPI.createRequest({
        item: item.id,
        quantity: totalUnits,
        requested_by: requestedBy || 'User',
        request_notes: notes,
        priority: item.needs_reorder ? 'high' : 'normal',
        preferred_supplier: selectedSupplier.id,
        package_quantity: packageQuantity,
      });

      setSubmitted(true);
      setTimeout(() => {
        navigate('/');
      }, 3000);
    } catch (err: any) {
      showError(extractErrorMessage(err, 'Failed to submit reorder request'));
      console.error('Error submitting reorder:', err);
    } finally {
      setSubmitting(false);
    }
  };

  // Pack size of the supplier the form would order through, and whether it is
  // one we can count with. Every unit figure below reads these, so the page
  // cannot print a confident number beside a refusal that says it has none.
  const selectedPackSize = selectedSupplier ? packSizeOf(selectedSupplier) : null;
  const packSizeUnknown = selectedSupplier !== null && selectedPackSize === null;

  // The supplier the SERVER says this item is bought from, and everything
  // qualifying that answer (op-3xsp). Read through `utils/supplierChoice` so
  // this page words it the same way the CSV export and the reorder queue do,
  // and so the page owns none of the derivation.
  const supplierName = chosenSupplierName(item?.supplier_choice);
  // This route is public, so everything the block renders has an AUDIENCE. A
  // signed-in operator gets the other suppliers by name and the derivation
  // caveats; a logged-out scanner gets neither — only the fact that there is
  // nothing to order, worded to name no vendor and describe no link. The page
  // picks which reader to ask and renders the answer; the wording, the joining
  // and the emptiness tests all belong to `utils/supplierChoice`, not here.
  const alternativeText = isLoggedIn
    ? alternativeSupplierNamesText(item?.supplier_choice)
    : null;
  const supplierNote = isLoggedIn
    ? supplierChoiceNote(item?.supplier_choice)
    : publicSupplierChoiceNote(item?.supplier_choice);

  if (loading) {
    return (
      <div className="scan-page">
        <div className="loading">Loading item details...</div>
      </div>
    );
  }

  // Show submitting state for non-logged users
  if (!isLoggedIn && submitting) {
    return (
      <div className="scan-page">
        <div className="loading">
          <h2>🔄 Submitting Reorder Request</h2>
          <p>Please wait while we process your request...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="scan-page">
        <div className="error">
          <h2>Error</h2>
          <p>{error}</p>
          <button onClick={() => navigate('/')}>Go Home</button>
        </div>
      </div>
    );
  }

  if (!item) {
    return (
      <div className="scan-page">
        <div className="error">
          <h2>Item not found</h2>
          <button onClick={() => navigate('/')}>Go Home</button>
        </div>
      </div>
    );
  }

  if (submitted) {
    // Check if we have an existing reorder request
    if (item?.has_pending_reorder && item?.active_reorder_request) {
      const request = item.active_reorder_request;
      const statusMessage = {
        'pending': 'Your reorder request is pending admin approval',
        'approved': 'Your reorder request has been approved and will be ordered soon',
        'ordered': `Your reorder was placed on ${new Date(request.ordered_at || '').toLocaleDateString()}`
      }[request.status] || 'Your reorder request is being processed';

      return (
        <div className="scan-page">
          <div className="info">
            <h2>ℹ️ Reorder Already Requested</h2>
            <p><strong>{item.name}</strong> already has a reorder request in progress.</p>
            <div className="request-details">
              <p><strong>Status:</strong> {request.status.charAt(0).toUpperCase() + request.status.slice(1)}</p>
              <p><strong>Quantity:</strong> {request.quantity} units</p>
              <p><strong>Requested:</strong> {new Date(request.requested_at).toLocaleDateString()}</p>
              {request.requested_by && <p><strong>Requested by:</strong> {request.requested_by}</p>}
              {item.expected_delivery_date && (
                <p><strong>Expected Delivery:</strong> {formatDateOnly(item.expected_delivery_date)}</p>
              )}
            </div>
            <p className="status-message">{statusMessage}</p>
            <p className="redirect-message">Redirecting to home...</p>
          </div>
        </div>
      );
    }

    return (
      <div className="scan-page">
        <div className="success">
          <h2>✓ Reorder Request Submitted</h2>
          <p>Your request for <strong>{item.name}</strong> has been submitted.</p>
          <p>An administrator will review and process it soon.</p>
          <p className="redirect-message">Redirecting to home...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="scan-page">
      <div className="item-card">
        <div className="item-header">
          {item.image && (
            <img src={item.image} alt={item.name} className="item-image" />
          )}
          <div className="item-title-section">
            <h1>{item.name}</h1>
            {item.sku && <p className="sku">SKU: {item.sku}</p>}
          </div>
        </div>

        {/* Checklists Section */}
        {checklists.length > 0 && (
          <div className="checklists-section" style={{ marginTop: '20px', padding: '15px', border: '1px solid #ddd', borderRadius: '5px' }}>
            <h3>Are you completing a checklist?</h3>
            <p>This item is part of the following checklists:</p>
            <ul style={{ listStyle: 'none', padding: 0 }}>
              {checklists.map((checklist) => (
                <li key={checklist.id} style={{ marginBottom: '10px' }}>
                  <button
                    onClick={() => handleStartChecklist(checklist.id)}
                    style={{
                      width: '100%',
                      padding: '10px',
                      backgroundColor: '#007bff',
                      color: 'white',
                      border: 'none',
                      borderRadius: '4px',
                      cursor: 'pointer',
                    }}
                  >
                    {checklist.name}
                    {checklist.step_count && ` (${checklist.step_count} steps)`}
                  </button>
                  {checklist.description && (
                    <p style={{ marginTop: '5px', fontSize: '0.9em', color: '#666' }}>{checklist.description}</p>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="item-details">
          <p className="description">{item.description}</p>

          <div className="info-grid">
            <div className="info-item">
              <span className="label">Location:</span>
              <span className="value">{item.location}</span>
            </div>

            {item.use_case_based_reorder ? (
              // Case-based display
              <>
                <div className="info-item">
                  <span className="label">Current Cases:</span>
                  <span className={`value ${item.needs_reorder ? 'low-stock' : ''}`}>
                    {item.current_cases === null
                      ? '— (case size unknown)'
                      : `${item.current_cases.toFixed(1)} cases`}
                  </span>
                </div>
                <div className="info-item">
                  <span className="label">Current Units:</span>
                  <span className="value secondary">
                    {item.current_stock} units
                  </span>
                </div>
                <div className="info-item">
                  <span className="label">Reorder Quantity:</span>
                  <span className="value">{reorderQuantityLabel(item)}</span>
                </div>
              </>
            ) : (
              // Traditional unit-based display
              <>
                <div className="info-item">
                  <span className="label">Current Stock:</span>
                  <span className={`value ${item.needs_reorder ? 'low-stock' : ''}`}>
                    {item.current_stock} units
                  </span>
                </div>
                <div className="info-item">
                  <span className="label">Reorder Quantity:</span>
                  <span className="value">{reorderQuantityLabel(item)}</span>
                </div>
              </>
            )}

            {/* Supplier, lead time and unit cost are ONE supplier's facts, so
                they are labelled as that supplier's and read off
                `supplier_choice` rather than the flat legacy keys (op-3xsp).
                The flats gave a bare name, and this block rendered it as
                "Supplier: Acme" for an item stocked by three — with the lead
                time and the price beside it reading as the item's own numbers.
                Every sentence below comes from the server's own answer; nothing
                here ranks or filters links. */}
            {supplierName && (
              <>
                <div className="info-item" data-testid="supplier-choice-name">
                  <span className="label">We order this from:</span>
                  <span className="value">
                    {supplierName}
                    {/* SIGNED-IN ONLY, and with no anonymous substitute. This
                        route is not behind RequireAuth and serves logged-out QR
                        scanners, who get the chosen supplier's name, lead time
                        and price — exactly what they saw before this field
                        existed, and no indication that any other vendor exists.
                        Not the roster, and not a count of it either: a count is
                        authorised on the item detail page and nowhere else, and
                        widening anonymous disclosure is the requester's to
                        grant, not a nearby surface's to infer by analogy. */}
                    {alternativeText !== null && (
                      <span className="value secondary" data-testid="supplier-choice-alternatives">
                        {' '}
                        — also available from {alternativeText}
                      </span>
                    )}
                  </span>
                </div>

                <div className="info-item">
                  <span className="label">Their Lead Time:</span>
                  <span className="value">{item.average_lead_time} days</span>
                </div>

                <div className="info-item">
                  <span className="label">Their Unit Cost:</span>
                  <span className="value">{money(item.unit_cost)}</span>
                </div>
              </>
            )}

            {/* Last, because it QUALIFIES the three rows above — or, where
                there is no supplier at all, replaces them and says which kind
                of nothing this is. */}
            {supplierNote && (
              <div className="info-item supplier-note" data-testid="supplier-choice-note">
                <span className="label">{supplierName ? 'Before you order:' : 'Supplier:'}</span>
                <span className="value secondary">{supplierNote}</span>
              </div>
            )}
          </div>

          {item.needs_reorder && (
            <div className="alert alert-warning">
              <strong>⚠ Low Stock Alert</strong>
              <p>This item is below minimum stock level and needs reordering.</p>
            </div>
          )}
        </div>

        {!isLoggedIn && !submitting && (
          <div className="auto-submit-message">
            <h2>🔄 Processing Reorder Request</h2>
            <p>We're automatically submitting a reorder request for <strong>
              {reorderQuantityLabel(item)}
            </strong> of this item.</p>
            <p>You'll be redirected to a confirmation page shortly...</p>
          </div>
        )}

        {isLoggedIn && item.has_pending_reorder && item.active_reorder_request && (
          <div className="existing-request-info">
            <h2>ℹ️ Reorder In Progress</h2>
            <p>This item already has a reorder request:</p>
            <div className="request-summary">
              <p><strong>Status:</strong> {item.active_reorder_request.status.charAt(0).toUpperCase() + item.active_reorder_request.status.slice(1)}</p>
              <p><strong>Quantity:</strong> {item.active_reorder_request.quantity} units</p>
              <p><strong>Requested:</strong> {new Date(item.active_reorder_request.requested_at).toLocaleDateString()}</p>
              {item.active_reorder_request.requested_by && (
                <p><strong>Requested by:</strong> {item.active_reorder_request.requested_by}</p>
              )}
              {item.expected_delivery_date && (
                <p><strong>Expected Delivery:</strong> {formatDateOnly(item.expected_delivery_date)}</p>
              )}
            </div>
          </div>
        )}

        {isLoggedIn && !item.has_pending_reorder && (
          <form onSubmit={handleSubmitReorder} className="reorder-form">
            <h2>Request Reorder</h2>

            {suppliers.length > 0 && (
                <div className="form-group">
                  <label htmlFor="supplierSelect">Supplier</label>
                  <select
                    id="supplierSelect"
                    value={selectedSupplier?.id || ''}
                    onChange={(e) => handleSupplierChange(Number(e.target.value))}
                    required
                  >
                    <option value="">Select a supplier...</option>
                    {suppliers
                      .filter(s => s.is_active)
                      .sort((a, b) => parseFloat(a.unit_cost || '999') - parseFloat(b.unit_cost || '999'))
                      .map(supplier => (
                        <option key={supplier.id} value={supplier.id}>
                          {supplier.supplier_name} - {money(supplier.unit_cost)}/unit
                          {packSizeOf(supplier) === null
                            ? ` ${PACK_SIZE_UNKNOWN}`
                            : ` (${packSizeOf(supplier)} per package)`}
                          {supplier.package_dimensions_display !== 'No dimensions specified' &&
                            ` - ${supplier.package_dimensions_display}`}
                        </option>
                      ))}
                  </select>
                </div>
              )}

              {selectedSupplier && (
                <>
                  <div className="supplier-details">
                    <h3>Package Details</h3>
                    <div className="detail-grid">
                      <div>
                        <strong>Units per package:</strong>{' '}
                        {selectedPackSize === null ? PACK_SIZE_UNKNOWN : selectedPackSize}
                      </div>
                      <div>
                        <strong>Package cost:</strong> {money(selectedSupplier.package_cost)}
                      </div>
                      <div>
                        <strong>Unit cost:</strong> {money(selectedSupplier.unit_cost)}
                      </div>
                      <div>
                        <strong>Lead time:</strong> {selectedSupplier.average_lead_time} days
                      </div>
                      {selectedSupplier.package_dimensions_display !== 'No dimensions specified' && (
                        <div>
                          <strong>Dimensions:</strong> {selectedSupplier.package_dimensions_display}
                        </div>
                      )}
                    </div>
                  </div>

                  {packSizeUnknown && (
                    <div className="alert alert-warning" role="alert">
                      {packSizeRefusal(selectedSupplier)}
                    </div>
                  )}

                  {estimatedCost === null && (
                    <div className="alert alert-info" role="status">
                      {priceUnknownNote(selectedSupplier)}
                    </div>
                  )}

                  <div className="form-group">
                    <label htmlFor="packageQuantity">Number of Packages</label>
                    <input
                      type="number"
                      id="packageQuantity"
                      value={packageQuantity}
                      onChange={(e) => handlePackageQuantityChange(Number(e.target.value))}
                      min="1"
                      required
                    />
                    <small className="form-help">
                      = {packSizeUnknown ? PACK_SIZE_UNKNOWN : `${totalUnits} total units`} (
                      {estimatedCost === null
                        ? `${PRICE_UNKNOWN} — estimated cost unknown`
                        : `$${estimatedCost.toFixed(2)} estimated cost`}
                      )
                    </small>
                  </div>

                  <div className="order-summary">
                    <h3>Order Summary</h3>
                    <div className="summary-item">
                      <span>Total Units:</span>
                      <span><strong>{packSizeUnknown ? PACK_SIZE_UNKNOWN : `${totalUnits} units`}</strong></span>
                    </div>
                    <div className="summary-item">
                      <span>Estimated Cost:</span>
                      <span>
                        <strong>
                          {estimatedCost === null
                            ? PRICE_UNKNOWN
                            : `$${estimatedCost.toFixed(2)}`}
                        </strong>
                      </span>
                    </div>
                    <div className="summary-item">
                      <span>Estimated Lead Time:</span>
                      <span><strong>{estimatedLeadTime} days</strong></span>
                    </div>
                  </div>
                </>
              )}

              <div className="form-group">
                <label htmlFor="requestedBy">Your Name</label>
                <input
                  type="text"
                  id="requestedBy"
                  value={requestedBy}
                  onChange={(e) => setRequestedBy(e.target.value)}
                  placeholder="Enter your name"
                />
              </div>

              <div className="form-group">
                <label htmlFor="notes">Notes (Optional)</label>
                <textarea
                  id="notes"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="Any additional information..."
                  rows={3}
                />
              </div>

              <button
                type="submit"
                className="submit-button"
                disabled={submitting || !selectedSupplier || packSizeUnknown}
              >
                {submitting
                  ? 'Submitting...'
                  : packSizeUnknown
                    ? 'Cannot request — case size unknown'
                    : `Request ${totalUnits} Units`}
              </button>
          </form>
        )}
      </div>
    </div>
  );
};

export default ScanPage;
