/**
 * Label-preview + overview-discoverability tests for the warden detail
 * page (op-hh8).
 *
 * Asserts:
 *  - the detail panel renders a label <img> sourced from
 *    projectStorageAPI.labelUrl(stintId, printer),
 *  - the Brother QL / Epson TM toggle re-points the <img> at the other
 *    printer's URL,
 *  - the hero exposes a "View queue" link to the existing overview list.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import FacilitiesProjectStoragePage from '../../pages/FacilitiesProjectStoragePage';
import { projectStorageAPI } from '../../services/api';
import { ProjectStorageStint } from '../../types';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    projectStorageAPI: {
      get: jest.fn(),
      byMember: jest.fn(),
      start: jest.fn(),
      sendViolationNotice: jest.fn(),
      moveToPurgatory: jest.fn(),
      markRemoved: jest.fn(),
      generateQr: jest.fn(),
      list: jest.fn(),
      // Distinct URL per printer so the toggle assertion is unambiguous.
      labelUrl: jest.fn(
        (_stintId: string, printer = 'brother_ql') =>
          `https://labels.example/${printer}.png`,
      ),
    },
  };
});

vi.mock('../../hooks/useNotifications', () => ({
  useNotifications: () => ({
    showSuccess: jest.fn(),
    showError: jest.fn(),
    showInfo: jest.fn(),
  }),
}));

const mockAPI = projectStorageAPI as jest.Mocked<typeof projectStorageAPI>;

const buildStint = (
  overrides: Partial<ProjectStorageStint> = {},
): ProjectStorageStint => ({
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

const ok = <T,>(data: T) =>
  ({
    data,
    status: 200,
    statusText: 'OK',
    headers: {},
    config: {} as never,
  }) as never;

const renderAt = (path: string) =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/facilities/project-storage/:stintId"
            element={<FacilitiesProjectStoragePage />}
          />
          <Route
            path="/facilities/project-storage"
            element={<FacilitiesProjectStoragePage />}
          />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('FacilitiesProjectStoragePage — label preview + overview link', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockAPI.byMember.mockResolvedValue(ok([]));
    mockAPI.get.mockResolvedValue(ok(buildStint()));
  });

  test('renders the label image from labelUrl once the stint loads', async () => {
    renderAt('/facilities/project-storage/PS-AB23CDFG');

    const img = await screen.findByTestId('label-preview');
    expect(img.getAttribute('src')).toBe('https://labels.example/brother_ql.png');
    expect(mockAPI.labelUrl).toHaveBeenCalledWith('PS-AB23CDFG', 'brother_ql');
  });

  test('does not request a label before a stint is loaded', async () => {
    renderAt('/facilities/project-storage');
    // Bare lookup form: no stint, so no label preview and no labelUrl call.
    expect(await screen.findByPlaceholderText(/PS-/i)).toBeInTheDocument();
    expect(screen.queryByTestId('label-preview')).not.toBeInTheDocument();
    expect(mockAPI.labelUrl).not.toHaveBeenCalled();
  });

  test('printer toggle re-points the image at the other printer URL', async () => {
    renderAt('/facilities/project-storage/PS-AB23CDFG');

    const img = await screen.findByTestId('label-preview');
    expect(img.getAttribute('src')).toBe('https://labels.example/brother_ql.png');

    fireEvent.click(screen.getByText('Epson TM'));

    await waitFor(() => {
      expect(
        screen.getByTestId('label-preview').getAttribute('src'),
      ).toBe('https://labels.example/epson_tm.png');
    });
    expect(mockAPI.labelUrl).toHaveBeenCalledWith('PS-AB23CDFG', 'epson_tm');
  });

  test('hero exposes a "View queue" link to the overview list', async () => {
    renderAt('/facilities/project-storage/PS-AB23CDFG');

    const link = await screen.findByTestId('view-queue-link');
    expect(link.getAttribute('href')).toBe('/facilities/project-storage/queue');
  });
});
