/**
 * Per-step photos on the work-order detail page (op-syov).
 *
 * Each step shows the template's *reference* photo — what it should look like,
 * zoomable — and lets the tech attach *evidence* photos of what they actually
 * did. Evidence files under the step they belong to, not in the work order's
 * general photo pile.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import WorkOrderPage from '../../pages/WorkOrderPage';
import { workOrderAPI } from '../../services/api';
import { WorkOrder, WorkOrderTaskCompletion } from '../../types';

vi.mock('../../services/api');
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useParams: () => ({ id: 'wo-1' }),
  useNavigate: () => jest.fn(),
}));

const mockWorkOrderAPI = workOrderAPI as jest.Mocked<typeof workOrderAPI>;

const okResponse = <T,>(data: T) =>
  ({ data, status: 200, statusText: 'OK', headers: {}, config: {} as never }) as never;

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <WorkOrderPage />
      </MemoryRouter>
    </MantineProvider>,
  );

const buildStep = (overrides: Partial<WorkOrderTaskCompletion> = {}): WorkOrderTaskCompletion =>
  ({
    id: 'tc-1',
    work_order: 'wo-1',
    task: 'st-1',
    task_title: 'Disconnect power',
    task_order: 0,
    is_required: true,
    is_completed: false,
    completed_by: null,
    completed_by_name: null,
    completed_at: null,
    notes: '',
    task_reference_image_url: null,
    evidence_photos: [],
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }) as WorkOrderTaskCompletion;

const buildWorkOrder = (task_completions: WorkOrderTaskCompletion[]): WorkOrder =>
  ({
    id: 'wo-1',
    short_id: 'wo-001',
    maintenance_item: 'mi-1',
    maintenance_item_title: 'Quarterly belt inspection',
    asset_name: 'Bandsaw',
    asset_tag: 'TAG001',
    asset_id: 'a-1',
    status: 'open',
    due_date: null,
    assigned_to: null,
    assigned_to_name: 'Alice',
    completed_by_name: null,
    completed_at: null,
    notes: '',
    loto_completion_note: '',
    is_overdue: false,
    task_completions,
    material_usage: [],
    loto_completions: [],
    photos: [],
    submissions: [],
    tools: [],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }) as unknown as WorkOrder;

const stepsCard = async () =>
  (await screen.findByText('Task Steps')).closest('.mantine-Card-root') as HTMLElement;

beforeEach(() => {
  jest.clearAllMocks();
  mockWorkOrderAPI.addPhoto.mockResolvedValue(okResponse({ id: 'p-new' }));
});

describe('WorkOrderPage — per-step reference photo (op-syov)', () => {
  it('shows the step reference thumbnail and zooms it', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(
        buildWorkOrder([
          buildStep({ task_reference_image_url: 'http://api.test/media/ref.jpg' }),
        ]),
      ),
    );

    renderPage();

    const card = await stepsCard();
    const thumbnail = within(card).getByAltText('Reference photo for Disconnect power');
    expect(thumbnail).toHaveAttribute('src', 'http://api.test/media/ref.jpg');

    fireEvent.click(
      within(card).getByRole('button', { name: /zoom reference photo for disconnect power/i }),
    );

    expect(
      await screen.findByAltText('Reference photo for Disconnect power (enlarged)'),
    ).toBeInTheDocument();
  });

  it('renders a step with no reference photo unchanged', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(buildWorkOrder([buildStep()])));

    renderPage();

    const card = await stepsCard();
    expect(within(card).getByText('Disconnect power')).toBeInTheDocument();
    expect(within(card).queryByAltText(/reference photo/i)).not.toBeInTheDocument();
  });

  it('renders on an older payload that omits the per-step photo keys', async () => {
    const legacyStep = buildStep();
    delete (legacyStep as Partial<WorkOrderTaskCompletion>).task_reference_image_url;
    delete (legacyStep as Partial<WorkOrderTaskCompletion>).evidence_photos;
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(buildWorkOrder([legacyStep])));

    renderPage();

    const card = await stepsCard();
    expect(within(card).getByText('Disconnect power')).toBeInTheDocument();
  });
});

describe('WorkOrderPage — per-step evidence photos (op-syov)', () => {
  it('uploads a photo against the step it was taken for', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(buildWorkOrder([buildStep()])));

    renderPage();

    const card = await stepsCard();
    const button = within(card).getByRole('button', { name: /add photo to disconnect power/i });
    const input = button.parentElement?.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(['bytes'], 'evidence.jpg', { type: 'image/jpeg' })] },
    });

    await waitFor(() => expect(mockWorkOrderAPI.addPhoto).toHaveBeenCalled());
    const [workOrderId, formData] = mockWorkOrderAPI.addPhoto.mock.calls[0];
    expect(workOrderId).toBe('wo-1');
    expect(formData.get('task_completion')).toBe('tc-1');
    expect(formData.get('work_order')).toBe('wo-1');
    expect((formData.get('image') as File).name).toBe('evidence.jpg');
    // The page reloads so the new photo appears under its step.
    expect(mockWorkOrderAPI.getWorkOrder).toHaveBeenCalledTimes(2);
  });

  it('groups the evidence gallery under its own step', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(
        buildWorkOrder([
          buildStep({
            evidence_photos: [
              {
                id: 'p-1',
                image_url: 'http://api.test/media/done.jpg',
                caption: 'Belt seated',
                uploaded_at: '2026-01-02T00:00:00Z',
                uploaded_by_name: 'Alice',
              },
            ],
          }),
          buildStep({ id: 'tc-2', task_title: 'Inspect belt', task_order: 1 }),
        ]),
      ),
    );

    renderPage();

    const card = await stepsCard();
    const photo = within(card).getByAltText('Belt seated');
    expect(photo).toHaveAttribute('src', 'http://api.test/media/done.jpg');
    // Exactly one gallery image — the second step has none of its own.
    expect(within(card).getAllByRole('img')).toHaveLength(1);
  });
});
