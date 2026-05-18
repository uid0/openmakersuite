/**
 * Spatial 2-column rendering of a power panel — odds on the left,
 * evens on the right, mirroring how breakers are physically arranged
 * inside the cabinet.
 *
 * Multi-pole breakers (2P stoves, 240V dryers, 3P industrial) visually
 * span their occupied slots. Tandem positions (e.g. "14/16") render at
 * both listed slots.
 *
 * Two density modes:
 *   - "interactive" — used by the panel detail page; includes circuit
 *     summaries and edit affordances.
 *   - "print" — stripped-down cabinet directory for printing and
 *     taping inside the cabinet door.
 */
import { Badge, Group, Paper, Stack, Text } from '@mantine/core';
import { IconBolt, IconEdit } from '@tabler/icons-react';
import React from 'react';
import { Link } from 'react-router-dom';

import { PowerBreakerNode, PowerPanelTopology } from '../services/api';

export type PanelLayoutDensity = 'interactive' | 'print';

interface SlotCell {
  slot: number;
  breaker: PowerBreakerNode | null;
  /**
   * Number of vertical cells this slot occupies. >1 means the breaker
   * is multi-pole and the following same-side slot(s) are continuation
   * rows owned by this breaker.
   */
  rowSpan: number;
  /**
   * True when this slot is covered by a multi-pole breaker rooted at
   * an earlier slot. Such cells render as an empty continuation marker.
   */
  isContinuation: boolean;
}

interface SlotPair {
  left: SlotCell;
  right: SlotCell;
}

/**
 * Parse a position string into one or more numeric slot numbers.
 * Accepts plain integers ("12") and tandem forms ("14/16", "14-16").
 * Returns an empty array for unparseable input so the caller can fall
 * back gracefully.
 */
function parsePositions(position: string): number[] {
  if (!position) return [];
  const parts = position.split(/[/\-,\s]+/).filter(Boolean);
  const nums: number[] = [];
  for (const p of parts) {
    const n = parseInt(p, 10);
    if (Number.isFinite(n) && n > 0) nums.push(n);
  }
  return nums;
}

/**
 * Compute the slot grid for a panel.
 *
 * Strategy: for every breaker, work out which slot numbers it occupies
 * (tandem positions from `position`, or primary slot + skip-2 for each
 * additional pole on `pole_count`). Then build a sparse map and bucket
 * into left (odd) / right (even) columns by slot number parity.
 */
function buildSlotPairs(topology: PowerPanelTopology): SlotPair[] {
  // slotNumber -> { breaker, isContinuation }
  const occupancy = new Map<number, { breaker: PowerBreakerNode; primary: boolean }>();

  for (const breaker of topology.breakers) {
    const parsed = parsePositions(breaker.position);
    if (parsed.length === 0) continue;
    const primary = parsed[0];
    occupancy.set(primary, { breaker, primary: true });

    if (parsed.length > 1) {
      // Tandem: every listed slot is "primary" for that breaker (one
      // physical breaker, two listed slot numbers).
      for (let i = 1; i < parsed.length; i++) {
        occupancy.set(parsed[i], { breaker, primary: false });
      }
    } else if (breaker.pole_count > 1) {
      // Multi-pole: next pole_count-1 same-side slots are continuations.
      for (let i = 1; i < breaker.pole_count; i++) {
        occupancy.set(primary + i * 2, { breaker, primary: false });
      }
    }
  }

  // Determine grid size. Round up to even to keep both columns balanced.
  // Minimum 12 slots — that's a 6-circuit subpanel; anything smaller
  // probably means a misconfigured panel and 12 still renders cleanly.
  let maxSlot = 12;
  Array.from(occupancy.keys()).forEach((slot) => {
    if (slot > maxSlot) maxSlot = slot;
  });
  if (maxSlot % 2 !== 0) maxSlot += 1;

  const cellFor = (slot: number): SlotCell => {
    const entry = occupancy.get(slot);
    if (!entry) {
      return { slot, breaker: null, rowSpan: 1, isContinuation: false };
    }
    if (!entry.primary) {
      return { slot, breaker: entry.breaker, rowSpan: 1, isContinuation: true };
    }
    // Compute row span only for true (non-tandem) multi-pole breakers.
    // Tandem positions list each slot explicitly and each gets its
    // own row, so row span stays 1 in that case.
    const parsed = parsePositions(entry.breaker.position);
    const span = parsed.length > 1 ? 1 : entry.breaker.pole_count;
    return { slot, breaker: entry.breaker, rowSpan: span, isContinuation: false };
  };

  const pairs: SlotPair[] = [];
  // Pair odds (left) with the next-higher even (right): (1,2), (3,4), ...
  for (let odd = 1; odd <= maxSlot; odd += 2) {
    pairs.push({ left: cellFor(odd), right: cellFor(odd + 1) });
  }
  return pairs;
}

function breakerSummary(breaker: PowerBreakerNode): string {
  const parts: string[] = [];
  if (breaker.label) parts.push(breaker.label);
  parts.push(`${breaker.amperage}A`);
  if (breaker.pole_count > 1) parts.push(`${breaker.pole_count}P`);
  return parts.join(' · ');
}

function circuitSummary(breaker: PowerBreakerNode): string {
  const circuitCount = breaker.circuits.length;
  if (circuitCount === 0) return 'no circuits wired';
  const outletCount = breaker.circuits.reduce((acc, c) => acc + c.outlets.length, 0);
  const labels = breaker.circuits
    .map((c) => c.label)
    .filter((l): l is string => Boolean(l))
    .slice(0, 2);
  const head = labels.length > 0 ? labels.join(', ') : `${circuitCount} circuit${circuitCount === 1 ? '' : 's'}`;
  return outletCount > 0 ? `${head} · ${outletCount} outlet${outletCount === 1 ? '' : 's'}` : head;
}

