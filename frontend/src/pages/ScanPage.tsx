/**
 * QR Code Scan Page
 * Shows item details and allows users to submit reorder requests
 * - Non-logged users: Simple reorder → thanks page
 * - Logged users: Supplier selection with cost optimization
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { checklistsAPI, inventoryAPI, reorderAPI } from '../services/api';
import '../styles/ScanPage.css';
import { Checklist, InventoryItem, ItemSupplier } from '../types';
import { formatDateOnly } from '../utils/dates';
import { promptInput, showError } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';
import { reorderFiling, reorderQuantityLabel } from '../utils/packaging';
import {
  alternativeSupplierNamesText,
  chosenSupplierName,
  publicSupplierChoiceNote,
  supplierChoiceNote,
} from '../utils/supplierChoice';

/**
 * How many times an anonymous scan tries to file its reorder before it stops
 * and says so. Bounded because the member cannot retry by hand — see the
 * auto-submit effect below.
 */
const AUTO_SUBMIT_ATTEMPTS = 3;

/** Gap before retry n: n × this, so the attempts spread rather than burst. */
const AUTO_SUBMIT_RETRY_MS = 400;

/**
 * What a logged-out member is told when the reorder could not be filed.
 *
 * Names the item, says plainly that NOTHING was ordered, and gives an action
 * they already have (reload, or ask someone). It deliberately offers no retry
 * control: an anonymous visitor has none today, and adding one would change
 * what such a visitor can DO rather than what they are told.
 */
const autoSubmitFailureNote = (itemName: string, detail: string): string =>
  `We could not submit a reorder request for ${itemName} after ` +
  `${AUTO_SUBMIT_ATTEMPTS} attempts, so nothing has been ordered. ${detail} ` +
  'Reload this page to try again, or ask a member of staff to add it to the ' +
  'reorder queue.';

