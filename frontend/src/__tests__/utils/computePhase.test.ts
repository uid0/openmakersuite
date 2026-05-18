import { computePhase } from '../../utils/computePhase';

describe('utils/computePhase', () => {
  describe('single-phase panels', () => {
    it('always returns A regardless of slot', () => {
      expect(computePhase({ position: '1', poleCount: 1, panelPhase: 'single' })).toBe('A');
      expect(computePhase({ position: '12', poleCount: 1, panelPhase: 'single' })).toBe('A');
      expect(computePhase({ position: '40', poleCount: 1, panelPhase: 'single' })).toBe('A');
    });
  });

  describe('split-phase panels', () => {
    // Row index pattern (2-slot stride): slots 1&2=row0, slots 3&4=row1,
    // 5&6=row2, ... Phase alternates A/B per row.
    it.each([
      ['1', 'A'],
      ['2', 'A'],
      ['3', 'B'],
      ['4', 'B'],
      ['5', 'A'],
      ['6', 'A'],
      ['7', 'B'],
      ['8', 'B'],
      ['11', 'B'],
      ['12', 'B'],
    ])('1-pole at slot %s → %s', (position, expected) => {
      expect(computePhase({ position, poleCount: 1, panelPhase: 'split' })).toBe(expected);
    });

    it.each([
      ['1', 'AB'],
      ['2', 'AB'],
      ['3', 'AB'],
      ['5', 'AB'],
    ])('2-pole at slot %s → %s (always AB on split phase)', (position, expected) => {
      expect(computePhase({ position, poleCount: 2, panelPhase: 'split' })).toBe(expected);
    });
  });

  describe('three-phase panels', () => {
    // Row index pattern: rows cycle A/B/C every 2 slots same side.
    it.each([
      ['1', 'A'],
      ['3', 'B'],
      ['5', 'C'],
      ['7', 'A'],
      ['9', 'B'],
      ['11', 'C'],
      ['2', 'A'],
      ['4', 'B'],
      ['6', 'C'],
      ['8', 'A'],
    ])('1-pole at slot %s → %s', (position, expected) => {
      expect(computePhase({ position, poleCount: 1, panelPhase: 'three' })).toBe(expected);
    });

    it('2-pole at slot 1 → AB', () => {
      expect(computePhase({ position: '1', poleCount: 2, panelPhase: 'three' })).toBe('AB');
    });

    it('2-pole at slot 3 → BC', () => {
      expect(computePhase({ position: '3', poleCount: 2, panelPhase: 'three' })).toBe('BC');
    });

    it('2-pole at slot 5 → AC (wraps C→A; sorted alphabetically)', () => {
      expect(computePhase({ position: '5', poleCount: 2, panelPhase: 'three' })).toBe('AC');
    });

    it('3-pole at slot 1 → ABC', () => {
      expect(computePhase({ position: '1', poleCount: 3, panelPhase: 'three' })).toBe('ABC');
    });

    it('3-pole at slot 5 → ABC (wraps and de-dups)', () => {
      expect(computePhase({ position: '5', poleCount: 3, panelPhase: 'three' })).toBe('ABC');
    });
  });

  describe('bail-out cases', () => {
    it('returns null for tandem positions', () => {
      expect(computePhase({ position: '14/16', poleCount: 1, panelPhase: 'split' })).toBeNull();
      expect(computePhase({ position: '1-3', poleCount: 1, panelPhase: 'split' })).toBeNull();
    });

    it('returns null for empty position', () => {
      expect(computePhase({ position: '', poleCount: 1, panelPhase: 'split' })).toBeNull();
    });

    it('returns null for non-numeric position', () => {
      expect(computePhase({ position: 'abc', poleCount: 1, panelPhase: 'split' })).toBeNull();
    });

    it('returns null for slot 0 / negative slots', () => {
      expect(computePhase({ position: '0', poleCount: 1, panelPhase: 'split' })).toBeNull();
    });
  });

  describe('pole count clamping', () => {
    it('clamps pole count below 1 to 1', () => {
      expect(computePhase({ position: '1', poleCount: 0, panelPhase: 'split' })).toBe('A');
    });

    it('clamps pole count above 3 to 3', () => {
      expect(computePhase({ position: '1', poleCount: 5, panelPhase: 'three' })).toBe('ABC');
    });
  });
});
