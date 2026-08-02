/**
 * Kiosk slot pre-fill — the other end of the printed rack card.
 *
 * A slot card's QR is `…/project-storage/kiosk?slot=<code>`, so scanning
 * it off the upright must land the member here with that slot chosen and
 * send it with the claim. `?slot_id=<pk>` is the same path by primary key.
 *
 * The lookup that fills in the slot's details is deliberately conditional:
 * /slots/ is staff-gated and this page is public, so an anonymous kiosk
 * must not fire it (a 401 would trip the api client's session-expired
 * handling). These tests pin both halves of that.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import ProjectStorageKioskPage from '../../pages/ProjectStorageKioskPage';
import { projectStorageAPI, storageSlotsAPI } from '../../services/api';
import { ProjectStorageStint, StorageSlot } from '../../types';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    projectStorageAPI: {
      start: vi.fn(),
      labelUrl: vi.fn(() => 'https://labels.example/brother_ql.png'),
    },
    storageSlotsAPI: { get: vi.fn() },
  };
});

const mockStart = projectStorageAPI.start as jest.Mock;
const mockSlotGet = storageSlotsAPI.get as jest.Mock;

const buildStint = (overrides: Partial<ProjectStorageStint> = {}): ProjectStorageStint => ({
  id: 1,
  stint_id: 'PS-AB23CDFG',
  username: 'alice',
  first_name: 'Alice',
  last_name: '',
  email: '',
  display_name: 'Alice',
  project_title: '',
  started_at: '2026-07-01T00:00:00Z',
  expires_at: '2026-07-31T00:00:00Z',
  removed_at: null,
  notice_sent_at: null,
  moved_to_purgatory_at: null,
  storage_location_name: '',
  purgatory_location_name: '',
  slot: 5,
  slot_code: '1A1',
  location_display: '1A1',
  notes: '',
  status: 'active',
  purgatory_at: null,
  expiry_week: 31,
  expiry_day_of_year: 212,
  events: [],
  qr_code_url: null,
  april_tag_id: null,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
  ...overrides,
});

const buildSlot = (overrides: Partial<StorageSlot> = {}): StorageSlot => ({
  id: 5,
  code: '1A1',
  rack: 1,
  level: 'A',
  position: 1,
  requires_pallet_jack: false,
  is_active: true,
  owning_group: null,
  owning_group_name: '',
  notes: '',
  april_tag_id: 101,
  current_stint: null,
  current_assignment: null,
  occupancy_type: null,
  is_occupied: false,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
  ...overrides,
});

const renderKiosk = (search = '') =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={[`/project-storage/kiosk${search}`]}>
        <ProjectStorageKioskPage />
      </MemoryRouter>
    </MantineProvider>,
  );

const enterUsername = () =>
  fireEvent.change(screen.getByTestId('kiosk-username-input'), {
    target: { value: 'alice' },
  });

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

describe('ProjectStorageKioskPage — slot pre-fill', () => {
  it('shows the slot from ?slot= and claims it on submit', async () => {
    mockStart.mockResolvedValue({ data: buildStint() });

    renderKiosk('?slot=1A1');

    // Slot shown from the URL, and the free-text location field steps
    // aside — the slot *is* the location.
    expect(screen.getByTestId('kiosk-slot-code')).toHaveTextContent('Slot 1A1');
    expect(screen.queryByTestId('kiosk-storage-location')).not.toBeInTheDocument();

    enterUsername();
    fireEvent.click(screen.getByTestId('kiosk-start-button'));

    await waitFor(() => expect(mockStart).toHaveBeenCalled());
    expect(mockStart).toHaveBeenCalledWith(
      expect.objectContaining({ username: 'alice', slot: '1A1' }),
    );

    // The confirmation names the slot, not the blank free-text location.
    await waitFor(() =>
      expect(screen.getByTestId('kiosk-result-location')).toHaveTextContent('slot 1A1'),
    );
  });

  it('accepts ?slot_id=<pk> and sends it under the same key', async () => {
    mockStart.mockResolvedValue({ data: buildStint() });

    renderKiosk('?slot_id=5');

    expect(screen.getByTestId('kiosk-slot-code')).toHaveTextContent('Slot 5');

    enterUsername();
    fireEvent.click(screen.getByTestId('kiosk-start-button'));

    await waitFor(() =>
      expect(mockStart).toHaveBeenCalledWith(expect.objectContaining({ slot: '5' })),
    );
  });

  it('sends no slot when the member opens the kiosk without scanning', async () => {
    mockStart.mockResolvedValue({ data: buildStint({ slot: null, slot_code: '' }) });

    renderKiosk();

    expect(screen.queryByTestId('kiosk-slot-banner')).not.toBeInTheDocument();
    expect(screen.getByTestId('kiosk-storage-location')).toBeInTheDocument();

    enterUsername();
    fireEvent.click(screen.getByTestId('kiosk-start-button'));

    await waitFor(() => expect(mockStart).toHaveBeenCalled());
    expect(mockStart.mock.calls[0][0].slot).toBeUndefined();
  });

  it('lets a wrong scan be cleared back to a free-text location', async () => {
    mockStart.mockResolvedValue({ data: buildStint() });

    renderKiosk('?slot=1A1');
    fireEvent.click(screen.getByTestId('kiosk-clear-slot'));

    // Cleared state sticks — the ?slot= seed must not re-apply.
    expect(screen.queryByTestId('kiosk-slot-banner')).not.toBeInTheDocument();
    fireEvent.change(screen.getByTestId('kiosk-storage-location'), {
      target: { value: 'Shelf A' },
    });

    enterUsername();
    fireEvent.click(screen.getByTestId('kiosk-start-button'));

    await waitFor(() => expect(mockStart).toHaveBeenCalled());
    const payload = mockStart.mock.calls[0][0];
    expect(payload.slot).toBeUndefined();
    expect(payload.storage_location_name).toBe('Shelf A');
  });
});

describe('ProjectStorageKioskPage — slot detail lookup', () => {
  it('does not call the staff-gated slots endpoint for an anonymous kiosk', async () => {
    mockStart.mockResolvedValue({ data: buildStint() });

    renderKiosk('?slot=1A1');

    await waitFor(() => expect(screen.getByTestId('kiosk-slot-code')).toBeInTheDocument());
    expect(mockSlotGet).not.toHaveBeenCalled();
  });

  it('fills in the details when a signed-in warden is working the rack', async () => {
    mockSlotGet.mockResolvedValue({ data: buildSlot({ requires_pallet_jack: true }) });
    localStorage.setItem('token', 'jwt');

    renderKiosk('?slot=1A1');

    await waitFor(() => expect(mockSlotGet).toHaveBeenCalledWith('1A1'));
    expect(await screen.findByText(/needs a pallet jack/i)).toBeInTheDocument();
  });
});

describe('ProjectStorageKioskPage — 409 slot_occupied', () => {
  it('names the slot and the stint already in it', async () => {
    mockStart.mockRejectedValue({
      isAxiosError: true,
      response: {
        status: 409,
        data: {
          detail:
            'Slot 1A1 is already holding stint PS-ZZ99YYXX (Bob Badger). Pick a free ' +
            'slot or ask the storage warden to clear that one.',
          code: 'slot_occupied',
          slot_code: '1A1',
          occupied_by: 'PS-ZZ99YYXX',
        },
      },
    });

    renderKiosk('?slot=1A1');
    enterUsername();
    fireEvent.click(screen.getByTestId('kiosk-start-button'));

    const rejection = await screen.findByTestId('kiosk-rejection');
    expect(rejection).toHaveTextContent('That slot is already taken');
    expect(rejection).toHaveTextContent('PS-ZZ99YYXX');
    expect(screen.getByTestId('kiosk-slot-occupied-hint')).toHaveTextContent('Slot 1A1');
  });
});
