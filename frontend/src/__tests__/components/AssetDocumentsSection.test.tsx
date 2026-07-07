/**
 * Tests for the Asset Document Library section (EAM P1.3): grouped current
 * documents with download links, the "previous versions" toggle for superseded
 * docs, staff-gated upload / new-version (supersede) / delete controls.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import AssetDocumentsSection from '../../components/assets/AssetDocumentsSection';
import { assetDocumentsAPI } from '../../services/api';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    assetDocumentsAPI: {
      list: jest.fn(),
      upload: jest.fn(),
      supersede: jest.fn(),
      update: jest.fn(),
      delete: jest.fn(),
    },
  };
});

const mockDocsApi = assetDocumentsAPI as jest.Mocked<typeof assetDocumentsAPI>;

const buildDoc = (overrides: Partial<any> = {}) => ({
  id: 'd1',
  asset: 'a1',
  file: '/media/assets/documents/2026/07/manual.pdf',
  file_url: 'http://test/media/assets/documents/2026/07/manual.pdf',
  category: 'manual',
  category_display: 'Manual / Documentation',
  title: 'Lathe Manual',
  description: '',
  version: 1,
  is_current: true,
  supersedes: null,
  supersedes_title: null,
  uploaded_by: 5,
  uploaded_by_name: 'Alice',
  uploaded_at: '2026-07-01T00:00:00Z',
  ...overrides,
});

const listResponse = (docs: any[]) => ({
  data: { count: docs.length, next: null, previous: null, results: docs },
});

const renderSection = (canManage = false, assetId = 'a1') =>
  render(
    <MantineProvider env="test">
      <AssetDocumentsSection assetId={assetId} canManage={canManage} />
    </MantineProvider>,
  );

const makeFile = (name = 'new-manual.pdf') =>
  new File(['%PDF-1.4 bytes'], name, { type: 'application/pdf' });

describe('AssetDocumentsSection', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
  });

  it('renders current documents grouped by category with a download link', async () => {
    mockDocsApi.list.mockResolvedValue(
      listResponse([
        buildDoc({ id: 'd1', title: 'Lathe Manual', category: 'manual' }),
        buildDoc({
          id: 'd2',
          title: 'Wiring Diagram',
          category: 'wiring_diagram',
          category_display: 'Wiring Diagram',
        }),
      ]) as any,
    );

    renderSection(false);

    await waitFor(() => {
      expect(screen.getByText('Lathe Manual')).toBeInTheDocument();
    });
    expect(mockDocsApi.list).toHaveBeenCalledWith({ asset: 'a1' });
    expect(screen.getByTestId('asset-documents-group-manual')).toBeInTheDocument();
    expect(screen.getByTestId('asset-documents-group-wiring_diagram')).toBeInTheDocument();
    // Download link points at the file URL.
    expect(screen.getByTestId('asset-document-download-d1')).toHaveAttribute(
      'href',
      'http://test/media/assets/documents/2026/07/manual.pdf',
    );
  });

  it('shows the empty state when there are no documents', async () => {
    mockDocsApi.list.mockResolvedValue(listResponse([]) as any);
    renderSection(false);
    await waitFor(() => {
      expect(screen.getByTestId('asset-documents-empty')).toBeInTheDocument();
    });
  });

  it('collapses superseded versions behind a toggle', async () => {
    mockDocsApi.list.mockResolvedValue(
      listResponse([
        buildDoc({ id: 'd2', title: 'Manual v2', version: 2, is_current: true }),
        buildDoc({ id: 'd1', title: 'Manual v1', version: 1, is_current: false }),
      ]) as any,
    );

    renderSection(false);

    await waitFor(() => {
      expect(screen.getByText('Manual v2')).toBeInTheDocument();
    });
    // The superseded v1 is hidden until the toggle is expanded.
    expect(screen.queryByText('Manual v1')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('asset-documents-previous-toggle'));
    expect(screen.getByText('Manual v1')).toBeInTheDocument();
  });

  it('hides management controls when canManage is false', async () => {
    mockDocsApi.list.mockResolvedValue(listResponse([buildDoc({ id: 'd1' })]) as any);
    renderSection(false);
    await waitFor(() => {
      expect(screen.getByText('Lathe Manual')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('asset-documents-add')).not.toBeInTheDocument();
    expect(screen.queryByTestId('asset-document-delete-d1')).not.toBeInTheDocument();
    expect(screen.queryByTestId('asset-document-supersede-d1')).not.toBeInTheDocument();
  });

  it('uploads a new document when canManage is true', async () => {
    mockDocsApi.list.mockResolvedValue(listResponse([]) as any);
    mockDocsApi.upload.mockResolvedValue({ data: buildDoc({ id: 'd9' }) } as any);

    renderSection(true);
    await waitFor(() => {
      expect(screen.getByTestId('asset-documents-empty')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('asset-documents-add'));
    fireEvent.change(screen.getByTestId('asset-document-title'), {
      target: { value: 'Operator Guide' },
    });
    fireEvent.change(screen.getByTestId('asset-document-category'), {
      target: { value: 'cad_source' },
    });
    fireEvent.change(screen.getByTestId('asset-document-file'), {
      target: { files: [makeFile('guide.pdf')] },
    });
    fireEvent.click(screen.getByTestId('asset-document-submit'));

    await waitFor(() => {
      expect(mockDocsApi.upload).toHaveBeenCalledTimes(1);
    });
    const arg = mockDocsApi.upload.mock.calls[0][0];
    expect(arg.asset).toBe('a1');
    expect(arg.title).toBe('Operator Guide');
    expect(arg.category).toBe('cad_source');
    expect(arg.file).toBeInstanceOf(File);
    // The list is refetched after a successful upload.
    expect(mockDocsApi.list).toHaveBeenCalledTimes(2);
  });

  it('uploads a new version (supersede) of an existing document', async () => {
    mockDocsApi.list.mockResolvedValue(
      listResponse([buildDoc({ id: 'd1', title: 'Lathe Manual', category: 'manual' })]) as any,
    );
    mockDocsApi.supersede.mockResolvedValue({ data: buildDoc({ id: 'd2', version: 2 }) } as any);

    renderSection(true);
    await waitFor(() => {
      expect(screen.getByText('Lathe Manual')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('asset-document-supersede-d1'));
    // Form opens pre-filled with the prior title + a supersede banner.
    expect(screen.getByTestId('asset-documents-supersede-banner')).toBeInTheDocument();
    expect(screen.getByTestId('asset-document-title')).toHaveValue('Lathe Manual');

    fireEvent.change(screen.getByTestId('asset-document-file'), {
      target: { files: [makeFile('v2.pdf')] },
    });
    fireEvent.click(screen.getByTestId('asset-document-submit'));

    await waitFor(() => {
      expect(mockDocsApi.supersede).toHaveBeenCalledTimes(1);
    });
    expect(mockDocsApi.supersede.mock.calls[0][0]).toBe('d1');
    expect(mockDocsApi.supersede.mock.calls[0][1].file).toBeInstanceOf(File);
    expect(mockDocsApi.upload).not.toHaveBeenCalled();
  });

  it('deletes a document after confirmation', async () => {
    mockDocsApi.list.mockResolvedValue(listResponse([buildDoc({ id: 'd1' })]) as any);
    mockDocsApi.delete.mockResolvedValue({ data: undefined } as any);
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    renderSection(true);
    await waitFor(() => {
      expect(screen.getByText('Lathe Manual')).toBeInTheDocument();
    });

    const row = screen.getByTestId('asset-document-row-d1');
    fireEvent.click(within(row).getByTestId('asset-document-delete-d1'));

    await waitFor(() => {
      expect(mockDocsApi.delete).toHaveBeenCalledWith('d1');
    });
    confirmSpy.mockRestore();
  });

  it('does not delete when confirmation is cancelled', async () => {
    mockDocsApi.list.mockResolvedValue(listResponse([buildDoc({ id: 'd1' })]) as any);
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

    renderSection(true);
    await waitFor(() => {
      expect(screen.getByText('Lathe Manual')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('asset-document-delete-d1'));
    expect(mockDocsApi.delete).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });
});
