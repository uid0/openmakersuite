/**
 * ServiceUnavailableNotice
 *
 * The one inline treatment for "this control depends on something that is
 * currently down". Put it next to the control you disabled, so the reason is
 * where the click would have been rather than only in the page-top banner.
 *
 *   const { isDegraded } = useServiceStatus();
 *   const controlDown = isDegraded('device_control');
 *   <button disabled={busy || controlDown}>Enable</button>
 *   <ServiceUnavailableNotice
 *     service="device_control"
 *     message="Device control unavailable (MQTT broker unreachable)"
 *   />
 *
 * Renders nothing unless the backend positively reports that service as
 * open/half-open — an unknown or failed status fetch shows nothing and gates
 * nothing (see ServiceStatusContext). Whether to *disable* stays with the call
 * site: some controls (a webhook test against one endpoint of a partly
 * degraded family) deserve the warning without losing the ability to try.
 *
 * `message` is author-supplied static copy. Never pass the API's `last_error`
 * — it can carry internal detail and this renders in member-facing surfaces.
 */
import React from 'react';

import { useServiceStatus } from '../hooks/useServiceStatus';
import { ServiceKey } from '../types';

/**
 * Standard copy for the device-control gate. Shared because every ForgeKey
 * surface (detail page, controls card, indicator card) ends in the same MQTT
 * publish and should say the same thing when the broker is unreachable.
 */
export const DEVICE_CONTROL_UNAVAILABLE =
  'Device control unavailable (MQTT broker unreachable)';

interface ServiceUnavailableNoticeProps {
  /** Which capability this control depends on. */
  service: ServiceKey;
  /** Plain-language reason. Defaults to the service's own label/description. */
  message?: string;
  testId?: string;
}

const ServiceUnavailableNotice: React.FC<ServiceUnavailableNoticeProps> = ({
  service,
  message,
  testId,
}) => {
  const { isDegraded, getService } = useServiceStatus();

  if (!isDegraded(service)) return null;

  const row = getService(service);
  const text =
    message ??
    `${row?.label ?? 'This service'} is temporarily unavailable — we'll keep retrying.`;

  return (
    <small
      role="status"
      data-testid={testId ?? `service-unavailable-${service}`}
      style={{ color: '#a1670c', display: 'block' }}
    >
      <span aria-hidden="true">⚠ </span>
      {text}
    </small>
  );
};

export default ServiceUnavailableNotice;
