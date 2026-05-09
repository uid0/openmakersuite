/**
 * Tests for UserProfilePage component
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import UserProfilePage from '../../pages/UserProfilePage';
import { notificationsAPI, userAPI } from '../../services/api';

// Mock the API services
jest.mock('../../services/api', () => ({
  notificationsAPI: {
    getPreferences: jest.fn(),
    updatePreferences: jest.fn(),
  },
  userAPI: {
    getProfile: jest.fn(),
    updateProfile: jest.fn(),
    changePassword: jest.fn(),
    uploadSignature: jest.fn(),
  },
}));

describe('UserProfilePage', () => {
  const mockProfile = {
    id: 1,
    email: 'test@example.com',
    first_name: 'Test',
    last_name: 'User',
    handle: 'testuser',
    discord_username: null,
    discourse_username: null,
    signature_image_url: null,
  };

  const mockPreferences = {
    id: 1,
    email_enabled: true,
    in_app_enabled: true,
    supply_alerts: true,
    maintenance_alerts: true,
    order_updates: true,
    system_notifications: true,
    recent_pages_limit: 8,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  };

  beforeEach(() => {
    jest.clearAllMocks();
    (userAPI.getProfile as jest.Mock).mockResolvedValue({ data: mockProfile });
  });

  it('renders loading state initially', () => {
    (notificationsAPI.getPreferences as jest.Mock).mockImplementation(() => 
      new Promise(() => {}) // Never resolves to keep loading state
    );

    render(
      <MantineProvider>
        <BrowserRouter>
          <UserProfilePage />
        </BrowserRouter>
      </MantineProvider>
    );

    expect(screen.getByText(/Loading profile/)).toBeInTheDocument();
  });

  it('loads and displays notification preferences successfully', async () => {
    (notificationsAPI.getPreferences as jest.Mock).mockResolvedValue({ data: mockPreferences });

    render(
      <MantineProvider>
        <BrowserRouter>
          <UserProfilePage />
        </BrowserRouter>
      </MantineProvider>
    );

    // Wait for profile to load and tabs to be available
    await waitFor(() => {
      expect(screen.getByTestId('page-hero-title')).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: /notifications/i })).toBeInTheDocument();
    });

    // Click on notifications tab
    const notificationsTab = screen.getByRole('tab', { name: /notifications/i });
    fireEvent.click(notificationsTab);

    // Wait for preferences to load
    await waitFor(() => {
      expect(screen.getByText('Manage your notification preferences. Changes are saved automatically.')).toBeInTheDocument();
      expect(screen.getByText('Email Notifications')).toBeInTheDocument();
      expect(screen.getByText('In-App Notifications')).toBeInTheDocument();
    });

    // Verify preferences are displayed
    expect(screen.queryByText('Loading preferences...')).not.toBeInTheDocument();
  });

  it('shows error message when preferences fail to load', async () => {
    const errorMessage = 'Failed to load preferences';
    (notificationsAPI.getPreferences as jest.Mock).mockRejectedValueOnce({
      response: { data: { detail: errorMessage } },
      message: errorMessage,
    });

    render(
      <MantineProvider>
        <BrowserRouter>
          <UserProfilePage />
        </BrowserRouter>
      </MantineProvider>
    );

    // Wait for profile to load and tabs to be available
    await waitFor(() => {
      expect(screen.getByTestId('page-hero-title')).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: /notifications/i })).toBeInTheDocument();
    });

    // Click on notifications tab
    const notificationsTab = screen.getByRole('tab', { name: /notifications/i });
    fireEvent.click(notificationsTab);

    // Wait for error to appear
    await waitFor(() => {
      expect(screen.getByText(/Error Loading Preferences/i)).toBeInTheDocument();
    }, { timeout: 3000 });
    
    // Check that error message is present (there might be multiple instances, so use getAllByText)
    const errorMessages = screen.getAllByText(new RegExp(errorMessage, 'i'));
    expect(errorMessages.length).toBeGreaterThan(0);

    // Verify loading message is not shown
    expect(screen.queryByText('Loading preferences...')).not.toBeInTheDocument();
  });

  it('shows retry button when preferences fail to load', async () => {
    const errorMessage = 'Network error';
    (notificationsAPI.getPreferences as jest.Mock)
      .mockRejectedValueOnce({
        response: { data: { detail: errorMessage } },
        message: errorMessage,
      })
      .mockResolvedValueOnce({ data: mockPreferences });

    render(
      <MantineProvider>
        <BrowserRouter>
          <UserProfilePage />
        </BrowserRouter>
      </MantineProvider>
    );

    // Wait for profile to load and tabs to be available
    await waitFor(() => {
      expect(screen.getByTestId('page-hero-title')).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: /notifications/i })).toBeInTheDocument();
    });

    // Click on notifications tab
    const notificationsTab = screen.getByRole('tab', { name: /notifications/i });
    fireEvent.click(notificationsTab);

    // Wait for error to appear
    await waitFor(() => {
      expect(screen.getByText('Retry')).toBeInTheDocument();
    });

    // Click retry button
    const retryButton = screen.getByText('Retry');
    fireEvent.click(retryButton);

    // Wait for preferences to load after retry
    await waitFor(() => {
      expect(screen.getByText('Email Notifications')).toBeInTheDocument();
      expect(notificationsAPI.getPreferences).toHaveBeenCalledTimes(2);
    });
  });

  it('prevents preferences from showing loading state indefinitely', async () => {
    const errorMessage = 'API error';
    (notificationsAPI.getPreferences as jest.Mock).mockRejectedValueOnce({
      response: { data: { detail: errorMessage } },
      message: errorMessage,
    });

    render(
      <MantineProvider>
        <BrowserRouter>
          <UserProfilePage />
        </BrowserRouter>
      </MantineProvider>
    );

    // Wait for profile to load
    await waitFor(() => {
      expect(screen.getByTestId('page-hero-title')).toBeInTheDocument();
    });

    // Wait for and click on notifications tab
    await waitFor(() => {
      expect(screen.getByText('Notifications')).toBeInTheDocument();
    });
    const notificationsTab = screen.getByText('Notifications');
    fireEvent.click(notificationsTab);

    // Wait a bit to ensure loading state completes and error appears
    await waitFor(() => {
      expect(screen.queryByText('Loading preferences...')).not.toBeInTheDocument();
      expect(screen.getByText(/Error Loading Preferences/i)).toBeInTheDocument();
    }, { timeout: 3000 });
  });

  it('allows updating notification preferences', async () => {
    (notificationsAPI.getPreferences as jest.Mock).mockResolvedValue({ data: mockPreferences });
    (notificationsAPI.updatePreferences as jest.Mock).mockResolvedValue({
      data: { ...mockPreferences, email_enabled: false },
    });

    render(
      <MantineProvider>
        <BrowserRouter>
          <UserProfilePage />
        </BrowserRouter>
      </MantineProvider>
    );

    // Wait for profile to load and tabs to be available
    await waitFor(() => {
      expect(screen.getByTestId('page-hero-title')).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: /notifications/i })).toBeInTheDocument();
    });

    // Click on notifications tab
    const notificationsTab = screen.getByRole('tab', { name: /notifications/i });
    fireEvent.click(notificationsTab);

    // Wait for preferences to load
    await waitFor(() => {
      expect(screen.getByText('Email Notifications')).toBeInTheDocument();
    });

    // Find and toggle email notifications switch
    // Switches in Mantine use role="switch" and the label text
    const emailSwitch = screen.getByRole('switch', { name: /email notifications/i });
    fireEvent.click(emailSwitch);

    // Verify update was called
    await waitFor(() => {
      expect(notificationsAPI.updatePreferences).toHaveBeenCalledWith({ email_enabled: false });
    });
  });
});

