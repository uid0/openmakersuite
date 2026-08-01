/**
 * Tests for the storage-slot warden console.
 *
 * The load covers pagination (DRF's page size is fixed at 50 here, so a
 * rack is several pages), the occupancy readout, the generate-rack form's
 * request shape, and the two print paths (selected slots vs a whole rack).
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import StorageSlotsPage from '../../pages/StorageSlotsPage';
import { storageSlotsAPI } from '../../services/api';
import { StorageSlot } from '../../types';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    storageSlotsAPI: {
      list: vi.fn(),
      get: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      remove: vi.fn(),
      generateRack: vi.fn(),
      cardPreview: vi.fn(),
      printCards: vi.fn(),
    },
  };
});

const mockAPI = storageSlotsAPI as jest.Mocked<typeof storageSlotsAPI>;

const buildSlot = (overrides: Partial<StorageSlot> = {}): StorageSlot => ({
  id: 1,
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
  is_occupied: false,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
  ...overrides,
});

// Same shape the other project-storage tests use — an AxiosResponse the
// mocked client can return without dragging in the real axios types.
const ok = <T,>(data: T) =>
  ({
    data,
    status: 200,
    statusText: 'OK',
    headers: {},
    config: {} as never,
  }) as never;

const page = (results: StorageSlot[], next: string | null = null) =>
  ok({ count: results.length, next, previous: null, results });

const renderPage = () =>
  render(
    // env="test" disables Mantine's transitions — without it the Modal
    // content never lands in the DOM for the query below.
    <MantineProvider env="test">
      <MemoryRouter>
        <StorageSlotsPage />
      </MemoryRouter>
    </MantineProvider>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  mockAPI.list.mockResolvedValue(page([]));
});

describe('StorageSlotsPage — listing', () => {
  it('groups slots by rack and shows who holds an occupied one', async () => {
    mockAPI.list.mockResolvedValue(
      page([
        buildSlot({ id: 1, code: '1A1' }),
        buildSlot({
          id: 2,
          code: '1A2',
          position: 2,
          april_tag_id: 102,
          is_occupied: true,
          current_stint: {
            id: 7,
            stint_id: 'PS-AB23CDFG',
            username: 'alice',
            display_name: 'Alice Aardvark',
            project_title: 'Big Sculpture',
            started_at: '2026-07-01T00:00:00Z',
            expires_at: '2026-07-31T00:00:00Z',
            status: 'active',
          },
        }),
        buildSlot({ id: 3, code: '2A1', rack: 2, april_tag_id: 201 }),
      ]),
    );

    renderPage();

    await waitFor(() => expect(screen.getByTestId('slot-row-1A1')).toBeInTheDocument());
    expect(screen.getByTestId('rack-header-1')).toBeInTheDocument();
    expect(screen.getByTestId('rack-header-2')).toBeInTheDocument();

    // The occupant is named and links to the warden's stint page.
    const occupant = screen.getByTestId('occupant-1A2');
    expect(occupant).toHaveTextContent('Alice Aardvark');
    expect(occupant).toHaveAttribute(
      'href',
      '/facilities/project-storage/PS-AB23CDFG',
    );
    // …and the free one reads free.
    expect(within(screen.getByTestId('slot-row-1A1')).getByText('Free')).toBeInTheDocument();
    // AprilTag ID is on the row so a warden can match a scan to a slot.
    expect(within(screen.getByTestId('slot-row-1A1')).getByText('#101')).toBeInTheDocument();
  });

  it('walks every page — the server page size is fixed, not negotiable', async () => {
    mockAPI.list
      .mockResolvedValueOnce(page([buildSlot({ id: 1, code: '1A1' })], 'http://x/?page=2'))
      .mockResolvedValueOnce(page([buildSlot({ id: 2, code: '1A2', position: 2 })]));

    renderPage();

    await waitFor(() => expect(screen.getByTestId('slot-row-1A2')).toBeInTheDocument());
    expect(mockAPI.list).toHaveBeenCalledTimes(2);
    expect(mockAPI.list.mock.calls[0][0]).toEqual(expect.objectContaining({ page: 1 }));
    expect(mockAPI.list.mock.calls[1][0]).toEqual(expect.objectContaining({ page: 2 }));
  });

  it('defaults to hiding retired slots and forwards the occupancy filter', async () => {
    renderPage();
    await waitFor(() => expect(mockAPI.list).toHaveBeenCalled());
    expect(mockAPI.list.mock.calls[0][0]).toEqual(
      expect.objectContaining({ is_active: 'true' }),
    );

    mockAPI.list.mockClear();
    // "Free" is the warden's "what can I hand out?" query.
    fireEvent.click(screen.getByText('Free'));

    await waitFor(() => expect(mockAPI.list).toHaveBeenCalled());
    expect(mockAPI.list.mock.calls[0][0]).toEqual(
      expect.objectContaining({ occupied: 'false' }),
    );
  });
});

describe('StorageSlotsPage — generate rack', () => {
  it('posts the rack number and one entry per level, pallet jack included', async () => {
    mockAPI.generateRack.mockResolvedValue(
      ok({
        rack: 3,
        created: ['3A1'],
        skipped: [],
        created_count: 12,
        skipped_count: 0,
        without_tag: [],
        slots: [],
      }),
    );

    renderPage();
    await waitFor(() => expect(mockAPI.list).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId('open-generate-rack'));

    fireEvent.change(screen.getByTestId('generate-rack-number'), {
      target: { value: '3' },
    });
    // Level A stays as seeded (12 positions, ground level); add a high one
    // that needs the jack.
    fireEvent.click(screen.getByTestId('generate-add-level'));
    fireEvent.change(screen.getByTestId('generate-level-1'), { target: { value: 'y' } });
    fireEvent.change(screen.getByTestId('generate-positions-1'), {
      target: { value: '10' },
    });
    fireEvent.click(screen.getByTestId('generate-jack-1'));

    fireEvent.click(screen.getByTestId('generate-submit'));

    await waitFor(() => expect(mockAPI.generateRack).toHaveBeenCalled());
    expect(mockAPI.generateRack).toHaveBeenCalledWith({
      rack: 3,
      levels: [
        { level: 'A', positions: 12, requires_pallet_jack: false },
        // Upper-cased before it goes out, so the summary names what the
        // backend actually created.
        { level: 'Y', positions: 10, requires_pallet_jack: true },
      ],
      notes: '',
    });

    // The run's report is shown, and the list reloads to pick up the slots.
    await waitFor(() => expect(screen.getByTestId('generate-summary')).toBeInTheDocument());
    expect(screen.getByTestId('generate-summary')).toHaveTextContent('Created 12');
  });

  it('warns when the tag family ran dry mid-run', async () => {
    mockAPI.generateRack.mockResolvedValue(
      ok({
        rack: 4,
        created: ['4A1', '4A2'],
        skipped: ['4A3'],
        created_count: 2,
        skipped_count: 1,
        without_tag: ['4A2'],
        slots: [],
      }),
    );

    renderPage();
    await waitFor(() => expect(mockAPI.list).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId('open-generate-rack'));
    fireEvent.click(screen.getByTestId('generate-submit'));

    await waitFor(() => expect(screen.getByTestId('generate-summary')).toBeInTheDocument());
    const summary = screen.getByTestId('generate-summary');
    expect(summary).toHaveTextContent('1 already existed');
    expect(summary).toHaveTextContent('No AprilTag left for 4A2');
  });
});

describe('StorageSlotsPage — printing cards', () => {
  beforeEach(() => {
    Object.defineProperty(window.URL, 'createObjectURL', {
      value: vi.fn(() => 'blob:mock'),
      writable: true,
    });
    Object.defineProperty(window.URL, 'revokeObjectURL', { value: vi.fn(), writable: true });
    mockAPI.printCards.mockResolvedValue(ok(new Blob(['%PDF-1.4'])));
  });

  it('prints the selected slots by id', async () => {
    mockAPI.list.mockResolvedValue(
      page([
        buildSlot({ id: 11, code: '1A1' }),
        buildSlot({ id: 12, code: '1A2', position: 2 }),
      ]),
    );

    renderPage();
    await waitFor(() => expect(screen.getByTestId('slot-row-1A2')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('select-slot-1A2'));
    fireEvent.click(screen.getByTestId('print-selected'));

    await waitFor(() => expect(mockAPI.printCards).toHaveBeenCalledWith({ slot_ids: [12] }));
    expect(window.URL.createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
  });

  it('prints a whole rack through the rack filter, not a list of ids', async () => {
    mockAPI.list.mockResolvedValue(page([buildSlot({ id: 11, code: '1A1' })]));

    renderPage();
    await waitFor(() => expect(screen.getByTestId('rack-header-1')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('print-rack-1'));

    await waitFor(() => expect(mockAPI.printCards).toHaveBeenCalledWith({ rack: 1 }));
  });
});
