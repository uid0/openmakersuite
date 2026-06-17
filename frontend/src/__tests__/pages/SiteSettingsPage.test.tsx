/**
 * Tests for SiteSettingsPage.
 *
 * Covers the superuser permission gate (AC-18: a protected frontend action
 * matches the backend permission and offers a recovery path) and the
 * loading / save-failed / duplicate-submit resilience states (AC-19: a
 * consistent, accessible state instead of a blank screen or raw exception).
 * Part of #457 R7 (settings/webhooks coverage).
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import SiteSettingsPage from '../../pages/SiteSettingsPage';
import { customizationAPI } from '../../services/api';

vi.mock('../../services/api', () => ({
  customizationAPI: {
    getSiteSettings: jest.fn(),
    updateSiteSettings: jest.fn(),
  },
}));

const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
}));

const mockSettings = {
  site_name: 'Test Makerspace',
  site_tagline: 'Make all the things',
  logo_url: null,
  logo_alt_text: '',
  favicon_url: null,
  primary_color: '#007cba',
  secondary_color: '#417690',
  footer_text: '',
  contact_email: 'hello@example.com',
  contact_phone: '555-1234',
  website_url: 'https://example.com',
  dashboard_title: '',
  dashboard_subtitle: '',
  show_logo_on_dashboard: true,
};

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <SiteSettingsPage />
      </MemoryRouter>
    </MantineProvider>,
  );

describe('SiteSettingsPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    (customizationAPI.getSiteSettings as jest.Mock).mockResolvedValue({ data: mockSettings });
    (customizationAPI.updateSiteSettings as jest.Mock).mockResolvedValue({ data: mockSettings });
  });

  // AC-18: a non-superuser lacks permission for this settings page, so the
  // page must redirect them out (recovery path) and never call the API.
  it('redirects a non-superuser to home and never loads settings', async () => {
    // is_superuser is absent from localStorage → not permitted.
    renderPage();

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/');
    });
    expect(customizationAPI.getSiteSettings).not.toHaveBeenCalled();
  });

  // AC-19: while settings load, a superuser sees a consistent loading state
  // rather than a blank screen.
  it('shows a loading state for a superuser while settings load', () => {
    localStorage.setItem('is_superuser', 'true');
    (customizationAPI.getSiteSettings as jest.Mock).mockImplementation(
      () => new Promise(() => {}), // never resolves: hold the loading state
    );

    renderPage();

    expect(screen.getByText(/loading site settings/i)).toBeInTheDocument();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  // AC-19: a failed save surfaces a consistent error alert, not a raw
  // exception or a silently swallowed failure.
  it('surfaces an error alert when saving settings fails', async () => {
    localStorage.setItem('is_superuser', 'true');
    (customizationAPI.updateSiteSettings as jest.Mock).mockRejectedValue({
      response: { data: { detail: 'Save blocked by server' } },
    });

    renderPage();

    const saveButton = await screen.findByRole('button', { name: /save settings/i });
    fireEvent.click(saveButton);

    expect(await screen.findByText('Save blocked by server')).toBeInTheDocument();
  });

  // AC-15/AC-19: the save button disables while a save is in flight so the
  // user cannot fire a duplicate submission, then re-enables on completion.
  it('disables the save button while a save is in flight', async () => {
    localStorage.setItem('is_superuser', 'true');
    let resolveSave: (value: unknown) => void = () => {};
    (customizationAPI.updateSiteSettings as jest.Mock).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSave = resolve;
        }),
    );

    renderPage();

    const saveButton = await screen.findByRole('button', { name: /save settings/i });
    expect(saveButton).not.toBeDisabled();

    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(saveButton).toBeDisabled();
    });
    // A second click on the disabled button cannot trigger another request.
    fireEvent.click(saveButton);
    expect(customizationAPI.updateSiteSettings).toHaveBeenCalledTimes(1);

    // Resolve the pending save so nothing leaks past the test.
    resolveSave({ data: mockSettings });
    await waitFor(() => {
      expect(saveButton).not.toBeDisabled();
    });
  });
});