interface CellProps {
  cell: SlotCell;
  density: PanelLayoutDensity;
  side: 'left' | 'right';
}

const SlotBlock: React.FC<CellProps> = ({ cell, density, side }) => {
  const { slot, breaker, isContinuation } = cell;
  const slotLabel = (
    <Text fw={700} size={density === 'print' ? 'md' : 'sm'} ff="monospace">
      {String(slot).padStart(2, ' ')}
    </Text>
  );

  if (isContinuation && breaker) {
    return (
      <Paper
        withBorder
        p={density === 'print' ? 'xs' : 'sm'}
        radius="sm"
        data-testid={`slot-${slot}`}
        data-continuation="true"
        style={{ borderStyle: 'dashed', minHeight: density === 'print' ? 36 : 56 }}
      >
        <Group gap="xs" wrap="nowrap" align="center">
          {slotLabel}
          <Text size="xs" c="dimmed" fs="italic">
            tied to slot {parsePositions(breaker.position)[0]}
          </Text>
        </Group>
      </Paper>
    );
  }

  if (!breaker) {
    return (
      <Paper
        withBorder
        p={density === 'print' ? 'xs' : 'sm'}
        radius="sm"
        data-testid={`slot-${slot}`}
        data-empty="true"
        style={{ minHeight: density === 'print' ? 36 : 56, background: 'rgba(0,0,0,0.02)' }}
      >
        <Group gap="xs" wrap="nowrap" align="center">
          {slotLabel}
          <Text size="xs" c="dimmed">
            spare
          </Text>
        </Group>
      </Paper>
    );
  }

  const statusColor = breaker.status === 'active' ? 'green' : breaker.status === 'locked_out' ? 'red' : 'gray';

  // Review-status driven background tint, orthogonal to status. Red =
  // active-but-circuit-gone (DANGER state), grey = known-stale awaiting
  // cleanup. Skipped in print density so the printed copy stays clean.
  const reviewTint =
    density === 'interactive' && breaker.review_status === 'needs_attention'
      ? { background: 'rgba(220, 53, 69, 0.08)', borderColor: '#dc3545' }
      : density === 'interactive' && breaker.review_status === 'circuit_moved'
        ? { background: 'rgba(108, 117, 125, 0.10)', borderColor: '#6c757d' }
        : {};

  return (
    <Paper
      withBorder
      p={density === 'print' ? 'xs' : 'sm'}
      radius="sm"
      data-testid={`slot-${slot}`}
      data-breaker-id={breaker.id}
      data-review-status={breaker.review_status}
      style={{ minHeight: density === 'print' ? 36 : 56, ...reviewTint }}
    >
      <Stack gap={density === 'print' ? 2 : 4}>
        <Group gap="xs" wrap="nowrap" justify="space-between" align="flex-start">
          <Group gap="xs" wrap="nowrap" align="center">
            {slotLabel}
            <Text fw={600} size={density === 'print' ? 'sm' : 'sm'}>
              {breakerSummary(breaker)}
            </Text>
          </Group>
          {density === 'interactive' && (
            <Group gap={4} wrap="nowrap">
              <Badge size="xs" color={statusColor} variant="light" radius="sm">
                {breaker.status}
              </Badge>
              {breaker.review_status === 'needs_attention' && (
                <Badge size="xs" color="red" variant="filled" radius="sm" title={breaker.review_note}>
                  needs attention
                </Badge>
              )}
              {breaker.review_status === 'circuit_moved' && (
                <Badge size="xs" color="gray" variant="filled" radius="sm" title={breaker.review_note}>
                  circuit moved
                </Badge>
              )}
              <Link
                to={`/facilities/electrical/breakers/${breaker.id}/edit`}
                aria-label={`Edit breaker ${breaker.label || slot}`}
                style={{ display: 'inline-flex', alignItems: 'center', color: 'inherit' }}
              >
                <IconEdit size={14} />
              </Link>
            </Group>
          )}
        </Group>
        <Text size={density === 'print' ? 'xs' : 'xs'} c="dimmed" style={{ paddingLeft: density === 'print' ? 0 : 4 }}>
          {circuitSummary(breaker)}
        </Text>
      </Stack>
      {/* Layout helper: ensure right-side cells visually nudge from the spine. */}
      <span data-side={side} style={{ display: 'none' }} />
    </Paper>
  );
};

export interface PanelLayoutGridProps {
  topology: PowerPanelTopology;
  density?: PanelLayoutDensity;
}

const PanelLayoutGrid: React.FC<PanelLayoutGridProps> = ({ topology, density = 'interactive' }) => {
  const pairs = buildSlotPairs(topology);

  if (topology.breakers.length === 0) {
    return (
      <Paper withBorder p="xl" radius="md">
        <Stack gap="sm" align="center">
          <IconBolt size={28} stroke={1.6} />
          <Text fw={500}>No breakers configured for this panel</Text>
        </Stack>
      </Paper>
    );
  }

  return (
    <div
      className={`panel-layout-grid panel-layout-grid--${density}`}
      data-testid="panel-layout-grid"
      style={{
        display: 'grid',
        gridTemplateColumns: '1fr 1fr',
        gap: density === 'print' ? 6 : 10,
      }}
    >
      {pairs.map((pair) => (
        <React.Fragment key={`${pair.left.slot}-${pair.right.slot}`}>
          <SlotBlock cell={pair.left} density={density} side="left" />
          <SlotBlock cell={pair.right} density={density} side="right" />
        </React.Fragment>
      ))}
    </div>
  );
};

export default PanelLayoutGrid;
