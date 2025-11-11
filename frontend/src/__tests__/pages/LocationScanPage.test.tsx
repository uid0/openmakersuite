/**
 * Tests for LocationScanPage component
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import LocationScanPage from '../../pages/LocationScanPage';
import * as api from '../../services/api';

// Mock the API
jest.mock('../../services/api');

const mockNavigate = jest.fn();
jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useNavigate: () => mockNavigate,
}));

describe('LocationScanPage', () => {
  const mockLocation = {
    id: 'test-location-123',
    name: 'Test Location',
    description: 'A test location description',
    is_active: true,
  };

  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
  });

  const renderWithRouter = async (locationId = 'test-location-123') => {
    const view = render(
      <MemoryRouter initialEntries={[`/scan/location/${locationId}`]}>
        <Routes>
          <Route path="/scan/location/:locationId" element={<LocationScanPage />} />
        </Routes>
      </MemoryRouter>
    );
    return view;
  };

  test('displays loading state initially', async () => {
    (api.inventoryAPI.getLocation as jest.Mock).mockReturnValue(new Promise(() => {}));

    await renderWithRouter();

    expect(screen.getByText(/loading location details/i)).toBeInTheDocument();
  });

  test('displays location details after loading', async () => {
    (api.inventoryAPI.getLocation as jest.Mock).mockResolvedValue({
      data: mockLocation,
    });

    await renderWithRouter();

    await screen.findByText('Test Location');
    expect(screen.getByText(/a test location description/i)).toBeInTheDocument();
  });

  test('displays error when location not found', async () => {
    (api.inventoryAPI.getLocation as jest.Mock).mockRejectedValue({
      response: { data: { detail: 'Location not found' } },
    });

    await renderWithRouter();

    await screen.findByText(/location not found/i);
  });

  test('allows switching between tabs', async () => {
    (api.inventoryAPI.getLocation as jest.Mock).mockResolvedValue({
      data: mockLocation,
    });

    await renderWithRouter();

    await screen.findByText('Test Location');

    // Check initial tab (checkin)
    expect(screen.getByText(/check in to/i)).toBeInTheDocument();

    // Switch to feedback tab
    const feedbackTab = screen.getByRole('button', { name: /^feedback$/i });
    fireEvent.click(feedbackTab);

    // Wait for feedback form to appear - use heading text
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /submit feedback/i })).toBeInTheDocument();
    }, { timeout: 3000 });

    // Switch to security tab
    const securityTab = screen.getByRole('button', { name: /report issue/i });
    fireEvent.click(securityTab);

    // Wait for security form to appear
    await waitFor(() => {
      expect(screen.getByText(/report an issue/i)).toBeInTheDocument();
    }, { timeout: 3000 });
  });

  test('handles check-in submission', async () => {
    (api.inventoryAPI.getLocation as jest.Mock).mockResolvedValue({
      data: mockLocation,
    });

    (api.locationCheckinAPI.checkin as jest.Mock).mockResolvedValue({
      data: { id: 'checkin-123' },
    });

    await renderWithRouter();

    await screen.findByText('Test Location');

    // Fill in check-in form using placeholder text
    const notesInput = screen.getByPlaceholderText(/any notes about your check-in/i);
    fireEvent.change(notesInput, { target: { value: 'Test check-in notes' } });

    // Submit check-in - find the form and submit it
    const form = screen.getByText(/check in to/i).closest('form');
    expect(form).toBeDefined();
    if (form) {
      fireEvent.submit(form);
    }

    await waitFor(() => {
      expect(api.locationCheckinAPI.checkin).toHaveBeenCalledWith(
        mockLocation.id,
        expect.objectContaining({
          checkin_type: 'anonymous',
          notes: 'Test check-in notes',
        })
      );
    });
  });

  test('handles feedback submission', async () => {
    (api.inventoryAPI.getLocation as jest.Mock).mockResolvedValue({
      data: mockLocation,
    });

    (api.locationCheckinAPI.submitFeedback as jest.Mock).mockResolvedValue({
      data: { id: 'feedback-123' },
    });

    await renderWithRouter();

    await screen.findByText('Test Location');

    // Switch to feedback tab
    const feedbackTab = screen.getByRole('button', { name: /^feedback$/i });
    fireEvent.click(feedbackTab);

    // Wait for feedback form to appear - use heading
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /submit feedback/i })).toBeInTheDocument();
    }, { timeout: 3000 });

    // Fill in feedback form using placeholder text
    const messageInput = await screen.findByPlaceholderText(/share your thoughts about this location/i);
    fireEvent.change(messageInput, { target: { value: 'This is test feedback' } });

    // Submit feedback - find the form and submit it
    const form = screen.getByRole('heading', { name: /submit feedback/i }).closest('form');
    expect(form).toBeDefined();
    if (form) {
      fireEvent.submit(form);
    }

    await waitFor(() => {
      expect(api.locationCheckinAPI.submitFeedback).toHaveBeenCalledWith(
        mockLocation.id,
        expect.objectContaining({
          feedback_type: 'neutral',
          message: 'This is test feedback',
        })
      );
    });
  });

  test('handles security report submission', async () => {
    (api.inventoryAPI.getLocation as jest.Mock).mockResolvedValue({
      data: mockLocation,
    });

    (api.locationCheckinAPI.reportSecurity as jest.Mock).mockResolvedValue({
      data: { id: 'report-123' },
    });

    await renderWithRouter();

    await screen.findByText('Test Location');

    // Switch to security tab
    const securityTab = screen.getByRole('button', { name: /report issue/i });
    fireEvent.click(securityTab);

    await waitFor(() => {
      expect(screen.getByText(/report an issue/i)).toBeInTheDocument();
    });

    // Select cleaning report type
    const cleaningButton = screen.getByText(/needs cleaning/i);
    fireEvent.click(cleaningButton);

    await waitFor(() => {
      // Check urgent checkbox
      const urgentCheckbox = screen.getByLabelText(/needs immediate attention/i);
      fireEvent.click(urgentCheckbox);

      // Fill in description using placeholder text
      const descriptionInput = screen.getByPlaceholderText(/add any additional details/i);
      fireEvent.change(descriptionInput, { target: { value: 'Area needs cleaning' } });

      // Submit report
      const submitButton = screen.getByRole('button', { name: /submit report/i });
      fireEvent.click(submitButton);
    });

    await waitFor(() => {
      expect(api.locationCheckinAPI.reportSecurity).toHaveBeenCalledWith(
        mockLocation.id,
        expect.objectContaining({
          report_type: 'cleaning',
          is_urgent: true,
          description: 'Area needs cleaning',
        })
      );
    });
  });
});

