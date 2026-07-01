/**
 * DeviceSectionGate (op-3u4)
 *
 * Wraps a device-detail section and, when it is not relevant to the device's
 * type, dims it and labels it "Not applicable for this device type" instead of
 * hiding it — so staff can still see the section exists but understand it does
 * not apply to this device.
 *
 * Relevance is a tri-state (see utils/deviceSectionRelevance): only `'no'`
 * (or `false`) triggers the greyed treatment; `'yes'` / `'unknown'` render the
 * children unchanged.
 */
import { Badge } from '@mantine/core';
import React from 'react';
import type { SectionRelevance } from '../utils/deviceSectionRelevance';

interface DeviceSectionGateProps {
  /** `'no'` / `false` dims + labels the section; anything else renders as-is. */
  relevant: SectionRelevance | boolean;
  /** Override the not-applicable label. */
  reason?: string;
  /** Stable test id on the wrapper (present in both states). */
  testId?: string;
  children: React.ReactNode;
}

const DEFAULT_REASON = 'Not applicable for this device type';

const DeviceSectionGate: React.FC<DeviceSectionGateProps> = ({
  relevant,
  reason = DEFAULT_REASON,
  testId,
  children,
}) => {
  const notApplicable = relevant === 'no' || relevant === false;

  if (!notApplicable) {
    return <div data-testid={testId}>{children}</div>;
  }

  return (
    <div data-testid={testId} aria-disabled="true">
      <Badge color="gray" variant="light" mb="xs">
        {reason}
      </Badge>
      <div style={{ opacity: 0.45, pointerEvents: 'none' }}>{children}</div>
    </div>
  );
};

export default DeviceSectionGate;
