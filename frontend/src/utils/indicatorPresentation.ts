/**
 * Indicator-light presentation policy (frontend mirror of epic ga-72l).
 *
 * The backend (`forgekey/services/indicator.py`) is the source of truth for the
 * status → (color, brightness, pattern) mapping it pushes to firmware. This
 * module mirrors that table so the management UI can render an on-screen swatch
 * and legend that match what an admin will actually see on the hardware, and so
 * a live "test light" preview can be rendered from an arbitrary presentation.
 */
import { ForgeKeyIndicatorPresentation, IndicatorStatusValue } from '../services/api';

// Display order for the legend (matches backend IndicatorStatus.CHOICES).
export const INDICATOR_STATUS_ORDER: IndicatorStatusValue[] = [
  'available',
  'in_use',
  'unavailable',
  'locked_out',
  'classroom',
];

// Human labels — match the backend ``IndicatorStatus.CHOICES`` display text.
export const INDICATOR_STATUS_LABELS: Record<IndicatorStatusValue, string> = {
  available: 'Available',
  in_use: 'In use',
  unavailable: 'Unavailable',
  locked_out: 'Locked out',
  classroom: 'In use for a class',
};

// Short plain-language description of the light each status produces.
export const INDICATOR_STATUS_DESCRIPTIONS: Record<IndicatorStatusValue, string> = {
  available: 'Low green',
  in_use: 'Bright green',
  unavailable: 'Low red',
  locked_out: 'Off',
  classroom: 'Purple slow blink',
};

export interface IndicatorPresentationSpec {
  color: string | null;
  brightness: 'low' | 'high' | null;
  pattern: 'solid' | 'slow_blink' | 'off';
  period_ms: number | null;
}

// Canonical ga-72l mapping — mirrors backend ``PRESENTATIONS``.
export const INDICATOR_PRESENTATIONS: Record<IndicatorStatusValue, IndicatorPresentationSpec> = {
  available: { color: 'green', brightness: 'low', pattern: 'solid', period_ms: null },
  in_use: { color: 'green', brightness: 'high', pattern: 'solid', period_ms: null },
  unavailable: { color: 'red', brightness: 'low', pattern: 'solid', period_ms: null },
  locked_out: { color: null, brightness: null, pattern: 'off', period_ms: null },
  classroom: { color: 'purple', brightness: 'high', pattern: 'slow_blink', period_ms: 1500 },
};

// Named firmware colors → CSS hex for the on-screen swatch.
const NAMED_COLOR_HEX: Record<string, string> = {
  green: '#2f9e44',
  red: '#e03131',
  purple: '#9c36b5',
  blue: '#1971c2',
  yellow: '#f08c00',
  orange: '#e8590c',
  white: '#f1f3f5',
  off: '#212529',
};

const OFF_BACKGROUND = NAMED_COLOR_HEX.off;

// Patterns that animate on the hardware (and so the swatch should pulse).
const BLINK_PATTERNS = new Set(['blink', 'slow_blink', 'breathe']);

export interface SwatchVisual {
  /** CSS color for the swatch background. */
  background: string;
  /** 0-1 opacity derived from brightness (dimmer for 'low'). */
  opacity: number;
  /** True when the pattern animates — the swatch pulses. */
  blink: boolean;
  /** True when the light is off (locked out / off pattern). */
  isOff: boolean;
}

function clamp(value: number, lo: number, hi: number): number {
  return Math.min(Math.max(value, lo), hi);
}

/** Resolve a firmware color (name / hex / [r,g,b]) to a CSS color string. */
export function resolveColorHex(
  color: string | number[] | null | undefined,
): string | null {
  if (color == null) return null;
  if (Array.isArray(color)) {
    if (color.length !== 3) return null;
    return `rgb(${color[0]}, ${color[1]}, ${color[2]})`;
  }
  const text = String(color).trim().toLowerCase();
  if (!text) return null;
  if (text.startsWith('#') || text.startsWith('rgb')) return text;
  // Fall back to the raw token — the browser may understand it (e.g. 'cyan').
  return NAMED_COLOR_HEX[text] ?? text;
}

/** Map a brightness word/int to a swatch opacity. */
export function brightnessOpacity(
  brightness: string | number | null | undefined,
): number {
  if (brightness == null || brightness === '') return 1;
  if (typeof brightness === 'number') return clamp(brightness / 255, 0.2, 1);
  const word = String(brightness).trim().toLowerCase();
  if (word === 'low') return 0.45;
  if (word === 'high') return 1;
  const numeric = Number(word);
  if (!Number.isNaN(numeric)) return clamp(numeric / 255, 0.2, 1);
  return 1;
}

export function isBlinkPattern(pattern: string | null | undefined): boolean {
  return pattern != null && BLINK_PATTERNS.has(pattern);
}

/**
 * Build the visual treatment for a swatch from an arbitrary presentation
 * (the derived/last-pushed state, or a manual test preview).
 */
export function swatchVisualForPresentation(
  presentation: ForgeKeyIndicatorPresentation | IndicatorPresentationSpec | null | undefined,
): SwatchVisual {
  const pattern = presentation?.pattern ?? null;
  const hex = resolveColorHex(presentation?.color);
  const isOff =
    pattern === 'off' ||
    (presentation?.color == null && presentation?.brightness == null && hex == null);
  return {
    background: isOff || hex == null ? OFF_BACKGROUND : hex,
    opacity: isOff ? 1 : brightnessOpacity(presentation?.brightness),
    blink: !isOff && isBlinkPattern(pattern),
    isOff,
  };
}

/** Swatch treatment for a canonical operational status. */
export function swatchVisualForStatus(status: IndicatorStatusValue): SwatchVisual {
  return swatchVisualForPresentation(INDICATOR_PRESENTATIONS[status]);
}

/** Human label for a status, tolerant of the empty pre-sync value. */
export function statusLabel(status: IndicatorStatusValue | '' | null | undefined): string {
  if (!status) return 'Not yet synced';
  return INDICATOR_STATUS_LABELS[status] ?? status;
}
