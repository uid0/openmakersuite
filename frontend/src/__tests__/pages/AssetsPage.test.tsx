/**
 * Resilience tests for AssetsPage (#457 R4 — AC-19).
 *
 * AssetsPage wraps <AssetList />, the asset-inventory table. It had no test.
 * AC-19 requires a critical journey to render a consistent, accessible state
 * while loading, when empty, and when the fetch fails — never a blank screen
 * or a raw exception. AssetList swallows a failed fetch (logs + stops the
 * spinner), so an offline/dependency failure lands on the same actionable
 * empty state as a genuinely empty inventory.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import AssetsPage from '../../pages/AssetsPage';
import { assetsAPI, inventoryAPI, sigAPI } from '../../services/api';
import { networkError } from '../helpers/offline';

vi.mock('../../services/api');

const mockAssetsAPI = assetsAPI as jest.Mocked<typeof assetsAPI>;
const mockInventoryAPI = inventoryAPI as jest.Mocked<typeof inventoryAPI>;
const mockSigAPI = sigAPI as jest.Mocked<typeof sigAPI>;

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <AssetsPage />
      </MemoryRouter>
    </MantineProvider>,
  );

const listResponse = (results: unknown[]) =>
  ({
    data: { results, count: results.length, next: null, previous: null },
    status: 200,
    statusText: 'OK',
    headers: {},
    config: {} as never,
  }) as never;

beforeEach(() => {
  jest.clearAllMocks();
  // Filter dropdowns hydrate from these; default to empty so the table is the
  // only thing under test.
  mockInventoryAPI.listLocations.mockResolvedValue(listResponse([]));
  mockSigAPI.listMySIGs.mockResolvedValue(listResponse([]));
});

describe('AssetsPage resilience (#457 R4)', () => {
  it('shows a loading state while assets load (AC-19)', async () => {
    mockAssetsAPI.listAssets.mockImplementation(
      () => new Promise(() => undefined) as never,
    );

    renderPage();

    expect(await screen.findByText(/loading assets/i)).toBeInTheDocument();
  });

  it('shows an actionable empty state when there are no assets (AC-19)', async () => {
    mockAssetsAPI.listAssets.mockResolvedValue(listResponse([]));

    renderPage();

    expect(await screen.findByText(/no assets found/i)).toBeInTheDocument();
  });

  it('degrades to the empty state (not a blank screen) when the fetch fails (AC-19)', async () => {
    mockAssetsAPI.listAssets.mockRejectedValue(networkError());

    renderPage();

    // The page shell still renders and the list resolves to a readable state
    // rather than a blank screen or an uncaught render exception.
    expect(await screen.findByText(/no assets found/i)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByText(/loading assets/i)).not.toBeInTheDocument();
    });
  });
});
