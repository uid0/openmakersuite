/**
 * Reprint claim-ticket parity test for the warden detail page (op-9lmz).
 *
 * The ScanTTY reprint action and this web button both POST
 * /project-storage/stints/{id}/reprint/ to re-queue a claim ticket for
 * printing (the backend clears printed_at so the Pi daemon reprints on
 * its next poll). Asserts the button renders once a stint loads, that a
 * click POSTs reprint with the stint_id, that the returned stint (with
 * its new "reprint requested" audit event) is reflected in the timeline,
 * and that success/failure surface a notification.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import FacilitiesProjectStoragePage from '../../pages/FacilitiesProjectStoragePage';
import { projectStorageAPI } from '../../services/api';
import { ProjectStorageStint } from '../../types';

// Stable notification spies so we can assert the toast fired.
const notify = vi.hoisted(() => ({
  showSuccess: vi.fn(),
  showError: vi.fn(),
  showInfo: vi.fn(),
}));

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
      reprint: jest.fn(),
      list: jest.fn(),
      labelUrl: jest.fn(
        (_stintId: string, printer = 'brother_ql') =>
          `https://labels.example/${printer}.png`,
      ),
    },
  };
});

vi.mock('../../hooks/useNotifications', () => ({
  useNotifications: () => notify,
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

describe('FacilitiesProjectStoragePage — reprint claim ticket', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockAPI.byMember.mockResolvedValue(ok([]));
    mockAPI.get.mockResolvedValue(ok(buildStint()));
  });

  test('renders a Reprint claim ticket button once a stint loads', async () => {
    renderAt('/facilities/project-storage/PS-AB23CDFG');
    const button = await screen.findByTestId('reprint-ticket-button');
    expect(button).toBeInTheDocument();
    expect(button).toHaveTextContent('Reprint claim ticket');
  });

  test('clicking reprint POSTs the endpoint and reflects the returned stint', async () => {
    // The reprint response carries the new audit event the backend logs.
    mockAPI.reprint.mockResolvedValue(
      ok(
        buildStint({
          events: [
            {
              id: 99,
              event_type: 'note_added',
              actor_username: 'warden',
              actor_label: 'reprint requested',
              note: '',
              created_at: '2026-06-01T00:00:00Z',
            },
          ],
        }),
      ),
    );

    renderAt('/facilities/project-storage/PS-AB23CDFG');
    const button = await screen.findByTestId('reprint-ticket-button');

    fireEvent.click(button);

    await waitFor(() =>
      expect(mockAPI.reprint).toHaveBeenCalledWith('PS-AB23CDFG'),
    );
    // The returned stint (with its new reprint event) is now displayed.
    expect(await screen.findByText(/reprint requested/)).toBeInTheDocument();
    expect(notify.showSuccess).toHaveBeenCalledWith(
      expect.stringContaining('Reprint queued for PS-AB23CDFG'),
    );
  });

  test('surfaces an error when the reprint request fails', async () => {
    mockAPI.reprint.mockRejectedValue(new Error('boom'));

    renderAt('/facilities/project-storage/PS-AB23CDFG');
    const button = await screen.findByTestId('reprint-ticket-button');

    fireEvent.click(button);

    await waitFor(() => expect(notify.showError).toHaveBeenCalled());
    expect(notify.showSuccess).not.toHaveBeenCalled();
  });
});
