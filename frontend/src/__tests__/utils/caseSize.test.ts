/**
 * Tests for the web's one wording of an unknown case size (op-2t4e).
 *
 * These pin the WORDS, not the state: `pack_size.py` decides which unknown an
 * item is in and `test_case_size_state_on_the_wire.py` pins that it says so on
 * the wire. What is tested here is the half that defect actually reached an
 * operator through — that two different problems no longer arrive wearing one
 * sentence, and that each sentence names something to DO.
 *
 * The distinctness assertions are the load-bearing ones. Against base every
 * unknown rendered `— (case size unknown)`, so a test that merely asserted "some
 * text appears" would have passed there and proved nothing.
 */
import {
  CASE_SIZE_STATE_UNRECOGNISED,
  caseSizeIsKnown,
  caseSizeUnknownLabel,
  caseSizeUnknownNote,
} from '../../utils/caseSize';

const UNKNOWN_STATES = ['not_recorded', 'recorded_zero', 'no_orderable_link'];

describe('caseSizeIsKnown', () => {
  it('is true only for the state the server calls known', () => {
    expect(caseSizeIsKnown('known')).toBe(true);
    UNKNOWN_STATES.forEach((state) => expect(caseSizeIsKnown(state)).toBe(false));
  });

  it('is false when the server sent no state at all', () => {
    expect(caseSizeIsKnown(undefined)).toBe(false);
  });
});

describe('caseSizeUnknownNote', () => {
  it('says nothing at all when the case size is known', () => {
    expect(caseSizeUnknownNote('known')).toBeNull();
  });

  it('tells a never-recorded case size apart from a wrongly-recorded one', () => {
    // THE DEFECT, as one assertion. Both are "case size unknown" on base.
    expect(caseSizeUnknownNote('not_recorded')).not.toBe(
      caseSizeUnknownNote('recorded_zero')
    );
  });

  it('gives every unknown state its own sentence', () => {
    const notes = UNKNOWN_STATES.map((state) => caseSizeUnknownNote(state));
    expect(new Set(notes).size).toBe(UNKNOWN_STATES.length);
    notes.forEach((note) => expect(note).not.toBeNull());
  });

  it('asks for a MISSING fact to be supplied when nothing was ever recorded', () => {
    const note = caseSizeUnknownNote('not_recorded') as string;
    expect(note).toMatch(/no supplier link records/i);
    expect(note).toMatch(/add a supplier relationship/i);
    // The remedy for the OTHER unknown must not leak into this one: an operator
    // told to "correct" a value nobody ever entered goes looking for a row that
    // is not there.
    expect(note).not.toMatch(/correct "Quantity per Package" on that/i);
  });

  it('asks for a WRONG fact to be corrected when a link records zero', () => {
    const note = caseSizeUnknownNote('recorded_zero') as string;
    expect(note).toMatch(/records a case of 0 units/i);
    expect(note).toMatch(/correct "Quantity per Package"/i);
    // And conversely: telling them to add a link is the wrong screen for an
    // item that already has one.
    expect(note).not.toMatch(/add a supplier relationship/i);
  });

  it('sends an item with only dead vendors to revive one, not to add one', () => {
    const note = caseSizeUnknownNote('no_orderable_link') as string;
    expect(note).toMatch(/inactive or discontinued/i);
    expect(note).toMatch(/reactivate a supplier relationship/i);
  });

  it('every unknown sentence names an action, not just a problem', () => {
    UNKNOWN_STATES.forEach((state) => {
      expect(caseSizeUnknownNote(state)).toMatch(
        /add|correct|reactivate|set "Quantity per Package"/i
      );
    });
  });

  it('treats an unrecognised state as its own fact, not as either unknown', () => {
    // A client too old for its server is a third thing. Dressing it as
    // "not recorded" would be this module committing the defect it closes.
    expect(caseSizeUnknownNote('something_new')).toBe(CASE_SIZE_STATE_UNRECOGNISED);
    expect(caseSizeUnknownNote(undefined)).toBe(CASE_SIZE_STATE_UNRECOGNISED);
    expect(CASE_SIZE_STATE_UNRECOGNISED).not.toBe(caseSizeUnknownNote('not_recorded'));
    expect(CASE_SIZE_STATE_UNRECOGNISED).not.toBe(caseSizeUnknownNote('recorded_zero'));
  });
});

describe('caseSizeUnknownLabel', () => {
  it('renders nothing for a known case size — the number speaks for itself', () => {
    expect(caseSizeUnknownLabel('known')).toBeNull();
  });

  it('distinguishes the states even in the short cell form', () => {
    const labels = UNKNOWN_STATES.map((state) => caseSizeUnknownLabel(state));
    expect(new Set(labels).size).toBe(UNKNOWN_STATES.length);
  });

  it('keeps the em dash, so a narrow column still reads as "no number"', () => {
    UNKNOWN_STATES.forEach((state) => {
      expect(caseSizeUnknownLabel(state)).toMatch(/^— /);
    });
  });

  it('falls back to the old undifferentiated wording only for an unknown state', () => {
    expect(caseSizeUnknownLabel('something_new')).toBe('— (case size unknown)');
    UNKNOWN_STATES.forEach((state) => {
      expect(caseSizeUnknownLabel(state)).not.toBe('— (case size unknown)');
    });
  });
});
