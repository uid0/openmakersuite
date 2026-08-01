/**
 * Tests for the storage overview — the rack board.
 *
 * The two things this screen has to get right: every tile paints the
 * letter and colour the server sent (a wrong colour here means somebody
 * walks past an expired project), and the staff intake for C/L/E posts
 * what the assign endpoint expects.
 */
import { MantineProvider } from '@mantine/core';
import { ModalsProvider } from '@mantine/modals';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import StorageOverviewPage from '../../pages/StorageOverviewPage';
import {
  sigAPI,
  storageAssignmentsAPI,
  storageOverviewAPI,
  storageSlotsAPI,
} from '../../services/api';
import {
  StorageOverview,
  StorageOverviewCell,
  StorageOverviewRack,
  StorageSlot,
} from '../../types';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    storageOverviewAPI: { get: vi.fn() },
    storageAssignmentsAPI: { list: vi.fn(), assign: vi.fn(), release: vi.fn() },
    storageSlotsAPI: { get: vi.fn() },
    sigAPI: { listMySIGs: vi.fn() },
  };
});

const mockOverview = storageOverviewAPI as jest.Mocked<typeof storageOverviewAPI>;
const mockAssignments = storageAssignmentsAPI as jest.Mocked<typeof storageAssignmentsAPI>;
const mockSlots = storageSlotsAPI as jest.Mocked<typeof storageSlotsAPI>;
const mockSigs = sigAPI as jest.Mocked<typeof sigAPI>;

const ok = <T,>(data: T) =>
  ({
    data,
    status: 200,
    statusText: 'OK',
    headers: {},
    config: {} as never,
  }) as never;

const cell = (overrides: Partial<StorageOverviewCell> = {}): StorageOverviewCell => ({
  code: '1A1',
  slot_id: 1,
  position: 1,
  type: null,
  status: 'empty',
  color: null,
  occupant: '',
  is_active: true,
  ...overrides,
});

const rack = (overrides: Partial<StorageOverviewRack> = {}): StorageOverviewRack => ({
  rack: 1,
  levels: ['B', 'A'],
  max_position: 2,
  rows: [
    { level: 'B', cells: [cell({ code: '1B1', slot_id: 3 }), cell({ code: '1B2', slot_id: 4, position: 2 })] },
    { level: 'A', cells: [cell({ code: '1A1' }), cell({ code: '1A2', slot_id: 2, position: 2 })] },
  ],
  ...overrides,
});

const overview = (racks: StorageOverviewRack[] = [rack()]): StorageOverview => ({
  racks,
  generated_at: '2026-08-01T12:00:00Z',
});

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
  current_assignment: null,
  occupancy_type: null,
  is_occupied: false,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
  ...overrides,
});

// env="test" disables Mantine's transitions — without it the Modal content
// never lands in the DOM. ModalsProvider is what confirmAction opens into.
const renderPage = () =>
  render(
    <MantineProvider env="test">
      <ModalsProvider>
        <MemoryRouter>
          <StorageOverviewPage />
        </MemoryRouter>
      </ModalsProvider>
    </MantineProvider>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  mockOverview.get.mockResolvedValue(ok(overview()));
  mockSlots.get.mockResolvedValue(ok(buildSlot()));
  mockSigs.listMySIGs.mockResolvedValue(ok({ results: [] }));
});