/** What that member is told when the page cannot learn what it would file. */
const FILING_UNKNOWN_NOTE =
  'This item did not tell us how much a reorder should order, so nothing has ' +
  'been submitted — filing a guessed quantity would be worse than filing ' +
  'nothing. Reload this page to try again, or ask a member of staff to add it ' +
  'to the reorder queue.';

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
  // The terminal state of a logged-out member's auto-submit when it did NOT
  // file: the sentence they are shown instead of a silent stall.
  const [autoSubmitFailure, setAutoSubmitFailure] = useState<string | null>(null);
  // Single-flight guard for that auto-submit, keyed by the item it fired for, so
  // a re-render can never re-enter it and a genuine route change still can.
  const autoSubmitStartedForRef = useRef<string | null>(null);
  const autoSubmitAbandonedRef = useRef(false);

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

  // Auto-submit reorder for non-logged users (only if no pending request exists).
  //
  // The one place this page acts on a member's behalf, so it owes them two
  // things, and it used to get both wrong.
  //
  // WHAT IT FILES. The quantity is `reorderFiling(item)` — the server's own
  // `reorder_display.order_quantity`, in the BASE units a request is stored in
  // — and the page prints the wording that comes with it, so the number a
  // member reads and the number that is filed are one value. It POSTed the raw
  // `item.reorder_quantity` column, which for a pack-counting item is a count
  // of PACKS: the screen said "3 cases" and three bottles were ordered. When
  // the payload carries no such answer the page files NOTHING and says so;
  // guessing is what the old code did.
  //
  // HOW OFTEN IT FILES. It is bounded to AUTO_SUBMIT_ATTEMPTS tries and ends in
  // exactly one of: filed (→ /thanks), already pending, or a stated failure.
  // Base cleared `submitting` in the catch while `submitting` was a dependency,
  // so a failed submit re-entered the effect for as long as the page was open —
  // 19 POSTs to the public endpoint in 150 ms against a rejection delayed 5 ms.
  // Latching it to a single attempt was tried and reverted, correctly: an
  // anonymous visitor has NO manual submit path (`handleSubmitReorder` returns
  // early on `!isLoggedIn`, and the form is `isLoggedIn`-gated), so a bare latch
  // parks them on "submitting" with nothing filed. Neither the storm nor the
  // silent drop is on the table now: the retries live inside ONE awaited loop
  // rather than in the dependency array, and the last failure is rendered.
  // `ScanPage.test.tsx` pins the bound, the wording and the no-drop guarantee.
  useEffect(() => {
    // A StrictMode remount re-runs this effect after its own cleanup. Clearing
    // the flag here — before the single-flight guard returns — hands an attempt
    // that is already in flight back its ability to finish, so the development
    // double-invoke can neither orphan a submit nor fire a second one.
    autoSubmitAbandonedRef.current = false;
    const abandon = () => {
      autoSubmitAbandonedRef.current = true;
    };

    if (isLoggedIn || !item) return abandon;
    if (autoSubmitStartedForRef.current === item.id) return abandon;
    autoSubmitStartedForRef.current = item.id;

    if (item.has_pending_reorder) {
      // Nothing to file; show the existing request instead.
      setSubmitted(true);
      return abandon;
    }

    const filing = reorderFiling(item);
    if (!filing) {
      setAutoSubmitFailure(FILING_UNKNOWN_NOTE);
      return abandon;
    }

    const submitOnce = () =>
      reorderAPI.createRequest({
        item: item.id,
        quantity: filing.quantity,
        requested_by: 'Anonymous',
        request_notes: 'Auto-submitted via QR scan',
        priority: item.needs_reorder ? 'high' : 'normal',
      });

    // The loop carries NO exit condition of its own, deliberately: every way out
    // is a `return` that has already put the page into a terminal state the
    // member can see (redirected, or told). A `for` bound would be a second,
    // silent way out — the loop would simply end, leaving "Submitting…" on
    // screen with nothing filed, which is the drop this whole effect exists to
    // prevent. The bound is the one `attempt >= AUTO_SUBMIT_ATTEMPTS` below.
    (async () => {
      setSubmitting(true);
      for (let attempt = 1; ; attempt += 1) {
        try {
          await submitOnce();
          if (!autoSubmitAbandonedRef.current) navigate('/thanks');
          return;
        } catch (err: any) {
          console.error(
            `Error auto-submitting reorder (attempt ${attempt} of ${AUTO_SUBMIT_ATTEMPTS}):`,
            err
          );
          if (autoSubmitAbandonedRef.current) return;
          if (attempt >= AUTO_SUBMIT_ATTEMPTS) {
            setSubmitting(false);
            setAutoSubmitFailure(
              autoSubmitFailureNote(
                item.name,
                extractErrorMessage(err, 'The request could not be sent.')
              )
            );
            return;
          }
          await new Promise((resolve) => setTimeout(resolve, AUTO_SUBMIT_RETRY_MS * attempt));
        }
      }
    })();

    return abandon;
  }, [isLoggedIn, item, navigate]);

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

  // What a reorder filed FROM THIS PAGE would order, and the page's one wording
  // for it. This page both shows a reorder quantity and files one, so it shows
  // the quantity it files and nothing else: `reorderQuantityLabel` answers a
  // different question (the item's configured amount in its own counting unit)
  // and the two are different numbers for a pack-counting item and for any item
  // well below its minimum. The list and item-detail pages, which file nothing,
  // keep that label. Null when the payload carried no answer — the page then
  // files nothing and says so rather than naming a number it invented.
  const filing = item ? reorderFiling(item) : null;
  const filingLabel = item ? filing?.text ?? reorderQuantityLabel(item) : '';

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

  // Show submitting state for non-logged users. It names the quantity for the
  // same reason the message below does: this screen can be the last thing a
  // member sees before the redirect, so the number in flight is on it.
  if (!isLoggedIn && submitting) {
    return (
      <div className="scan-page">
        <div className="loading">
          <h2>🔄 Submitting Reorder Request</h2>
          <p>Please wait while we submit a request for <strong>{filingLabel}</strong>...</p>
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
                  <span className="value" data-testid="reorder-quantity">{filingLabel}</span>
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
                  <span className="value" data-testid="reorder-quantity">{filingLabel}</span>
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

        {/* The auto-submit's one non-redirect outcome. A logged-out member gets
            here only after the attempts stop without filing, and the block
            says so — it replaced a "Processing… you'll be redirected shortly"
            notice that was the RESTING state of the retry storm, and so was the
            screen a member sat on while nothing was ordered. The in-flight
            wording now lives on the submitting screen above, which names the
            same quantity; there is one story per state. */}
        {!isLoggedIn && autoSubmitFailure && (
          <div className="alert alert-warning" role="alert" data-testid="auto-submit-failed">
            <h2>⚠ Reorder Not Submitted</h2>
            <p>{autoSubmitFailure}</p>
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
