/**
 * Tests for serialized-component presentation helpers (#818/#819).
 */
import { describe, expect, it } from 'vitest';

import {
  parseSerialNumbers,
  serializedActionColor,
  serializedActionLabel,
  serializedStatusColor,
  SERIALIZED_ACTIONS_NEEDING_INPUT,
} from '../../utils/serializedComponents';

describe('parseSerialNumbers', () => {
  it('splits on newlines and commas, trimming whitespace', () => {
    expect(parseSerialNumbers('SN-1\nSN-2, SN-3\n  SN-4  ')).toEqual([
      'SN-1',
      'SN-2',
      'SN-3',
      'SN-4',
    ]);
  });

  it('drops blank lines and de-duplicates while preserving order', () => {
    expect(parseSerialNumbers('SN-1\n\nSN-2\nSN-1\n')).toEqual(['SN-1', 'SN-2']);
  });

  it('returns an empty list for empty / whitespace input', () => {
    expect(parseSerialNumbers('')).toEqual([]);
    expect(parseSerialNumbers('   \n  ')).toEqual([]);
  });
});

describe('status + action helpers', () => {
  it('maps every status to a colour and falls back for unknown', () => {
    expect(serializedStatusColor('installed')).toBe('blue');
    expect(serializedStatusColor('disposed')).toBe('red');
    // Unknown status defensively falls back rather than returning undefined.
    expect(serializedStatusColor('mystery' as never)).toBe('gray');
  });

  it('labels and colours lifecycle actions', () => {
    expect(serializedActionLabel('receive')).toBe('Receive');
    expect(serializedActionColor('dispose')).toBe('red');
  });

  it('flags only install + dispose as needing extra input', () => {
    expect(SERIALIZED_ACTIONS_NEEDING_INPUT.has('install')).toBe(true);
    expect(SERIALIZED_ACTIONS_NEEDING_INPUT.has('dispose')).toBe(true);
    expect(SERIALIZED_ACTIONS_NEEDING_INPUT.has('receive')).toBe(false);
    expect(SERIALIZED_ACTIONS_NEEDING_INPUT.has('consume')).toBe(false);
  });
});
