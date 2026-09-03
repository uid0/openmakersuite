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
  chosenSupplierName,
  supplierChoiceCaveats,
  supplierChoiceNote,
  supplierChoiceSummary,
  supplierChoiceWithAlternatives,
} from '../../utils/supplierChoice';

const choice = (overrides: Partial<SupplierChoice> = {}): SupplierChoice => ({
  item_supplier_id: 1,
  supplier_id: 50,
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
});

describe('supplierChoiceWithAlternatives', () => {
  it('names the others outright where a surface has room for them', () => {
    expect(
      supplierChoiceWithAlternatives(choice({ alternatives: others('Beta', 'Gamma') }))
    ).toBe('Acme Supplies (also available from Beta, Gamma)');
  });

  it('adds no parenthetical when there was nothing else', () => {
    expect(supplierChoiceWithAlternatives(choice())).toBe('Acme Supplies');
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
