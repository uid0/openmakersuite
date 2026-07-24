/**
 * Attachments section on the work-order detail page (op-rjsv, op-7pjj backend).
 *
 * The internal work order's general file list — a receipt, datasheet, or
 * nameplate photo hung off the whole job. Every authenticated volunteer can
 * read the list; only staff / SIG-admin can upload or delete, the server rule
 * (`IsAuthenticatedOrStaffSigAdminWrite`) the page mirrors with `isStaff`.
 * Covers list, upload, delete, and the staff gate.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import WorkOrderPage from '../../pages/WorkOrderPage';
import { workOrderAPI } from '../../services/api';
import { WorkOrder, WorkOrderAttachment } from '../../types';

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

// A corrective work order with no PM template — maintenance_item null keeps the
// estimate fetch (maintenanceAPI) out of this test entirely.
const buildWorkOrder = (): WorkOrder =>
  ({
    id: 'wo-1',
    short_id: 'wo-001',
    maintenance_item: null,
    maintenance_item_title: '',
    asset_name: 'Bandsaw',
    asset_tag: 'TAG001',
    asset_id: 'a-1',
    status: 'open',
    due_date: null,
    assigned_to: null,
    assigned_to_name: null,
    completed_by_name: null,
    completed_at: null,
    notes: '',
    loto_completion_note: '',
    is_overdue: false,
    task_completions: [],
    material_usage: [],
    loto_completions: [],
    photos: [],
    submissions: [],
    tools: [],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }) as unknown as WorkOrder;

const buildAttachment = (
  overrides: Partial<WorkOrderAttachment> = {},
): WorkOrderAttachment => ({
  id: 'att-1',
  work_order: 'wo-1',
  file: '/media/work_orders/attachments/2026/07/receipt.pdf',
  file_url: 'http://api.test/media/work_orders/attachments/2026/07/receipt.pdf',
  file_name: 'receipt.pdf',
  kind: 'document',
  kind_display: 'Document',
  description: 'Supplier receipt',
  uploaded_by: 7,
  uploaded_by_name: 'Alice Tech',
  uploaded_at: '2026-07-20T12:00:00Z',
  ...overrides,
});

const listResponse = (results: WorkOrderAttachment[]) =>
  okResponse({ count: results.length, next: null, previous: null, results });

const attachmentsCard = async () =>
  (await screen.findByText('Attachments')).closest('.mantine-Card-root') as HTMLElement;

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(buildWorkOrder()));
  mockWorkOrderAPI.listAttachments.mockResolvedValue(listResponse([]));
  mockWorkOrderAPI.uploadAttachment.mockResolvedValue(
    okResponse(buildAttachment({ id: 'att-new' })),
  );
  mockWorkOrderAPI.deleteAttachment.mockResolvedValue(okResponse({}));
});

describe('WorkOrderPage — attachments (op-rjsv)', () => {
  it('lists the work order attachments with a download link and metadata', async () => {
    mockWorkOrderAPI.listAttachments.mockResolvedValue(listResponse([buildAttachment()]));

    renderPage();

    const card = await attachmentsCard();
    const link = await within(card).findByRole('link', { name: 'receipt.pdf' });
    expect(link).toHaveAttribute(
      'href',
      'http://api.test/media/work_orders/attachments/2026/07/receipt.pdf',
    );
    expect(within(card).getByText('Document')).toBeInTheDocument();
    expect(within(card).getByText('Supplier receipt')).toBeInTheDocument();
    expect(within(card).getByText(/Uploaded by Alice Tech/)).toBeInTheDocument();
    // The list is fetched for this work order via the top-level route.
    expect(mockWorkOrderAPI.listAttachments).toHaveBeenCalledWith('wo-1');
  });

  it('shows the empty state when there are no attachments', async () => {
    renderPage();

    const card = await attachmentsCard();
    expect(within(card).getByText(/No attachments yet/)).toBeInTheDocument();
  });

  it('uploads a file with a description (staff)', async () => {
    localStorage.setItem('is_staff', 'true');

    renderPage();

    const card = await attachmentsCard();
    const input = card.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(['bytes'], 'datasheet.pdf', { type: 'application/pdf' })] },
    });
    // The chosen file surfaces before submit.
    expect(within(card).getByText('datasheet.pdf')).toBeInTheDocument();

    fireEvent.change(within(card).getByPlaceholderText(/nameplate photo/i), {
      target: { value: 'Pump datasheet' },
    });
    fireEvent.click(within(card).getByRole('button', { name: 'Upload' }));

    await waitFor(() => expect(mockWorkOrderAPI.uploadAttachment).toHaveBeenCalled());
    const [woId, file, description] = mockWorkOrderAPI.uploadAttachment.mock.calls[0];
    expect(woId).toBe('wo-1');
    expect((file as File).name).toBe('datasheet.pdf');
    expect(description).toBe('Pump datasheet');
    // The list refreshes after a successful upload (initial load + reload).
    await waitFor(() => expect(mockWorkOrderAPI.listAttachments).toHaveBeenCalledTimes(2));
  });

  it('omits the description from the payload when left blank (staff)', async () => {
    localStorage.setItem('is_staff', 'true');

    renderPage();

    const card = await attachmentsCard();
    const input = card.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(['bytes'], 'nameplate.jpg', { type: 'image/jpeg' })] },
    });
    fireEvent.click(within(card).getByRole('button', { name: 'Upload' }));

    await waitFor(() => expect(mockWorkOrderAPI.uploadAttachment).toHaveBeenCalled());
    const [, , description] = mockWorkOrderAPI.uploadAttachment.mock.calls[0];
    expect(description).toBeUndefined();
  });

  it('deletes an attachment (staff)', async () => {
    localStorage.setItem('is_staff', 'true');
    mockWorkOrderAPI.listAttachments.mockResolvedValue(listResponse([buildAttachment()]));

    renderPage();

    const card = await attachmentsCard();
    const del = await within(card).findByRole('button', { name: /delete receipt\.pdf/i });
    fireEvent.click(del);

    await waitFor(() =>
      expect(mockWorkOrderAPI.deleteAttachment).toHaveBeenCalledWith('wo-1', 'att-1'),
    );
    // The list refreshes after a successful delete (initial load + reload).
    await waitFor(() => expect(mockWorkOrderAPI.listAttachments).toHaveBeenCalledTimes(2));
  });

  it('hides the upload + delete controls for a non-staff volunteer but still lists', async () => {
    mockWorkOrderAPI.listAttachments.mockResolvedValue(listResponse([buildAttachment()]));

    renderPage();

    const card = await attachmentsCard();
    // Read is open — the row and its download link are visible...
    await within(card).findByRole('link', { name: 'receipt.pdf' });
    // ...but every write affordance is gone.
    expect(within(card).queryByRole('button', { name: 'Upload' })).not.toBeInTheDocument();
    expect(within(card).queryByText('Add attachment')).not.toBeInTheDocument();
    expect(
      within(card).queryByRole('button', { name: /delete receipt\.pdf/i }),
    ).not.toBeInTheDocument();
    expect(card.querySelector('input[type="file"]')).toBeNull();
  });
});
