/**
 * Tests for the web's one reading of `supplier_choice` (op-3xsp).
 *
 * These pin the WORDS, not the choice: `supplier_selection.py` decides which
 * supplier wins and `test_supplier_choice_payload.py` pins that it says so on
 * the wire. What is tested here is that the reading never quietly reintroduces
 * the thing being fixed — a single name presented as if it were the only one,
 * or a qualifier the server sent that never reaches the operator.
 */
import { SupplierChoice } from '../../types';
import {
  SUPPLIER_BASIS_LABELS,
  SUPPLIER_CHOICE_UNKNOWN,
  alternativeSupplierNames,
  alternativeSupplierNamesText,
  anonymousAlternativesNote,
  chosenSupplierName,
  supplierChoiceCaveats,
  publicSupplierChoiceNote,
  supplierChoiceNote,
  supplierChoiceSummary,
} from '../../utils/supplierChoice';

const choice = (overrides: Partial<SupplierChoice> = {}): SupplierChoice => ({
  item_supplier_id: 1,
  supplier_name: 'Acme Supplies',
  basis: 'best_scored',
  reason: null,
  flagged_primary_unorderable: false,
  scored_without_price: false,
  scored_without_history: false,
  alternatives: [],
  ...overrides,
});

const others = (...names: string[]) =>
  names.map((supplier_name, index) => ({ id: index + 10, supplier_name }));

describe('chosenSupplierName', () => {
  it('is the supplier the server said we would buy from', () => {
    expect(chosenSupplierName(choice())).toBe('Acme Supplies');
  });

  it('is null when nothing here can be ordered from', () => {
    expect(
      chosenSupplierName(choice({ supplier_name: null, reason: 'none_orderable' }))
    ).toBeNull();
  });

  it('is null — not undefined, not a guess — when the field is absent', () => {
    expect(chosenSupplierName(undefined)).toBeNull();
  });
});

describe('supplierChoiceSummary', () => {
  it('names the chosen supplier alone when it really was the only one', () => {
    expect(supplierChoiceSummary(choice())).toBe('Acme Supplies');
  });

  /**
   * The defect, in one assertion: an item stocked by three suppliers must not
   * render as "Acme Supplies" full stop. That is what the flat `supplier_name`
   * key produced on every surface it fed.
   */
  it('says how many others were on offer', () => {
    expect(supplierChoiceSummary(choice({ alternatives: others('Beta', 'Gamma') }))).toBe(
      'Acme Supplies, or 2 others'
    );
  });

  it('counts a single alternative in the singular', () => {
    expect(supplierChoiceSummary(choice({ alternatives: others('Beta') }))).toBe(
      'Acme Supplies, or 1 other'
    );
  });

  it('is null where there is no supplier to name', () => {
    expect(supplierChoiceSummary(choice({ supplier_name: null, reason: 'no_suppliers' }))).toBeNull();
  });

  /**
   * A payload without the array is not what the current server sends, but this
   * reads a wire object rather than a local one, and its siblings here already
   * tolerate the shape. Throwing would blank the whole cell that called it —
   * KitListPage's "From" column, the admin dashboard's supplier line — rather
   * than losing the "or N others" clause it could not compute.
   */
  it('still names the supplier when the payload carries no alternatives array', () => {
    const withoutAlternatives = { ...choice() } as Partial<SupplierChoice>;
    delete withoutAlternatives.alternatives;

    expect(supplierChoiceSummary(withoutAlternatives as SupplierChoice)).toBe('Acme Supplies');
  });
});

describe('alternativeSupplierNames', () => {
  it('keeps the server ordering', () => {
    expect(alternativeSupplierNames(choice({ alternatives: others('Beta', 'Gamma') }))).toEqual([
      'Beta',
      'Gamma',
    ]);
  });

  it('is empty rather than undefined when the field is absent', () => {
    expect(alternativeSupplierNames(undefined)).toEqual([]);
  });
});

/**
 * The joined list, owned here rather than at three call sites.
 *
 * The scan page, the kit card and the CSV each joined `alternatives` for
 * themselves, so the same list had three separators and three emptiness tests.
 * Null — not `''` — is what stops a caller rendering a dangling lead-in.
 */
describe('alternativeSupplierNamesText', () => {
  it('reads as one run of names in the server ordering', () => {
    expect(alternativeSupplierNamesText(choice({ alternatives: others('Beta', 'Gamma') }))).toBe(
      'Beta, Gamma'
    );
  });

  it('is null, not empty, where there were no others', () => {
    expect(alternativeSupplierNamesText(choice())).toBeNull();
    expect(alternativeSupplierNamesText(undefined)).toBeNull();
  });
});

/**
 * The anonymous count — how many, never who.
 *
 * Written twice before this lived here, and the two copies had already drifted
 * apart by a full stop. It is granted on the item detail page only; the scan
 * page asks for nothing, which is why this is a function a surface must call
 * by name rather than a mode the module applies for it.
 */
