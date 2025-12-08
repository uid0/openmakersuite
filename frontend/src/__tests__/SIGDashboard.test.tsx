/**
 * Unit tests for SIG Dashboard component
 */
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import SIGDashboard from '../pages/SIGDashboard';
import * as sigAPI from '../services/api';

// Mock the API
jest.mock('../services/api', () => ({
  sigAPI: {
    listMySIGs: jest.fn(),
    getSIGMembers: jest.fn(),
    getSIGDetails: jest.fn(),
  },
  assetsAPI: {
    listAssets: jest.fn(),
  },
  inventoryAPI: {
    listItems: jest.fn(),
  },
  reorderAPI: {
    getSIGPendingRequests: jest.fn(),
  },
}));

const mockSIGs = [
  {
    id: 1,
    name: 'Test SIG',
    member_count: 5,
    asset_count: 10,
    inventory_count: 15,
    admins: [
      { id: 1, username: 'admin1', email: 'admin1@test.com', handle: 'admin1' },
    ],
    is_user_admin: true,
  },
];

const MockedSIGDashboard = () => (
  <BrowserRouter>
    <SIGDashboard />
  </BrowserRouter>
);

describe('SIGDashboard', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('should show loading state initially', () => {
    (sigAPI.sigAPI.listMySIGs as jest.Mock).mockImplementation(
      () => new Promise(() => {}) // Never resolves
    );

    render(<MockedSIGDashboard />);
    expect(screen.getByText(/Loading SIGs/i)).toBeInTheDocument();
  });

  it('should show message when user is not a SIG admin', async () => {
    (sigAPI.sigAPI.listMySIGs as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });

    render(<MockedSIGDashboard />);

    await waitFor(() => {
      expect(screen.getByText(/not an admin of any SIGs/i)).toBeInTheDocument();
    });
  });

  it('should display SIG dashboard when user is a SIG admin', async () => {
    (sigAPI.sigAPI.listMySIGs as jest.Mock).mockResolvedValue({
      data: { results: mockSIGs },
    });
    (sigAPI.assetsAPI.listAssets as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });
    (sigAPI.inventoryAPI.listItems as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });
    (sigAPI.reorderAPI.getSIGPendingRequests as jest.Mock).mockResolvedValue({
      data: [],
    });
    (sigAPI.sigAPI.getSIGMembers as jest.Mock).mockResolvedValue({
      data: [],
    });

    render(<MockedSIGDashboard />);

    // Wait for SIG Dashboard to appear
    await waitFor(() => {
      expect(screen.getByText('SIG Dashboard')).toBeInTheDocument();
    });

    // Wait for the selected SIG to be set and overview tab to show the SIG name
    // The name appears in the overview tab as an h2
    await waitFor(() => {
      expect(screen.getByText('Test SIG')).toBeInTheDocument();
    }, { timeout: 3000 });
  });

  it('should display overview stats', async () => {
    (sigAPI.sigAPI.listMySIGs as jest.Mock).mockResolvedValue({
      data: { results: mockSIGs },
    });
    (sigAPI.assetsAPI.listAssets as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });
    (sigAPI.inventoryAPI.listItems as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });
    (sigAPI.reorderAPI.getSIGPendingRequests as jest.Mock).mockResolvedValue({
      data: [],
    });
    (sigAPI.sigAPI.getSIGMembers as jest.Mock).mockResolvedValue({
      data: [],
    });

    render(<MockedSIGDashboard />);

    await waitFor(() => {
      expect(screen.getByText('Members')).toBeInTheDocument();
    });

    // Check for stat cards - look for stats section
    expect(screen.getByText('Members')).toBeInTheDocument();
    expect(screen.getByText('Assets')).toBeInTheDocument();
    expect(screen.getByText('Inventory Items')).toBeInTheDocument();
  });

  it('should switch between tabs', async () => {
    (sigAPI.sigAPI.listMySIGs as jest.Mock).mockResolvedValue({
      data: { results: mockSIGs },
    });
    (sigAPI.assetsAPI.listAssets as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });
    (sigAPI.inventoryAPI.listItems as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });
    (sigAPI.reorderAPI.getSIGPendingRequests as jest.Mock).mockResolvedValue({
      data: [],
    });
    (sigAPI.sigAPI.getSIGMembers as jest.Mock).mockResolvedValue({
      data: [],
    });

    render(<MockedSIGDashboard />);

    await waitFor(() => {
      expect(screen.getByText('SIG Dashboard')).toBeInTheDocument();
    });

    // Click Members tab
    const membersTab = screen.getByRole('button', { name: /Members/i });
    membersTab.click();

    await waitFor(() => {
      expect(screen.getByText('Members')).toBeInTheDocument();
    });
  });
});

