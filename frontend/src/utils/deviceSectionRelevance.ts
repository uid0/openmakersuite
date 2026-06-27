/**
 * Device-section relevance (op-3u4).
 *
 * The ForgeKey device-detail page renders sections (occupancy, temperature,
 * indicator light, …) that only make sense for certain device types — e.g. the
 * occupancy chart is meaningful for a people counter but not for an
 * indicator/status-light. This helper decides, per section, whether it applies
 * to a given device so the page can grey out (not hide) the ones that don't.
 *
 * Signal priority (capability-first, device_type fallback):
 *   1. Announced `capabilities` — the most accurate, device-reported signal.
 *   2. The device's `device_type` *code* — stable, but the device API only
 *      exposes the numeric id + display name, so the caller resolves the code
 *      via `resolveDeviceTypeCode()` (the device-types list).
 *
 * It is intentionally conservative: it only returns `'no'` (grey out) when we
 * have a positive signal to judge on. With neither announced capabilities nor a
 * resolvable type it returns `'unknown'` and the caller renders normally — we
 * never grey a section when we can't be sure.
 *
 * Token sources (keep in sync with the backend):
 *   - DeviceType.code choices: backend/forgekey/models.py:28-48
 *   - capabilities help text:  backend/forgekey/models.py:165-178
 *     (documented tokens: 'people_counter', 'status_led'; indicator firmware
 *      also announces 'status_matrix'.)
 */
import type { ForgeKeyDevice, ForgeKeyDeviceType } from '../services/api';

export type DeviceSection = 'occupancy' | 'temperature' | 'indicator';

/** Tri-state: applies / confidently does not apply / undetermined. */
export type SectionRelevance = 'yes' | 'no' | 'unknown';

/**
 * Capability tokens (announced by firmware) that make a section relevant.
 *
 * Occupancy is people-counter ONLY (product decision: door counters and mmWave
 * presence are deliberately excluded). Temperature has no canonical capability
 * token, so its entries are best-effort — temperature relevance is driven by
 * device_type below; the type signal covers real temperature/env sensors even
 * when no recognised capability is announced.
 */
const CAPABILITY_SECTIONS: Record<DeviceSection, readonly string[]> = {
  occupancy: ['people_counter'],
  temperature: ['temperature', 'env_sensor'],
  indicator: ['status_led', 'status_matrix', 'led_strip'],
};

/** Stable DeviceType.code values that make a section relevant. */
const DEVICE_TYPE_SECTIONS: Record<DeviceSection, readonly string[]> = {
  occupancy: ['people_counter'],
  temperature: ['temperature_sensor', 'env_sensor'],
  indicator: ['indicator', 'led_strip'],
};

/** Display name the seeded indicator DeviceType ships with (last-ditch fallback). */
const INDICATOR_TYPE_NAME = 'Indicator/Status Light';

/**
 * Resolve a device's stable `device_type` *code* (e.g. `'indicator'`,
 * `'people_counter'`).
 *
 * The device serializer only exposes the numeric `device_type` id and the
 * display `device_type_name`, so we map the id through the device-types list
 * (mirrors IndicatorManagementCard). Falls back to the well-known indicator
 * display name, then `null` when it cannot be resolved.
 */
export function resolveDeviceTypeCode(
  device: Pick<ForgeKeyDevice, 'device_type' | 'device_type_name'>,
  deviceTypes: readonly ForgeKeyDeviceType[],
): string | null {
  if (device.device_type != null) {
    const match = deviceTypes.find((t) => t.id === device.device_type);
    if (match) return match.code;
  }
  if (device.device_type_name === INDICATOR_TYPE_NAME) return 'indicator';
  return null;
}

/**
 * Decide whether `section` applies to `device`.
 *
 * @param deviceTypeCode resolved via {@link resolveDeviceTypeCode}, or null.
 */
export function sectionRelevance(
  section: DeviceSection,
  device: Pick<ForgeKeyDevice, 'capabilities'>,
  deviceTypeCode: string | null,
): SectionRelevance {
  const capabilities = device.capabilities ?? [];
  const capMatch = capabilities.some((cap) => CAPABILITY_SECTIONS[section].includes(cap));
  const typeMatch = deviceTypeCode != null && DEVICE_TYPE_SECTIONS[section].includes(deviceTypeCode);

  if (capMatch || typeMatch) return 'yes';

  // No match. We can only say "no" if we had a signal to judge on: either the
  // device announced capabilities (and none matched) or we resolved its type
  // (and it isn't a match). With neither, we can't tell — render normally.
  if (capabilities.length > 0 || deviceTypeCode != null) return 'no';
  return 'unknown';
}
