/**
 * Compute the expected phase for a breaker at a given panel slot.
 *
 * Convention: US load-center "2-slot stride" layout where each pair of
 * vertically-adjacent same-side slots taps the same phase. Walking down
 * one side, rows alternate between phases:
 *
 *   Split-phase:  row 0=A, row 1=B, row 2=A, row 3=B, ...
 *   Three-phase:  row 0=A, row 1=B, row 2=C, row 3=A, row 4=B, ...
 *
 * `rowIndex(slot)` is `floor((slot - 1) / 2)`, which works for both odd
 * (left column) and even (right column) slots and gives the same row
 * index for the horizontally-adjacent pair (1↔2, 3↔4, ...).
 *
 * Multi-pole breakers span `pole_count` consecutive same-side slots,
 * which map to consecutive rows. The resulting phase string is the
 * alphabetical concatenation of the covered single phases — matching
 * the model's PHASE_CHOICES ("A", "B", "C", "AB", "BC", "AC", "ABC").
 *
 * Returns null when auto-calc isn't safe (tandem positions like "14/16"
 * where both slots share a single bus tap, or unparseable positions).
 */
import type { PowerPanelPhase } from '../services/api';

type SinglePhase = 'A' | 'B' | 'C';
export type ComputedPhase = 'A' | 'B' | 'C' | 'AB' | 'BC' | 'AC' | 'ABC';

const SINGLE_PHASES: SinglePhase[] = ['A', 'B', 'C'];

/**
 * Parse the first integer slot out of a position string. Returns null
 * when the position is empty/non-numeric or carries a tandem separator
 * ("/" or "-"), because tandem slots break the row-index model.
 */
function primarySlot(position: string): number | null {
  if (!position) return null;
  if (/[/-]/.test(position)) return null; // tandem — bail out
  const n = parseInt(position.trim(), 10);
  if (!Number.isFinite(n) || n < 1) return null;
  return n;
}

function rowIndex(slot: number): number {
  return Math.floor((slot - 1) / 2);
}

function phaseForRow(row: number, config: PowerPanelPhase): SinglePhase {
  if (config === 'single') return 'A';
  const modulus = config === 'three' ? 3 : 2;
  return SINGLE_PHASES[((row % modulus) + modulus) % modulus];
}

/**
 * Returns the auto-calculated phase string, or null when we should NOT
 * auto-fill (tandem position, missing inputs, single-phase always-A).
 *
 * Single-phase panels return 'A' for every well-formed input so the
 * caller can still pre-populate the field rather than leave it blank.
 */
export function computePhase(args: {
  position: string;
  poleCount: number;
  panelPhase: PowerPanelPhase;
}): ComputedPhase | null {
  const { position, poleCount, panelPhase } = args;
  const slot = primarySlot(position);
  if (slot === null) return null;
  const poles = Math.max(1, Math.min(3, poleCount || 1));

  const covered = new Set<SinglePhase>();
  const startRow = rowIndex(slot);
  for (let i = 0; i < poles; i++) {
    covered.add(phaseForRow(startRow + i, panelPhase));
  }

  // Concatenate in canonical A,B,C order so "C+A" comes out as "AC" —
  // matches the model's PHASE_CHOICES ("AB", "BC", "AC", "ABC").
  const ordered = SINGLE_PHASES.filter((p) => covered.has(p)).join('');
  return ordered as ComputedPhase;
}
