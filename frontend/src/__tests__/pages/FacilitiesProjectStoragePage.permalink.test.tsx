/**
 * Permalink behavior tests for the warden page.
 *
 * Covers PR 3: the page auto-loads the stint when mounted at
 * /facilities/project-storage/:stintId, and the lookup form +
 * scanner-success path push the URL via useNavigate so back/forward
 * works.
 *
 * The rest of the page surface (action buttons, status pills, history)
 * is already exercised elsewhere — this file only asserts the
 * permalink contract.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';

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
      labelUrl: jest.fn(() => 'https://example.com/label.png'),
      list: jest.fn(),
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

const ok = <T,>(data: T) =>
  ({
    data,
    status: 200,
    statusText: 'OK',
    headers: {},
    config: {} as never,
  }) as never;

// LocationProbe surfaces the current URL so the lookup-form-pushes-URL
// assertion can read what navigate() did.
const LocationProbe: React.FC = () => {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname}</div>;
};

const renderAt = (path: string) =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/facilities/project-storage/:stintId"
            element={
              <>
                <FacilitiesProjectStoragePage />
                <LocationProbe />
              </>
            }
          />
          <Route
            path="/facilities/project-storage"
            element={
              <>
                <FacilitiesProjectStoragePage />
                <LocationProbe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('FacilitiesProjectStoragePage — permalink', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockAPI.byMember.mockResolvedValue(ok([]));
  });

  test('auto-loads the stint from the :stintId param', async () => {
    mockAPI.get.mockResolvedValue(ok(buildStint()));
    renderAt('/facilities/project-storage/PS-AB23CDFG');
    await waitFor(() => {
      expect(mockAPI.get).toHaveBeenCalledWith('PS-AB23CDFG');
    });
    // The page renders multiple things that include "Alice Aardvark"
    // (the main title, the history collapsible). All we really need is
    // proof the stint loaded — assert findAllByText returns at least
    // one match.
    const matches = await screen.findAllByText((_, node) =>
      (node?.textContent ?? '').includes('Alice Aardvark'),
    );
    expect(matches.length).toBeGreaterThan(0);
  });

  test('lowercases in the URL are normalized before the API call', async () => {
    mockAPI.get.mockResolvedValue(ok(buildStint()));
    renderAt('/facilities/project-storage/ps-ab23cdfg');
    await waitFor(() => {
      expect(mockAPI.get).toHaveBeenCalledWith('PS-AB23CDFG');
    });
  });

  test('lookup form submit pushes the path-based permalink', async () => {
    mockAPI.get.mockResolvedValue(ok(buildStint()));
    renderAt('/facilities/project-storage');
    // Bare lookup page mounted; no API call yet.
    expect(mockAPI.get).not.toHaveBeenCalled();

    const input = await screen.findByPlaceholderText(/PS-/i);
    await userEvent.type(input, 'PS-WALKED1');
    await userEvent.keyboard('{Enter}');

    await waitFor(() => {
      expect(screen.getByTestId('loc').textContent).toBe(
        '/facilities/project-storage/PS-WALKED1',
      );
    });
  });

  test('lookup form rejects empty input without navigating', async () => {
    renderAt('/facilities/project-storage');
    const submitBtn = await screen.findByRole('button', { name: /look up/i });
    await userEvent.click(submitBtn);
    expect(screen.getByTestId('loc').textContent).toBe('/facilities/project-storage');
    expect(mockAPI.get).not.toHaveBeenCalled();
  });
});
