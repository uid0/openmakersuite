/**
 * Tests for the project-storage warden queue list page.
 *
 * Covers the API contract (status filter forwarded), the empty state, the
 * row count badges on the "all" view, and the SegmentedControl filter
 * re-fetching with the right query param.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import FacilitiesProjectStorageListPage from '../../pages/FacilitiesProjectStorageListPage';
import { projectStorageAPI } from '../../services/api';
import { ProjectStorageStint } from '../../types';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    projectStorageAPI: {
      list: jest.fn(),
      labelUrl: jest.fn(
        (_stintId: string, printer = 'brother_ql') =>
          `https://labels.example/${printer}.png`,
      ),
    },
  };
});

const mockAPI = projectStorageAPI as jest.Mocked<typeof projectStorageAPI>;

const buildStint = (overrides: Partial<ProjectStorageStint> = {}): ProjectStorageStint => ({
  id: 1,
  stint_id: 'PS-AB23CDFG',
  username: 'alice',
  first_name: 'Alice',
  last_name: 'Aardvark',
  email: 'alice@example.com',
  display_name: 'Alice Aardvark',
  project_title: 'Big Sculpture',
  started_at: '2026-05-01T00:00:00Z',
  expires_at: '2026-06-30T00:00:00Z',
  removed_at: null,
  notice_sent_at: null,
  moved_to_purgatory_at: null,
  storage_location_name: 'Shelf A',
  purgatory_location_name: '',
  slot: null,
  slot_code: '',
  location_display: 'Shelf A',
  notes: '',
  status: 'active',
  purgatory_at: null,
  expiry_week: 26,
  expiry_day_of_year: 181,
  events: [],
  qr_code_url: null,
  april_tag_id: null,
  created_at: '2026-05-01T00:00:00Z',
  updated_at: '2026-05-01T00:00:00Z',
  ...overrides,
});

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <FacilitiesProjectStorageListPage />
      </MemoryRouter>
    </MantineProvider>,
  );

const ok = <T,>(data: T) =>
  ({
    data,
    status: 200,
    statusText: 'OK',
    headers: {},
    config: {} as never,
  }) as never;

describe('FacilitiesProjectStorageListPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('renders rows from the list endpoint', async () => {
    mockAPI.list.mockResolvedValue(
      ok({ count: 1, next: null, previous: null, results: [buildStint()] }),
    );
    renderPage();
    expect(await screen.findByTestId('stint-row-PS-AB23CDFG')).toBeInTheDocument();
    expect(screen.getByText('Alice Aardvark')).toBeInTheDocument();
    expect(screen.getByText('Big Sculpture')).toBeInTheDocument();
  });

  test('starts on the "all" filter and omits the status param', async () => {
    mockAPI.list.mockResolvedValue(
      ok({ count: 0, next: null, previous: null, results: [] }),
    );
    renderPage();
    await waitFor(() => {
      expect(mockAPI.list).toHaveBeenCalledWith({
        status: undefined,
        ordering: 'expires_at',
        page_size: 100,
      });
    });
  });

  test('changing the filter re-fetches with the right status', async () => {
    mockAPI.list.mockResolvedValue(
      ok({ count: 0, next: null, previous: null, results: [] }),
    );
    renderPage();
    await waitFor(() => expect(mockAPI.list).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByText('Expired'));
    await waitFor(() =>
      expect(mockAPI.list).toHaveBeenLastCalledWith({
        status: 'expired',
        ordering: 'expires_at',
        page_size: 100,
      }),
    );
  });

  test('empty bucket shows the empty placeholder', async () => {
    mockAPI.list.mockResolvedValue(
      ok({ count: 0, next: null, previous: null, results: [] }),
    );
    renderPage();
    expect(await screen.findByTestId('list-empty')).toBeInTheDocument();
  });

  test('renders status count badges in the "all" view only', async () => {
    mockAPI.list.mockResolvedValue(
      ok({
        count: 2,
        next: null,
        previous: null,
        results: [
          buildStint({ stint_id: 'PS-1', status: 'expired' }),
          buildStint({ stint_id: 'PS-2', status: 'expiring_soon' }),
        ],
      }),
    );
    renderPage();
    expect(await screen.findByText('Expired: 1')).toBeInTheDocument();
    expect(screen.getByText('Expiring soon: 1')).toBeInTheDocument();
  });

  test('uses purgatory_location_name when status=purgatory', async () => {
    mockAPI.list.mockResolvedValue(
      ok({
        count: 1,
        next: null,
        previous: null,
        results: [
          buildStint({
            stint_id: 'PS-PURG',
            status: 'purgatory',
            storage_location_name: 'Shelf A',
            purgatory_location_name: 'Back room',
          }),
        ],
      }),
    );
    renderPage();
    expect(await screen.findByText('Back room')).toBeInTheDocument();
    expect(screen.queryByText('Shelf A')).not.toBeInTheDocument();
  });

  test('per-row Preview opens a label modal for that stint', async () => {
    mockAPI.list.mockResolvedValue(
      ok({ count: 1, next: null, previous: null, results: [buildStint()] }),
    );
    renderPage();

    // No modal / label request until the warden clicks Preview.
    expect(await screen.findByTestId('stint-row-PS-AB23CDFG')).toBeInTheDocument();
    expect(screen.queryByTestId('row-label-preview')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('preview-label-PS-AB23CDFG'));

    const img = await screen.findByTestId('row-label-preview');
    expect(img.getAttribute('src')).toBe('https://labels.example/brother_ql.png');
    expect(mockAPI.labelUrl).toHaveBeenCalledWith('PS-AB23CDFG', 'brother_ql');
  });
});