describe('StorageOverviewPage — the grid', () => {
  it('paints the letter and colour the server sent, per cell', async () => {
    mockOverview.get.mockResolvedValue(
      ok(
        overview([
          rack({
            rows: [
              {
                level: 'B',
                cells: [
                  cell({
                    code: '1B1',
                    slot_id: 3,
                    type: 'P',
                    status: 'expired',
                    color: 'red',
                    occupant: 'Alice Aardvark',
                  }),
                  cell({
                    code: '1B2',
                    slot_id: 4,
                    position: 2,
                    type: 'P',
                    status: 'expiring_soon',
                    color: 'yellow',
                    occupant: 'Bob Badger',
                  }),
                ],
              },
              {
                level: 'A',
                cells: [
                  cell({
                    code: '1A1',
                    type: 'C',
                    status: 'occupied',
                    occupant: 'Welding',
                  }),
                  cell({ code: '1A2', slot_id: 2, position: 2 }),
                ],
              },
            ],
          }),
        ]),
      ),
    );

    renderPage();

    await waitFor(() => expect(screen.getByTestId('cell-1B1')).toBeInTheDocument());

    // Expired project — red, and the letter says whose kind of storage it is.
    const expired = screen.getByTestId('cell-1B1');
    expect(expired).toHaveTextContent('P');
    expect(expired).toHaveAttribute('data-tone', 'red');
    expect(expired).toHaveStyle({ background: 'var(--mantine-color-red-6)' });

    // Expiring soon — yellow.
    const expiring = screen.getByTestId('cell-1B2');
    expect(expiring).toHaveAttribute('data-tone', 'yellow');
    expect(expiring).toHaveStyle({ background: 'var(--mantine-color-yellow-4)' });

    // A committee holding is C and plainly occupied: colouring a slot that
    // has been theirs for two years would drown out the red one above.
    const committee = screen.getByTestId('cell-1A1');
    expect(committee).toHaveTextContent('C');
    expect(committee).toHaveAttribute('data-tone', 'occupied');

    // Empty stays blank.
    const empty = screen.getByTestId('cell-1A2');
    expect(empty).toHaveTextContent('');
    expect(empty).toHaveAttribute('data-tone', 'empty');

    // Counts: one committee + two projects in use, one free, two coloured.
    expect(screen.getByTestId('count-attention')).toHaveTextContent('2 need attention');
    expect(screen.getByTestId('count-occupied')).toHaveTextContent('3 in use');
    expect(screen.getByTestId('count-free')).toHaveTextContent('1 free');
  });

  it('lays levels out high-first and leaves a hole where the racking has none', async () => {
    mockOverview.get.mockResolvedValue(
      ok(
        overview([
          rack({
            levels: ['C', 'A'],
            max_position: 3,
            rows: [
              {
                level: 'C',
                // Dense and 1-indexed: position 2 is a hole in the racking.
                cells: [cell({ code: '1C1', slot_id: 5 }), null, cell({ code: '1C3', slot_id: 6, position: 3 })],
              },
              {
                level: 'A',
                cells: [
                  cell({ code: '1A1' }),
                  cell({ code: '1A2', slot_id: 2, position: 2 }),
                  cell({ code: '1A3', slot_id: 7, position: 3 }),
                ],
              },
            ],
          }),
        ]),
      ),
    );

    renderPage();

    await waitFor(() => expect(screen.getByTestId('cell-1C1')).toBeInTheDocument());

    // The hole is a spacer, not a tappable slot.
    expect(screen.getByTestId('cell-gap-1C2')).toBeInTheDocument();
    expect(screen.queryByTestId('cell-1C2')).not.toBeInTheDocument();

    // High shelf first — the grid reads the way the steel does.
    const grid = screen.getByTestId('rack-grid-1');
    const levels = within(grid)
      .getAllByTestId(/^level-label-/)
      .map((node) => node.textContent);
    expect(levels).toEqual(['C', 'A']);
  });

  it('narrows to one rack through the endpoint, keeping the other racks selectable', async () => {
    mockOverview.get.mockResolvedValue(ok(overview([rack(), rack({ rack: 2 })])));

    renderPage();

    await waitFor(() => expect(screen.getByTestId('rack-grid-2')).toBeInTheDocument());
    expect(mockOverview.get).toHaveBeenLastCalledWith(undefined);

    mockOverview.get.mockResolvedValue(ok(overview([rack({ rack: 2 })])));
    const selector = screen.getByTestId('rack-filter');
    fireEvent.click(within(selector).getByText('Rack 2'));

    await waitFor(() => expect(mockOverview.get).toHaveBeenLastCalledWith({ rack: 2 }));
    await waitFor(() => expect(screen.queryByTestId('rack-grid-1')).not.toBeInTheDocument());
    // Rack 1 is still offered even though the narrowed payload never mentions it.
    expect(within(selector).getByText('Rack 1')).toBeInTheDocument();
  });

  it('surfaces a rejected load instead of an empty board', async () => {
    mockOverview.get.mockRejectedValue({
      response: { status: 403, data: { detail: 'Storage admin or staff only.' } },
    });

    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId('overview-error')).toHaveTextContent('Storage admin or staff only.'),
    );
  });
});

