/**
 * "Documentation & References" panel on the work-order detail page (op-pzae).
 *
 * Whoever performs and signs the work order needs the manual — and needs to
 * know which revision is current — without leaving the job, so the panel sits
 * with the sign-off / completion CTA at the bottom of the page. The rows come
 * from the asset's existing document library: `revisions` is that library's
 * supersedes chain, collapsed until someone asks for the history.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import WorkOrderPage from '../../pages/WorkOrderPage';
import { workOrderAPI } from '../../services/api';
import { ReferenceDocument, ReferenceDocuments, WorkOrder } from '../../types';

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

const buildWorkOrder = (reference_documents?: ReferenceDocuments): WorkOrder =>
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
    completed_by_name: '',
    completed_at: null,
    notes: '',
    loto_completion_note: '',
    is_overdue: false,
    task_completions: [],
    material_usage: [],
    loto_completions: [],
    photos: [],
    submissions: [],
    reference_documents,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }) as WorkOrder;

const document_ = (overrides: Partial<ReferenceDocument> = {}): ReferenceDocument => ({
  id: 'doc-1',
  category: 'manual',
  category_display: 'Manual / Documentation',
  title: 'Bandsaw operator manual',
  version: 3,
  file_url: 'http://testserver/media/assets/documents/manual-v3.pdf',
  uploaded_at: '2026-05-02T10:00:00Z',
  revisions: [],
  ...overrides,
});

const panel = () =>
  (screen.getByText('Documentation & References').closest('.mantine-Card-root') ??
    undefined) as HTMLElement;

beforeEach(() => {
  jest.clearAllMocks();
});

describe('WorkOrderPage — Documentation & References (op-pzae)', () => {
  it('lists each current document with its category, revision and link', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(
        buildWorkOrder({
          documents: [
            document_(),
            document_({
              id: 'doc-2',
              category: 'wiring_diagram',
              category_display: 'Wiring Diagram',
              title: 'Motor wiring',
              version: 1,
              file_url: 'http://testserver/media/assets/documents/wiring.pdf',
            }),
          ],
          links: [],
        }),
      ),
    );

    renderPage();
    await screen.findByText('Documentation & References');

    const card = panel();
    expect(within(card).getByText('Manual / Documentation')).toBeInTheDocument();
    expect(within(card).getByText('rev 3')).toBeInTheDocument();
    expect(within(card).getByText('Motor wiring')).toBeInTheDocument();
    const manualLink = within(card).getByRole('link', { name: 'Bandsaw operator manual' });
    expect(manualLink).toHaveAttribute(
      'href',
      'http://testserver/media/assets/documents/manual-v3.pdf',
    );
    expect(manualLink).toHaveAttribute('target', '_blank');
  });

  it('keeps the revision history collapsed until it is asked for', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(
        buildWorkOrder({
          documents: [
            document_({
              revisions: [
                {
                  id: 'rev-2',
                  version: 2,
                  file_url: 'http://testserver/media/assets/documents/manual-v2.pdf',
                  uploaded_at: '2026-02-01T10:00:00Z',
                },
                {
                  id: 'rev-1',
                  version: 1,
                  file_url: 'http://testserver/media/assets/documents/manual-v1.pdf',
                  uploaded_at: '2025-06-01T10:00:00Z',
                },
              ],
            }),
          ],
          links: [],
        }),
      ),
    );

    renderPage();
    await screen.findByText('Documentation & References');

    const card = panel();
    expect(within(card).queryByText(/rev 2/)).not.toBeInTheDocument();

    await userEvent.click(within(card).getByText('Revision history (2)'));

    expect(within(card).getByText(/rev 2/)).toBeInTheDocument();
    expect(within(card).getByText(/rev 1/)).toBeInTheDocument();
    expect(within(card).getAllByRole('link', { name: 'Open' })).toHaveLength(2);
    expect(within(card).getByText('Hide revision history')).toBeInTheDocument();
  });

  it('offers no revision history for a document that has never been superseded', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(buildWorkOrder({ documents: [document_()], links: [] })),
    );

    renderPage();
    await screen.findByText('Documentation & References');

    expect(within(panel()).queryByText(/Revision history/)).not.toBeInTheDocument();
  });

  it('renders the asset quick links as external links', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(
        buildWorkOrder({
          documents: [],
          links: [
            { label: 'Manual (PDF)', url: 'http://testserver/media/assets/manuals/legacy.pdf' },
            { label: 'Wiki', url: 'https://wiki.example.com/bandsaw' },
          ],
        }),
      ),
    );

    renderPage();
    await screen.findByText('Documentation & References');

    const wiki = within(panel()).getByRole('link', { name: 'Wiki' });
    expect(wiki).toHaveAttribute('href', 'https://wiki.example.com/bandsaw');
    expect(wiki).toHaveAttribute('target', '_blank');
  });

  it('accounts for itself when the asset has no documents or links', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(buildWorkOrder({ documents: [], links: [] })),
    );

    renderPage();
    await screen.findByText('Documentation & References');

    expect(within(panel()).getByText('No linked documents.')).toBeInTheDocument();
  });

  it('renders on an older payload that omits the reference_documents key', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(okResponse(buildWorkOrder(undefined)));

    renderPage();

    expect(await screen.findByText('No linked documents.')).toBeInTheDocument();
  });

  it('sits at the bottom of the page, with the sign-off CTA', async () => {
    mockWorkOrderAPI.getWorkOrder.mockResolvedValue(
      okResponse(buildWorkOrder({ documents: [document_()], links: [] })),
    );

    renderPage();

    const heading = await screen.findByText('Documentation & References');
    const loto = screen.getByText('Lockout / Tagout');
    const backButton = screen.getByRole('button', { name: 'Back to Dashboard' });
    expect(loto.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(
      heading.compareDocumentPosition(backButton) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});
