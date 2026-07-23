/**
 * Tests for AssetProblemDetailPage (op-ybpn).
 *
 * The asset-side twin of LocationProblemDetailPage: promote to an in-house
 * work order, promote to a vendor work order, resolve. Every mutation patches
 * the visible problem from its response — no follow-up GET, no flip back to
 * "Loading problem…".
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import AssetProblemDetailPage from '../../pages/AssetProblemDetailPage';
import api, { assetProblemsAPI } from '../../services/api';
import { AssetProblem } from '../../types';
import { networkError } from '../helpers/offline';

vi.mock('../../services/api', () => {
  const apiMock = { get: jest.fn() };
  return {
    __esModule: true,
    default: apiMock,
    assetProblemsAPI: {
      get: jest.fn(),
      promoteStandard: jest.fn(),
      promoteThirdParty: jest.fn(),
      resolve: jest.fn(),
    },
  };
});

const mockApi = api as jest.Mocked<typeof api>;
const mockAP = assetProblemsAPI as jest.Mocked<typeof assetProblemsAPI>;

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={['/maintenance/asset-problems/ap-1']}>
        <Routes>
          <Route
            path="/maintenance/asset-problems/:id"
            element={<AssetProblemDetailPage />}
          />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

const buildProblem = (overrides: Partial<AssetProblem> = {}): AssetProblem => ({
  id: 'ap-1',
  asset: 'asset-1',
  asset_name: 'Bandsaw',
  asset_tag: 'TAG001',
  reported_by: 'alice',
  description: 'Blade guide is loose',
  status: 'reported',
  work_order: null,
  work_order_short_id: null,
  third_party_work_order: null,
  third_party_work_order_short_id: null,
  resolution_notes: '',
  created_at: '2026-05-01T00:00:00Z',
  updated_at: '2026-05-01T00:00:00Z',
  resolved_at: null,
  resolved_by: '',
  photos: [],
  ...overrides,
});

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  localStorage.setItem('token', 'fake-token');
  mockApi.get.mockResolvedValue({
    data: { results: [{ id: 'vendor-1', name: 'Acme Repairs' }] },
  } as any);
});

afterEach(() => {
  localStorage.clear();
});

describe('AssetProblemDetailPage promote/resolve (op-ybpn)', () => {
  test('promotes to an in-house work order and links to it — no follow-up GET', async () => {
    mockAP.get.mockResolvedValue({ data: buildProblem() } as any);
    mockAP.promoteStandard.mockResolvedValue({
      data: buildProblem({
        status: 'in_progress',
        work_order: 'wo-1',
        work_order_short_id: 'WO-ABCD1234',
      }),
    } as any);

    renderPage();

    await screen.findByText('Blade guide is loose');
    // No MaintenanceItem picker on this side — the WO anchors to the asset.
    expect(screen.queryByLabelText(/maintenance item/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /create work order/i }));

    await waitFor(() => {
      expect(mockAP.promoteStandard).toHaveBeenCalledWith('ap-1');
    });

    const link = await screen.findByRole('link', { name: 'WO-ABCD1234' });
    expect(link).toHaveAttribute('href', '/maintenance/work-orders/wo-1');
    expect(mockAP.get).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/loading problem/i)).not.toBeInTheDocument();
    // Promote actions retire once the report has real work attached.
    expect(
      screen.queryByRole('button', { name: /create work order/i }),
    ).not.toBeInTheDocument();
  });

  test('promotes to a vendor work order with the picked vendor, title and work type', async () => {
    mockAP.get.mockResolvedValue({ data: buildProblem() } as any);
    mockAP.promoteThirdParty.mockResolvedValue({
      data: buildProblem({
        status: 'in_progress',
        third_party_work_order: 'tp-1',
        third_party_work_order_short_id: 'TPWO-99887766',
      }),
    } as any);

    renderPage();

    await screen.findByText('Blade guide is loose');

    const vendorSelect = await screen.findByLabelText(/^vendor$/i);
    await waitFor(() => {
      expect(screen.getByRole('option', { name: 'Acme Repairs' })).toBeInTheDocument();
    });
    fireEvent.change(vendorSelect, { target: { value: 'vendor-1' } });
    fireEvent.change(screen.getByLabelText(/^title$/i), {
      target: { value: 'Rebuild blade guide' },
    });
    fireEvent.change(screen.getByLabelText(/^work type$/i), {
      target: { value: 'major_repair' },
    });
    fireEvent.click(screen.getByRole('button', { name: /open vendor work order/i }));

    await waitFor(() => {
      expect(mockAP.promoteThirdParty).toHaveBeenCalledWith('ap-1', {
        vendor: 'vendor-1',
        title: 'Rebuild blade guide',
        work_type: 'major_repair',
      });
    });

    const link = await screen.findByRole('link', { name: 'TPWO-99887766' });
    expect(link).toHaveAttribute('href', '/maintenance/third-party/tp-1');
  });

  test('resolve patches the problem from the response and disables both actions in flight', async () => {
    mockAP.get.mockResolvedValue({ data: buildProblem() } as any);
    let resolveResolve: (value: any) => void = () => undefined;
    mockAP.resolve.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveResolve = resolve;
        }) as any,
    );

    renderPage();

    await screen.findByText('Blade guide is loose');

    fireEvent.change(screen.getByLabelText(/resolution notes/i), {
      target: { value: 'Tightened the guide' },
    });
    const markResolved = screen.getByRole('button', { name: /mark resolved/i });
    const markClosed = screen.getByRole('button', { name: /mark closed/i });
    fireEvent.click(markResolved);

    await waitFor(() => expect(markResolved).toBeDisabled());
    // The sibling action is locked too so transitions can't race.
    expect(markClosed).toBeDisabled();
    // Duplicate clicks while pending are ignored.
    fireEvent.click(markResolved);
    expect(mockAP.resolve).toHaveBeenCalledTimes(1);
    expect(mockAP.resolve).toHaveBeenCalledWith('ap-1', {
      status: 'resolved',
      resolution_notes: 'Tightened the guide',
    });

    resolveResolve({
      data: buildProblem({
        status: 'resolved',
        resolved_at: '2026-05-02T00:00:00Z',
        resolved_by: 'alice',
        resolution_notes: 'Tightened the guide',
      }),
    });

    // Resolved: the resolve panel retires and the notes are shown back.
    await waitFor(() => {
      expect(
        screen.queryByRole('button', { name: /mark resolved/i }),
      ).not.toBeInTheDocument();
    });
    expect(screen.getByText('Tightened the guide')).toBeInTheDocument();
    expect(screen.queryByText(/loading problem/i)).not.toBeInTheDocument();
  });

  test('hides the mutation actions when logged out', async () => {
    localStorage.removeItem('token');
    mockAP.get.mockResolvedValue({ data: buildProblem() } as any);

    renderPage();

    await screen.findByText('Blade guide is loose');
    expect(
      screen.queryByRole('button', { name: /create work order/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /mark resolved/i }),
    ).not.toBeInTheDocument();
  });

  test('renders an actionable error state when the load fails offline', async () => {
    mockAP.get.mockRejectedValue(networkError());

    renderPage();

    expect(
      await screen.findByRole('link', { name: /back to maintenance/i }),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/failed to load/i).length).toBeGreaterThan(0);
    expect(screen.queryByText(/loading problem/i)).not.toBeInTheDocument();
  });
});
