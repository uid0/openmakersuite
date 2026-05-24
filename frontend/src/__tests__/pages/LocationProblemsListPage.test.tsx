import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import LocationProblemsListPage from '../../pages/LocationProblemsListPage';
import * as api from '../../services/api';

vi.mock('../../services/api');

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <LocationProblemsListPage />
      </MemoryRouter>
    </MantineProvider>,
  );

const baseProblem = {
  id: 'p1',
  location: 1,
  location_name: 'Shelf A',
  reported_by: 'tester',
  description: 'Light is out',
  status: 'open' as const,
  status_display: 'Open',
  severity: 'medium' as const,
  severity_display: 'Medium',
  photo: null,
  photo_url: null,
  paper_form_attachment: null,
  paper_form_url: null,
  work_order: null,
  work_order_short_id: null,
  third_party_work_order: null,
  third_party_work_order_short_id: null,
  resolution_notes: '',
  reported_at: '2026-05-01T12:00:00Z',
  updated_at: '2026-05-01T12:00:00Z',
  resolved_at: null,
  resolved_by: '',
};

describe('LocationProblemsListPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders the table once problems load', async () => {
    (api.locationProblemsAPI.list as jest.Mock).mockResolvedValueOnce({
      data: { results: [baseProblem] },
    });

    renderPage();

    expect(screen.getByText('Location problems')).toBeInTheDocument();
    expect(await screen.findByTestId('location-problems-table')).toBeInTheDocument();
    expect(screen.getByText('Light is out')).toBeInTheDocument();
    expect(screen.getByText('Shelf A')).toBeInTheDocument();
  });

  it('filters out resolved problems by default ("Open" filter)', async () => {
    (api.locationProblemsAPI.list as jest.Mock).mockResolvedValueOnce({
      data: {
        results: [
          baseProblem,
          {
            ...baseProblem,
            id: 'p2',
            description: 'Closed already',
            status: 'resolved',
            status_display: 'Resolved',
          },
        ],
      },
    });

    renderPage();

    expect(await screen.findByText('Light is out')).toBeInTheDocument();
    expect(screen.queryByText('Closed already')).not.toBeInTheDocument();
  });

  it('shows resolved problems when the Resolved filter is chosen', async () => {
    (api.locationProblemsAPI.list as jest.Mock).mockResolvedValueOnce({
      data: {
        results: [
          baseProblem,
          {
            ...baseProblem,
            id: 'p2',
            description: 'Closed already',
            status: 'resolved',
            status_display: 'Resolved',
          },
        ],
      },
    });

    renderPage();

    expect(await screen.findByText('Light is out')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('Resolved'));

    expect(await screen.findByText('Closed already')).toBeInTheDocument();
    expect(screen.queryByText('Light is out')).not.toBeInTheDocument();
  });

  it('renders an empty-state message when nothing matches', async () => {
    (api.locationProblemsAPI.list as jest.Mock).mockResolvedValueOnce({
      data: { results: [] },
    });

    renderPage();

    expect(await screen.findByTestId('location-problems-empty')).toBeInTheDocument();
  });

  it('surfaces an alert when the API call fails', async () => {
    (api.locationProblemsAPI.list as jest.Mock).mockRejectedValueOnce(
      new Error('boom'),
    );

    renderPage();

    expect(await screen.findByText(/Failed to load/)).toBeInTheDocument();
  });
});