describe('anonymousAlternativesNote', () => {
  it('counts the others without naming any of them', () => {
    const note = anonymousAlternativesNote(choice({ alternatives: others('Beta', 'Gamma') }));

    expect(note).toBe('2 other suppliers also stock this item.');
    expect(note).not.toContain('Beta');
    expect(note).not.toContain('Gamma');
  });

  it('agrees with itself for exactly one other', () => {
    expect(anonymousAlternativesNote(choice({ alternatives: others('Beta') }))).toBe(
      '1 other supplier also stocks this item.'
    );
  });

  it('is null where there were no others', () => {
    expect(anonymousAlternativesNote(choice())).toBeNull();
    expect(anonymousAlternativesNote(undefined)).toBeNull();
  });

  it('never names the chosen supplier either', () => {
    expect(anonymousAlternativesNote(choice({ alternatives: others('Beta') }))).not.toContain(
      'Acme Supplies'
    );
  });
});

describe('supplierChoiceCaveats', () => {
  it('is empty for a choice with nothing qualifying it', () => {
    expect(supplierChoiceCaveats(choice())).toEqual([]);
  });

  /**
   * The scoring punishes neither gap, so the winner can have won while nobody
   * knew its price. An operator reading a blank cost cell cannot tell that from
   * "there is no supplier" unless it is said.
   */
  it('reports a choice made without a price', () => {
    expect(supplierChoiceCaveats(choice({ scored_without_price: true }))).toEqual([
      'chosen without a price on file',
    ]);
  });

  it('reports a choice made with no delivery history', () => {
    expect(supplierChoiceCaveats(choice({ scored_without_history: true }))).toEqual([
      'chosen with no delivery history',
    ]);
  });

  it('reports the operator’s own flagged primary being skipped', () => {
    expect(supplierChoiceCaveats(choice({ flagged_primary_unorderable: true }))).toEqual([
      'your flagged primary supplier cannot be ordered from and was skipped',
    ]);
  });

  it('reports all three together rather than only the first', () => {
    expect(
      supplierChoiceCaveats(
        choice({
          scored_without_price: true,
          scored_without_history: true,
          flagged_primary_unorderable: true,
        })
      )
    ).toHaveLength(3);
  });
});

describe('supplierChoiceNote', () => {
  it('is null when a supplier was chosen and nothing qualifies it', () => {
    expect(supplierChoiceNote(choice())).toBeNull();
  });

  it('carries the one caveat there is', () => {
    expect(supplierChoiceNote(choice({ scored_without_price: true }))).toBe(
      'chosen without a price on file'
    );
  });

  /**
   * A surface shows this line and nothing else, so a note that stops at the
   * first caveat silently drops the rest — the operator is told the price was
   * unknown and never told their own flagged primary was skipped.
   */
  it('carries EVERY caveat, not just the first', () => {
    const note = supplierChoiceNote(
      choice({ scored_without_price: true, flagged_primary_unorderable: true })
    );

    expect(note).toContain('chosen without a price on file');
    expect(note).toContain('flagged primary supplier cannot be ordered from');
  });

  /**
   * "Nobody has said where this comes from" and "everyone who did is dead" need
   * different words and different actions from an operator. The server keeps
   * them apart; so must the reading.
   */
  it('words the two no-supplier reasons differently', () => {
    const bare = supplierChoiceNote(choice({ supplier_name: null, reason: 'no_suppliers' }));
    const dead = supplierChoiceNote(choice({ supplier_name: null, reason: 'none_orderable' }));

    expect(bare).toBe('No supplier is linked to this item.');
    expect(dead).toContain('inactive or discontinued');
    expect(bare).not.toBe(dead);
  });

  it('says the field was missing rather than inventing an answer', () => {
    expect(supplierChoiceNote(undefined)).toBe(SUPPLIER_CHOICE_UNKNOWN);
  });

  /**
   * `chosenSupplierName` reads an ABSENT key as "no supplier" (`?? null`), so a
   * note that tested `=== null` strictly disagreed with it: the name rendered
   * blank while the caveats rendered anyway — a qualifier about a supplier no
   * surface had named. On the CSV export that is a blank `Supplier` cell with
   * nothing in `Supplier Caveats` to say the field never arrived.
   */
  it('reads an absent supplier_name the same way the name reader does', () => {
    const withoutName = { ...choice() } as Partial<SupplierChoice>;
    delete withoutName.supplier_name;
    const noName = withoutName as SupplierChoice;

    expect(chosenSupplierName(noName)).toBeNull();
    expect(supplierChoiceNote(noName)).toBe(SUPPLIER_CHOICE_UNKNOWN);
    expect(supplierChoiceCaveats({ ...noName, scored_without_price: true })).toEqual([]);
  });
});

