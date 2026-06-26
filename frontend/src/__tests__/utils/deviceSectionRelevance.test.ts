/**
 * Unit tests for the device-section relevance helper (op-3u4).
 */
import {
  resolveDeviceTypeCode,
  sectionRelevance,
} from '../../utils/deviceSectionRelevance';

const TYPES = [
  { id: 1, name: 'People Counter', code: 'people_counter' },
  { id: 9, name: 'Indicator/Status Light', code: 'indicator' },
  { id: 12, name: 'Temperature Sensor', code: 'temperature_sensor' },
] as any;

describe('resolveDeviceTypeCode', () => {
  it('resolves the stable code from the numeric device_type id', () => {
    expect(
      resolveDeviceTypeCode({ device_type: 9, device_type_name: 'whatever' }, TYPES),
    ).toBe('indicator');
  });

  it('falls back to the indicator display name when the id is unknown', () => {
    expect(
      resolveDeviceTypeCode(
        { device_type: 999, device_type_name: 'Indicator/Status Light' },
        [],
      ),
    ).toBe('indicator');
  });

  it('returns null when the type cannot be resolved', () => {
    expect(
      resolveDeviceTypeCode({ device_type: null, device_type_name: 'people_counter' }, TYPES),
    ).toBeNull();
  });
});

describe('sectionRelevance', () => {
  describe('occupancy (people-counter only)', () => {
    it('is yes when the people_counter capability is announced', () => {
      expect(sectionRelevance('occupancy', { capabilities: ['people_counter'] }, null)).toBe('yes');
    });

    it('is yes when the device_type code is people_counter (no capabilities)', () => {
      expect(sectionRelevance('occupancy', { capabilities: [] }, 'people_counter')).toBe('yes');
    });

    it('is no for an indicator device (capabilities announced, none match)', () => {
      expect(
        sectionRelevance('occupancy', { capabilities: ['status_led', 'status_matrix'] }, 'indicator'),
      ).toBe('no');
    });

    it('is no when capabilities are announced but none match, even without a type', () => {
      expect(sectionRelevance('occupancy', { capabilities: ['status_led'] }, null)).toBe('no');
    });

    it('does NOT include door_counter or mmwave_presence', () => {
      expect(sectionRelevance('occupancy', { capabilities: ['mmwave_presence'] }, 'door_counter')).toBe(
        'no',
      );
    });

    it('is unknown with no announced capabilities and an unresolved type', () => {
      expect(sectionRelevance('occupancy', { capabilities: [] }, null)).toBe('unknown');
    });

    it('treats null capabilities like an empty list', () => {
      expect(sectionRelevance('occupancy', { capabilities: null as any }, null)).toBe('unknown');
    });
  });

  describe('temperature (type-driven; no canonical capability token)', () => {
    it('is yes for a temperature_sensor device type even with no temp capability', () => {
      expect(sectionRelevance('temperature', { capabilities: [] }, 'temperature_sensor')).toBe('yes');
    });

    it('is yes for an env_sensor device type', () => {
      expect(sectionRelevance('temperature', { capabilities: [] }, 'env_sensor')).toBe('yes');
    });

    it('is no for an indicator device', () => {
      expect(
        sectionRelevance('temperature', { capabilities: ['status_led'] }, 'indicator'),
      ).toBe('no');
    });
  });

  describe('indicator', () => {
    it('is yes for the status_led capability', () => {
      expect(sectionRelevance('indicator', { capabilities: ['status_led'] }, null)).toBe('yes');
    });

    it('is yes for the indicator device type', () => {
      expect(sectionRelevance('indicator', { capabilities: [] }, 'indicator')).toBe('yes');
    });

    it('is no for a people_counter device', () => {
      expect(sectionRelevance('indicator', { capabilities: ['people_counter'] }, 'people_counter')).toBe(
        'no',
      );
    });
  });
});
