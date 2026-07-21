/**
 * Task-steps editor on the PM-template form (op-syov).
 *
 * Steps are the numbered checklist the work order prints and scans back, and
 * each one can carry a *reference* photo — the instructional "this is what it
 * should look like" shot. Until now there was no way to enter steps outside
 * Django admin at all; this editor mirrors the tools/materials ones (add rows,
 * remove rows, diff against what was loaded) and adds a per-row photo.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import MaintenanceItemFormPage from '../../pages/MaintenanceItemFormPage';
import { maintenanceAPI, maintenanceTaskAPI } from '../../services/api';
import { MaintenanceItem, MaintenanceTask } from '../../types';

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
const mockTaskAPI = maintenanceTaskAPI as jest.Mocked<typeof maintenanceTaskAPI>;

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

const buildTask = (overrides: Partial<MaintenanceTask> = {}): MaintenanceTask =>
  ({
    id: 'st-1',
    maintenance_item: 'mi-1',
    order: 0,
    title: 'Disconnect power',
    description: 'Breaker 12, panel A',
    is_required: true,
    reference_image_url: null,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }) as MaintenanceTask;

const buildItem = (tasks: MaintenanceTask[]): MaintenanceItem =>
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
    tools: [],
    tasks,
  }) as unknown as MaintenanceItem;

const imageFile = (name = 'belt.jpg') =>
  new File(['fake-jpeg-bytes'], name, { type: 'image/jpeg' });

/** Fill the "Add Step" row and click Add Step. */
const addStep = (title: string, details = '') => {
  fireEvent.change(screen.getByLabelText('Step'), { target: { value: title } });
  if (details) {
    fireEvent.change(screen.getByLabelText('Details'), { target: { value: details } });
  }
  fireEvent.click(screen.getByRole('button', { name: /add step/i }));
};

/** Pick a file through one of the FileButton-backed hidden inputs. */
const pickPhoto = (button: HTMLElement, file: File) => {
  const input = button.parentElement?.querySelector(
    'input[type="file"]',
  ) as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
};

const stepsTable = () =>
  screen.getAllByRole('table').find((t) => within(t).queryByText('Reference photo')) as HTMLElement;

beforeEach(() => {
  jest.clearAllMocks();
  mockUseParams.mockReturnValue({ assetId: 'a-1' });
  mockTaskAPI.createTask.mockResolvedValue(okResponse(buildTask()));
  mockTaskAPI.updateTask.mockResolvedValue(okResponse(buildTask()));
  mockTaskAPI.deleteTask.mockResolvedValue(okResponse({}));
});