/**
 * `/inventory/scan` is a public QR route. Most of what `supplier_choice`
 * carries is addressed to whoever maintains the supplier links — a member has
 * no flagged primary, and cannot order at all — so the audience split lives
 * here rather than in any page.
 */
describe('publicSupplierChoiceNote', () => {
  it('BEFORE/AFTER: withholds every operator caveat', () => {
    const note = publicSupplierChoiceNote(
      choice({
        scored_without_price: true,
        scored_without_history: true,
        flagged_primary_unorderable: true,
      })
    );

    expect(note).toBeNull();
  });

  it('BEFORE/AFTER: still says when there is nothing to order, in two ways', () => {
    const bare = publicSupplierChoiceNote(choice({ supplier_name: null, reason: 'no_suppliers' }));
    const dead = publicSupplierChoiceNote(
      choice({ supplier_name: null, reason: 'none_orderable' })
    );

    expect(bare).toBe('No supplier is listed for this item.');
    expect(dead).toBe('This item cannot currently be ordered.');
    expect(bare).not.toBe(dead);
  });

  // A refusal a member can act on, without the link state behind it.
  it('leaks no vendor name and no link state', () => {
    const dead = publicSupplierChoiceNote(
      choice({ supplier_name: null, reason: 'none_orderable' })
    );

    expect(dead).not.toMatch(/inactive|discontinued|flagged|primary|link/i);
  });

  it('stays silent about a payload that never carried the field', () => {
    expect(publicSupplierChoiceNote(undefined)).toBeNull();
    expect(publicSupplierChoiceNote(choice({ supplier_name: null, reason: null }))).toBeNull();
  });

  it('CONTROL: the operator reading is unchanged', () => {
    expect(supplierChoiceNote(choice({ flagged_primary_unorderable: true }))).toContain(
      'flagged primary supplier cannot be ordered from'
    );
  });
});

/**
 * An unauthenticated payload does not carry the four derivation keys at all —
 * the server OMITS `basis`, `flagged_primary_unorderable`,
 * `scored_without_price` and `scored_without_history` for a logged-out caller
 * (`SupplierChoiceSerializer.OPERATOR_ONLY_FIELDS`).
 *
 * The public reading has to be blind to that difference, or the gate turns into
 * a rendering bug: an absent key must never become a rendered caveat, and it
 * must never silently change what an anonymous visitor is shown.
 */
describe('a payload with the operator-only keys omitted', () => {
  const restricted = (overrides: Partial<SupplierChoice> = {}): SupplierChoice => {
    const full = choice(overrides);
    const {
      basis: _basis,
      flagged_primary_unorderable: _flagged,
      scored_without_price: _price,
      scored_without_history: _history,
      ...rest
    } = full;
    return rest as SupplierChoice;
  };

  it('reads the same publicly as the same choice with the keys present and false', () => {
    const withNames = { alternatives: others('Beta', 'Gamma') };

    expect(chosenSupplierName(restricted(withNames))).toBe(
      chosenSupplierName(choice(withNames))
    );
    expect(anonymousAlternativesNote(restricted(withNames))).toBe(
      anonymousAlternativesNote(choice(withNames))
    );
    expect(publicSupplierChoiceNote(restricted(withNames))).toBe(
      publicSupplierChoiceNote(choice(withNames))
    );
    expect(supplierChoiceSummary(restricted(withNames))).toBe(
      supplierChoiceSummary(choice(withNames))
    );
  });

  it('grows no caveat out of a key that was never sent', () => {
    expect(supplierChoiceCaveats(restricted())).toEqual([]);
    expect(supplierChoiceNote(restricted())).toBeNull();
  });

  it('still tells the two no-supplier reasons apart', () => {
    expect(publicSupplierChoiceNote(restricted({ supplier_name: null, reason: 'no_suppliers' }))).toBe(
      publicSupplierChoiceNote(choice({ supplier_name: null, reason: 'no_suppliers' }))
    );
    expect(
      publicSupplierChoiceNote(restricted({ supplier_name: null, reason: 'none_orderable' }))
    ).toBe(publicSupplierChoiceNote(choice({ supplier_name: null, reason: 'none_orderable' })));
  });
});

describe('SUPPLIER_BASIS_LABELS', () => {
  it('tells an operator decision apart from a system score', () => {
    expect(SUPPLIER_BASIS_LABELS.flagged_primary).toBe('flagged primary');
    expect(SUPPLIER_BASIS_LABELS.best_scored).toBe('price, lead time and delivery record');
  });

  /** "Cheapest" would be false — the score weighs lead time and record too. */
  it('does not describe the scored basis as cheapest', () => {
    expect(SUPPLIER_BASIS_LABELS.best_scored).not.toMatch(/cheap/i);
  });
});
