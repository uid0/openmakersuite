/**
 * Tools editor on the PM-template form (op-67q5).
 *
 * A maintainer enters the tools a task needs here; they persist against the
 * template and are what the work order prints up front. The editor mirrors the
 * materials editor: add rows, remove rows, and diff against what was loaded so
 * an edit only creates what is new and deletes what was dropped.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import MaintenanceItemFormPage from '../../pages/MaintenanceItemFormPage';
import { maintenanceAPI, maintenanceToolAPI } from '../../services/api';
import { MaintenanceItem, MaintenanceTool } from '../../types';

vi.mock('../../services/api');

const mockUseParams = vi.fn<() => { assetId?: string; id?: string }>(() => ({
  assetId: 'a-1',
}));
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useParams: () => mockUseParams(),
  useNavigate: () => mockNavigate,
}));

const mockMaintenanceAPI = maintenanceAPI as jest.Mocked<typeof maintenanceAPI>;
const mockToolAPI = maintenanceToolAPI as jest.Mocked<typeof maintenanceToolAPI>;

const okResponse = <T,>(data: T) =>
  ({ data, status: 200, statusText: 'OK', headers: {}, config: {} as never }) as never;

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <MaintenanceItemFormPage />
      </MemoryRouter>
    </MantineProvider>,
  );

const buildTool = (overrides: Partial<MaintenanceTool> = {}): MaintenanceTool => ({
  id: 't-1',
  maintenance_item: 'mi-1',
  inventory_item: null,
  inventory_item_detail: null,
  name: 'Torque wrench',
  quantity: 1,
  location_hint: 'Tool crib, drawer 3',
  is_required: true,
  notes: '',
  ...overrides,
});

const buildItem = (tools: MaintenanceTool[]): MaintenanceItem =>
  ({
    id: 'mi-1',
    asset: 'a-1',
    title: 'Quarterly belt inspection',
    description: '',
    instructions: '',
    estimated_time_minutes: null,
    estimated_cost: '0.00',
    interval_days: 90,
    is_active: true,
    materials: [],
    tools,
    tasks: [],
  }) as unknown as MaintenanceItem;

/** Fill the "Add Tool" row and click Add. */
const addTool = (name: string, location = '') => {
  fireEvent.change(screen.getByLabelText('Tool'), { target: { value: name } });
  if (location) {
    fireEvent.change(screen.getByLabelText('Location'), { target: { value: location } });
  }
  fireEvent.click(screen.getByRole('button', { name: /add tool/i }));
};

beforeEach(() => {
  jest.clearAllMocks();
  mockUseParams.mockReturnValue({ assetId: 'a-1' });
  mockToolAPI.create.mockResolvedValue(okResponse(buildTool()));
  mockToolAPI.delete.mockResolvedValue(okResponse({}));
});

describe('MaintenanceItemFormPage — tools editor (op-67q5)', () => {
  it('adds a tool row and clears the entry fields', async () => {
    renderPage();

    await screen.findByLabelText(/title/i);
    addTool('Torque wrench', 'Tool crib, drawer 3');

    const table = screen.getByRole('table');
    expect(within(table).getByText('Torque wrench')).toBeInTheDocument();
    expect(within(table).getByText('Tool crib, drawer 3')).toBeInTheDocument();
    // The entry row resets so the next tool can be typed straight in.
    expect((screen.getByLabelText('Tool') as HTMLInputElement).value).toBe('');
    expect((screen.getByLabelText('Location') as HTMLInputElement).value).toBe('');
  });

  it('will not add a nameless tool', async () => {
    renderPage();

    await screen.findByLabelText(/title/i);
    expect(screen.getByRole('button', { name: /add tool/i })).toBeDisabled();
  });

  it('removes a tool row', async () => {
    renderPage();

    await screen.findByLabelText(/title/i);
    addTool('Torque wrench');
    addTool('Shop vacuum');

    fireEvent.click(screen.getAllByRole('button', { name: /remove tool/i })[0]);

    expect(screen.queryByText('Torque wrench')).not.toBeInTheDocument();
    expect(screen.getByText('Shop vacuum')).toBeInTheDocument();
  });

  it('creates the tools entered on a new task once the task is saved', async () => {
    mockMaintenanceAPI.createItem.mockResolvedValue(okResponse({ id: 'mi-new' }));

    renderPage();

    fireEvent.change(await screen.findByLabelText(/title/i), {
      target: { value: 'Replace air filter' },
    });
    addTool('Torque wrench', 'Tool crib, drawer 3');
    fireEvent.click(screen.getByRole('button', { name: /create task/i }));

    await waitFor(() => {
      expect(mockToolAPI.create).toHaveBeenCalledWith({
        maintenance_item: 'mi-new',
        name: 'Torque wrench',
        quantity: 1,
        location_hint: 'Tool crib, drawer 3',
        is_required: true,
        notes: '',
      });
    });
  });

  it('loads the template tools when editing and deletes only the removed ones', async () => {
    mockUseParams.mockReturnValue({ assetId: 'a-1', id: 'mi-1' });
    mockMaintenanceAPI.getItem.mockResolvedValue(
      okResponse(
        buildItem([
          buildTool(),
          buildTool({ id: 't-2', name: 'Shop vacuum', location_hint: 'Under bench 2' }),
        ]),
      ),
    );
    mockMaintenanceAPI.updateItem.mockResolvedValue(okResponse({ id: 'mi-1' }));

    renderPage();

    // Both persisted tools render on load.
    expect(await screen.findByText('Torque wrench')).toBeInTheDocument();
    expect(screen.getByText('Shop vacuum')).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole('button', { name: /remove tool/i })[0]);
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => {
      expect(mockToolAPI.delete).toHaveBeenCalledWith('t-1');
    });
    // The surviving row is untouched — no delete, no redundant re-create.
    expect(mockToolAPI.delete).toHaveBeenCalledTimes(1);
    expect(mockToolAPI.create).not.toHaveBeenCalled();
  });
});
