/**
 * Tests for electricalCircuitsAPI client (oms-a5f).
 *
 * Verifies the wire format hitting the backend at /api/electrical-circuits/...
 * — particularly that file uploads switch the request to multipart/form-data,
 * which the OutletViewSet/NetworkDropViewSet require for the photo field.
 */
import MockAdapter from 'axios-mock-adapter';
import { electricalCircuitsAPI } from '../../services/api';
import api from '../../services/api';

describe('electricalCircuitsAPI', () => {
  let mock: MockAdapter;

  beforeEach(() => {
    mock = new MockAdapter(api);
  });

  afterEach(() => {
    mock.restore();
  });

  it('lists breakers with location and panel filters', async () => {
    mock.onGet('/electrical-circuits/breakers/').reply((config) => {
      expect(config.params).toEqual({ location: 7, panel: 'A' });
      return [200, { results: [] }];
    });
    const response = await electricalCircuitsAPI.listBreakers({
      location: 7,
      panel: 'A',
    });
    expect(response.data.results).toEqual([]);
  });

  it('normalizes plain-array list responses for outlets', async () => {
    mock.onGet('/electrical-circuits/outlets/').reply(200, [
      {
        id: 1,
        identifier: 'bench-1',
        location: 1,
        location_name: 'Shop',
        outlet_type: 'standard',
      },
    ]);
    const response = await electricalCircuitsAPI.listOutlets();
    expect(response.data.results).toHaveLength(1);
    expect(response.data.results[0].identifier).toBe('bench-1');
  });

  it('uses multipart/form-data when creating an outlet with a photo', async () => {
    let capturedHeaders: Record<string, unknown> | undefined;
    let capturedBody: unknown;
    mock.onPost('/electrical-circuits/outlets/').reply((config) => {
      capturedHeaders = config.headers as Record<string, unknown>;
      capturedBody = config.data;
      return [201, { id: 99 }];
    });

    const photo = new File([new Uint8Array([0])], 'outlet.png', { type: 'image/png' });
    await electricalCircuitsAPI.createOutlet({
      location: 1,
      identifier: 'bench-1',
      outlet_type: 'standard',
      photo,
    });

    expect(capturedHeaders?.['Content-Type']).toBe('multipart/form-data');
    expect(capturedBody).toBeInstanceOf(FormData);
    const fd = capturedBody as FormData;
    expect(fd.get('identifier')).toBe('bench-1');
    expect(fd.get('photo')).toBeInstanceOf(File);
  });

  it('sends a JSON body when creating an outlet without a photo', async () => {
    let capturedBody: unknown;
    mock.onPost('/electrical-circuits/outlets/').reply((config) => {
      capturedBody = config.data;
      return [201, { id: 100 }];
    });
    await electricalCircuitsAPI.createOutlet({
      location: 1,
      identifier: 'bench-2',
      outlet_type: 'standard',
    });
    expect(typeof capturedBody).toBe('string');
    expect(JSON.parse(capturedBody as string)).toMatchObject({
      identifier: 'bench-2',
    });
  });

  it('passes drop_type filter on network drop list', async () => {
    mock.onGet('/electrical-circuits/network-drops/').reply((config) => {
      expect(config.params).toEqual({ drop_type: 'camera' });
      return [200, { results: [] }];
    });
    await electricalCircuitsAPI.listNetworkDrops({ drop_type: 'camera' });
  });
});
