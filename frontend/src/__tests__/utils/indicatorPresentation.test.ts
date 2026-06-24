/**
 * Unit tests for the indicator presentation policy (epic ga-72l). These lock
 * the frontend mirror to the backend ``PRESENTATIONS`` table so the on-screen
 * swatch/legend can't silently drift from what firmware actually renders.
 */
import {
  INDICATOR_PRESENTATIONS,
  INDICATOR_STATUS_ORDER,
  brightnessOpacity,
  isBlinkPattern,
  resolveColorHex,
  statusLabel,
  swatchVisualForPresentation,
  swatchVisualForStatus,
} from '../../utils/indicatorPresentation';

describe('indicatorPresentation', () => {
  it('mirrors the canonical ga-72l status → presentation mapping', () => {
    expect(INDICATOR_PRESENTATIONS.available).toEqual({
      color: 'green',
      brightness: 'low',
      pattern: 'solid',
      period_ms: null,
    });
    expect(INDICATOR_PRESENTATIONS.in_use).toEqual({
      color: 'green',
      brightness: 'high',
      pattern: 'solid',
      period_ms: null,
    });
    expect(INDICATOR_PRESENTATIONS.unavailable).toEqual({
      color: 'red',
      brightness: 'low',
      pattern: 'solid',
      period_ms: null,
    });
    expect(INDICATOR_PRESENTATIONS.locked_out).toEqual({
      color: null,
      brightness: null,
      pattern: 'off',
      period_ms: null,
    });
    expect(INDICATOR_PRESENTATIONS.classroom).toEqual({
      color: 'purple',
      brightness: 'high',
      pattern: 'slow_blink',
      period_ms: 1500,
    });
  });

  it('orders the five statuses for the legend', () => {
    expect(INDICATOR_STATUS_ORDER).toEqual([
      'available',
      'in_use',
      'unavailable',
      'locked_out',
      'classroom',
    ]);
  });

  it('derives swatch visuals from a status', () => {
    const available = swatchVisualForStatus('available');
    const inUse = swatchVisualForStatus('in_use');
    expect(available.isOff).toBe(false);
    expect(available.blink).toBe(false);
    // Low brightness is dimmer than high.
    expect(available.opacity).toBeLessThan(inUse.opacity);

    expect(swatchVisualForStatus('classroom').blink).toBe(true);

    const lockedOut = swatchVisualForStatus('locked_out');
    expect(lockedOut.isOff).toBe(true);
    expect(lockedOut.blink).toBe(false);
  });

  it('derives swatch visuals from an explicit presentation', () => {
    expect(swatchVisualForPresentation({ pattern: 'off' }).isOff).toBe(true);
    const preview = swatchVisualForPresentation({
      color: 'green',
      brightness: 'high',
      pattern: 'solid',
    });
    expect(preview.isOff).toBe(false);
    expect(preview.blink).toBe(false);
    expect(preview.background).toBe('#2f9e44');
    expect(swatchVisualForPresentation(null).isOff).toBe(true);
  });

  it('resolves firmware colors to CSS', () => {
    expect(resolveColorHex('green')).toBe('#2f9e44');
    expect(resolveColorHex('#abcdef')).toBe('#abcdef');
    expect(resolveColorHex([1, 2, 3])).toBe('rgb(1, 2, 3)');
    expect(resolveColorHex(null)).toBeNull();
    expect(resolveColorHex([1, 2])).toBeNull();
  });

  it('maps brightness to opacity', () => {
    expect(brightnessOpacity('high')).toBe(1);
    expect(brightnessOpacity('low')).toBeLessThan(1);
    expect(brightnessOpacity(255)).toBe(1);
    expect(brightnessOpacity(null)).toBe(1);
  });

  it('recognises animating patterns', () => {
    expect(isBlinkPattern('slow_blink')).toBe(true);
    expect(isBlinkPattern('blink')).toBe(true);
    expect(isBlinkPattern('breathe')).toBe(true);
    expect(isBlinkPattern('solid')).toBe(false);
    expect(isBlinkPattern('off')).toBe(false);
    expect(isBlinkPattern(null)).toBe(false);
  });

  it('labels statuses, including the pre-sync empty value', () => {
    expect(statusLabel('')).toBe('Not yet synced');
    expect(statusLabel('available')).toBe('Available');
    expect(statusLabel('in_use')).toBe('In use');
    expect(statusLabel('classroom')).toBe('In use for a class');
  });
});