describe('MaintenanceItemFormPage — task steps editor (op-syov)', () => {
  it('adds a step row and clears the entry fields', async () => {
    renderPage();

    await screen.findByLabelText(/title/i);
    addStep('Disconnect power', 'Breaker 12, panel A');

    const table = stepsTable();
    expect(within(table).getByText('Disconnect power')).toBeInTheDocument();
    expect(within(table).getByText('Breaker 12, panel A')).toBeInTheDocument();
    expect((screen.getByLabelText('Step') as HTMLInputElement).value).toBe('');
    expect((screen.getByLabelText('Details') as HTMLInputElement).value).toBe('');
  });

  it('will not add a nameless step', async () => {
    renderPage();

    await screen.findByLabelText(/title/i);
    expect(screen.getByRole('button', { name: /add step/i })).toBeDisabled();
  });

  it('removes a step row', async () => {
    renderPage();

    await screen.findByLabelText(/title/i);
    addStep('Disconnect power');
    addStep('Inspect belt');

    fireEvent.click(screen.getAllByRole('button', { name: /remove step/i })[0]);

    expect(screen.queryByText('Disconnect power')).not.toBeInTheDocument();
    expect(screen.getByText('Inspect belt')).toBeInTheDocument();
  });

  it('creates the steps entered on a new task, numbered by row position', async () => {
    mockMaintenanceAPI.createItem.mockResolvedValue(okResponse({ id: 'mi-new' }));

    renderPage();

    fireEvent.change(await screen.findByLabelText(/title/i), {
      target: { value: 'Replace air filter' },
    });
    addStep('Disconnect power', 'Breaker 12, panel A');
    addStep('Inspect belt');
    fireEvent.click(screen.getByRole('button', { name: /create task/i }));

    await waitFor(() => {
      expect(mockTaskAPI.createTask).toHaveBeenCalledWith({
        maintenance_item: 'mi-new',
        order: 0,
        title: 'Disconnect power',
        description: 'Breaker 12, panel A',
        is_required: true,
      });
    });
    expect(mockTaskAPI.createTask).toHaveBeenCalledWith({
      maintenance_item: 'mi-new',
      order: 1,
      title: 'Inspect belt',
      description: '',
      is_required: true,
    });
  });

  it('sends a reference photo picked for a new step', async () => {
    mockMaintenanceAPI.createItem.mockResolvedValue(okResponse({ id: 'mi-new' }));
    const file = imageFile();

    renderPage();

    fireEvent.change(await screen.findByLabelText(/title/i), {
      target: { value: 'Replace air filter' },
    });
    pickPhoto(screen.getByRole('button', { name: /reference photo for new step/i }), file);
    addStep('Inspect belt');
    fireEvent.click(screen.getByRole('button', { name: /create task/i }));

    await waitFor(() => {
      expect(mockTaskAPI.createTask).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'Inspect belt', reference_image: file }),
      );
    });
  });

  it('attaches a photo to an already-added row', async () => {
    mockMaintenanceAPI.createItem.mockResolvedValue(okResponse({ id: 'mi-new' }));
    const file = imageFile('after.jpg');

    renderPage();

    fireEvent.change(await screen.findByLabelText(/title/i), {
      target: { value: 'Replace air filter' },
    });
    addStep('Inspect belt');
    pickPhoto(screen.getByRole('button', { name: /photo for step 1/i }), file);

    // The row shows the pending file, then it rides along on save.
    expect(within(stepsTable()).getByText('after.jpg')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /create task/i }));

    await waitFor(() => {
      expect(mockTaskAPI.createTask).toHaveBeenCalledWith(
        expect.objectContaining({ reference_image: file }),
      );
    });
  });

  it('loads the template steps when editing and deletes only the removed ones', async () => {
    mockUseParams.mockReturnValue({ assetId: 'a-1', id: 'mi-1' });
    mockMaintenanceAPI.getItem.mockResolvedValue(
      okResponse(
        buildItem([
          buildTask(),
          buildTask({ id: 'st-2', order: 1, title: 'Inspect belt', description: '' }),
        ]),
      ),
    );
    mockMaintenanceAPI.updateItem.mockResolvedValue(okResponse({ id: 'mi-1' }));

    renderPage();

    expect(await screen.findByText('Disconnect power')).toBeInTheDocument();
    expect(screen.getByText('Inspect belt')).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole('button', { name: /remove step/i })[0]);
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => {
      expect(mockTaskAPI.deleteTask).toHaveBeenCalledWith('st-1');
    });
    expect(mockTaskAPI.deleteTask).toHaveBeenCalledTimes(1);
    expect(mockTaskAPI.createTask).not.toHaveBeenCalled();
    // The survivor moved from position 1 to position 0, so it is renumbered.
    expect(mockTaskAPI.updateTask).toHaveBeenCalledWith('st-2', { order: 0 });
  });

  it('shows the saved reference photo and only re-uploads when it is replaced', async () => {
    mockUseParams.mockReturnValue({ assetId: 'a-1', id: 'mi-1' });
    mockMaintenanceAPI.getItem.mockResolvedValue(
      okResponse(
        buildItem([buildTask({ reference_image_url: 'http://api.test/media/step.jpg' })]),
      ),
    );
    mockMaintenanceAPI.updateItem.mockResolvedValue(okResponse({ id: 'mi-1' }));
    const replacement = imageFile('replacement.jpg');

    renderPage();

    const thumbnail = await screen.findByAltText('Reference photo for Disconnect power');
    expect(thumbnail).toHaveAttribute('src', 'http://api.test/media/step.jpg');

    // Saving an untouched step is a no-op: same position, same photo.
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));
    await waitFor(() => expect(mockMaintenanceAPI.updateItem).toHaveBeenCalled());
    expect(mockTaskAPI.updateTask).not.toHaveBeenCalled();

    pickPhoto(screen.getByRole('button', { name: /photo for step 1/i }), replacement);
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => {
      expect(mockTaskAPI.updateTask).toHaveBeenCalledWith('st-1', {
        reference_image: replacement,
      });
    });
  });
});