describe('StorageOverviewPage — C/L/E staff assignment', () => {
  it('assigns a free slot to a committee', async () => {
    mockSigs.listMySIGs.mockResolvedValue(
      ok({ results: [{ id: 3, name: 'Welding', member_count: 0, asset_count: 0, inventory_count: 0, admins: [], is_user_admin: false }] }),
    );
    mockAssignments.assign.mockResolvedValue(ok({ id: 11 }));

    renderPage();

    await waitFor(() => expect(screen.getByTestId('cell-1A1')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('cell-1A1'));

    // The tile says what to paint; the slot fetch says what can be done with it.
    await waitFor(() => expect(screen.getByTestId('slot-assign-form')).toBeInTheDocument());
    expect(mockSlots.get).toHaveBeenCalledWith('1A1');

    fireEvent.click(await screen.findByPlaceholderText('Pick a committee'));
    fireEvent.click(await screen.findByRole('option', { name: 'Welding' }));
    fireEvent.change(screen.getByTestId('assign-notes'), {
      target: { value: 'Fixture cart lives here' },
    });
    fireEvent.click(screen.getByTestId('assign-submit'));

    await waitFor(() =>
      expect(mockAssignments.assign).toHaveBeenCalledWith({
        slot: '1A1',
        storage_type: 'committee',
        owning_group: 3,
        occupant_label: '',
        notes: 'Fixture cart lives here',
      }),
    );
    // The board reloads so the new C tile is on it.
    await waitFor(() => expect(mockOverview.get).toHaveBeenCalledTimes(2));
  });

  it('assigns logistics/class by free-text occupant, with no committee picker', async () => {
    mockAssignments.assign.mockResolvedValue(ok({ id: 12 }));

    renderPage();

    await waitFor(() => expect(screen.getByTestId('cell-1A1')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('cell-1A1'));
    await waitFor(() => expect(screen.getByTestId('slot-assign-form')).toBeInTheDocument());

    fireEvent.click(screen.getByText('E · Class'));
    expect(screen.queryByTestId('assign-group')).not.toBeInTheDocument();

    fireEvent.change(screen.getByTestId('assign-label'), {
      target: { value: "Ana's CNC class" },
    });
    fireEvent.click(screen.getByTestId('assign-submit'));

    await waitFor(() =>
      expect(mockAssignments.assign).toHaveBeenCalledWith({
        slot: '1A1',
        storage_type: 'class',
        owning_group: null,
        occupant_label: "Ana's CNC class",
        notes: '',
      }),
    );
  });

  it('will not submit a committee assignment that names no committee', async () => {
    renderPage();

    await waitFor(() => expect(screen.getByTestId('cell-1A1')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('cell-1A1'));
    await waitFor(() => expect(screen.getByTestId('slot-assign-form')).toBeInTheDocument());

    // Same rule the server enforces: a committee holding with neither the
    // group nor a label is just a blocked slot.
    expect(screen.getByTestId('assign-submit')).toBeDisabled();
    fireEvent.change(screen.getByTestId('assign-label'), { target: { value: 'Metal shop crew' } });
    expect(screen.getByTestId('assign-submit')).not.toBeDisabled();
  });

  it('shows the conflict when the slot was taken between the load and the click', async () => {
    mockAssignments.assign.mockRejectedValue({
      response: {
        status: 409,
        data: { detail: 'Slot 1A1 is already held by Alice Aardvark.', code: 'slot_occupied' },
      },
    });

    renderPage();

    await waitFor(() => expect(screen.getByTestId('cell-1A1')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('cell-1A1'));
    await waitFor(() => expect(screen.getByTestId('slot-assign-form')).toBeInTheDocument());

    fireEvent.change(screen.getByTestId('assign-label'), { target: { value: 'Welding' } });
    fireEvent.click(screen.getByTestId('assign-submit'));

    await waitFor(() =>
      expect(screen.getByTestId('slot-detail-error')).toHaveTextContent(
        'Slot 1A1 is already held by Alice Aardvark.',
      ),
    );
  });

  it('releases a held slot by the assignment id the slot carries', async () => {
    mockOverview.get.mockResolvedValue(
      ok(
        overview([
          rack({
            levels: ['A'],
            max_position: 1,
            rows: [
              {
                level: 'A',
                cells: [cell({ code: '1A1', type: 'C', status: 'occupied', occupant: 'Welding' })],
              },
            ],
          }),
        ]),
      ),
    );
    mockSlots.get.mockResolvedValue(
      ok(
        buildSlot({
          is_occupied: true,
          occupancy_type: 'C',
          current_assignment: {
            id: 42,
            storage_type: 'committee',
            type_letter: 'C',
            occupant_display: 'Welding',
            assigned_at: '2026-05-02T00:00:00Z',
          },
        }),
      ),
    );
    mockAssignments.release.mockResolvedValue(ok({ id: 42 }));

    renderPage();

    await waitFor(() => expect(screen.getByTestId('cell-1A1')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('cell-1A1'));

    await waitFor(() => expect(screen.getByTestId('slot-detail-assignment')).toBeInTheDocument());
    // No assign form over an occupied slot.
    expect(screen.queryByTestId('slot-assign-form')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('slot-release'));
    fireEvent.click(await screen.findByRole('button', { name: 'Release' }));

    await waitFor(() => expect(mockAssignments.release).toHaveBeenCalledWith(42));
    await waitFor(() => expect(mockOverview.get).toHaveBeenCalledTimes(2));
  });

  it('links a project tile to the stint rather than offering to assign it', async () => {
    mockOverview.get.mockResolvedValue(
      ok(
        overview([
          rack({
            levels: ['A'],
            max_position: 1,
            rows: [
              {
                level: 'A',
                cells: [
                  cell({
                    code: '1A1',
                    type: 'P',
                    status: 'expired',
                    color: 'red',
                    occupant: 'Alice Aardvark',
                  }),
                ],
              },
            ],
          }),
        ]),
      ),
    );
    mockSlots.get.mockResolvedValue(
      ok(
        buildSlot({
          is_occupied: true,
          occupancy_type: 'P',
          current_stint: {
            id: 7,
            stint_id: 'PS-AB23CDFG',
            username: 'alice',
            display_name: 'Alice Aardvark',
            project_title: 'Big Sculpture',
            started_at: '2026-06-01T00:00:00Z',
            expires_at: '2026-07-01T00:00:00Z',
            status: 'expired',
          },
        }),
      ),
    );

    renderPage();

    await waitFor(() => expect(screen.getByTestId('cell-1A1')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('cell-1A1'));

    await waitFor(() => expect(screen.getByTestId('slot-detail-stint')).toBeInTheDocument());
    expect(screen.getByTestId('slot-detail-stint-link')).toHaveAttribute(
      'href',
      '/facilities/project-storage/PS-AB23CDFG',
    );
    expect(screen.getByTestId('slot-detail-status')).toHaveTextContent('Expired');
    expect(screen.queryByTestId('slot-assign-form')).not.toBeInTheDocument();
  });

  it('sends a retired slot back to the console instead of assigning it', async () => {
    mockOverview.get.mockResolvedValue(
      ok(
        overview([
          rack({
            levels: ['A'],
            max_position: 1,
            rows: [{ level: 'A', cells: [cell({ code: '1A1', is_active: false })] }],
          }),
        ]),
      ),
    );
    mockSlots.get.mockResolvedValue(ok(buildSlot({ is_active: false })));

    renderPage();

    await waitFor(() => expect(screen.getByTestId('cell-1A1')).toBeInTheDocument());
    expect(screen.getByTestId('cell-1A1')).toHaveAttribute('data-tone', 'retired');

    fireEvent.click(screen.getByTestId('cell-1A1'));
    await waitFor(() => expect(screen.getByTestId('slot-detail-retired')).toBeInTheDocument());
    expect(screen.queryByTestId('slot-assign-form')).not.toBeInTheDocument();
  });
});
