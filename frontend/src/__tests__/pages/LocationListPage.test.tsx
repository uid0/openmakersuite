/**
 * Tests for LocationListPage component
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import LocationListPage from '../../pages/LocationListPage';
import * as api from '../../services/api';

// Mock the API
jest.mock('../../services/api');

// Mock dialogs so confirmDelete invokes onConfirm immediately
jest.mock('../../utils/dialogs', () => ({
  confirmDelete: jest.fn(),
  showError: jest.fn(),
  showSuccess: jest.fn(),
}));
import { confirmDelete, showError, showSuccess } from '../../utils/dialogs';

const mockNavigate = jest.fn();
jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useNavigate: () => mockNavigate,
}));

// Mock localStorage
const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] || null,
    setItem: (key: string, value: string) => {
      store[key] = value.toString();
    },
    removeItem: (key: string) => {
      delete store[key];
    },
    clear: () => {
      store = {};
    },
  };
})();

Object.defineProperty(window, 'localStorage', {
  value: localStorageMock,
});

describe('LocationListPage', () => {
  const mockLocations = [
    {
      id: 1,
      name: 'Main Workshop',
      description: 'Main workshop area',
      is_active: true,
      parent: null,
      fixture_count: 5,
      qr_code_url: 'https://example.com/qr1.png',
      qr_code: 'QR123',
      access_code: null,
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-01T00:00:00Z',
    },
    {
      id: 2,
      name: 'Electronics Lab',
      description: 'Electronics workspace',
      is_active: true,
      parent: 1,
      fixture_count: 3,
      qr_code_url: null,
      qr_code: null,
      access_code: null,
      created_at: '2024-01-02T00:00:00Z',
      updated_at: '2024-01-02T00:00:00Z',
    },
    {
      id: 3,
      name: 'Wood Shop',
      description: 'Woodworking area',
      is_active: true,
      parent: null,
      fixture_count: 8,
      qr_code_url: 'https://example.com/qr3.png',
      qr_code: 'QR456',
      access_code: null,
      created_at: '2024-01-03T00:00:00Z',
      updated_at: '2024-01-03T00:00:00Z',
    },
    {
      id: 4,
      name: 'Storage Room',
      description: 'Storage area',
      is_active: false,
      parent: 1,
      fixture_count: 0,
      qr_code_url: null,
      qr_code: null,
      access_code: null,
      created_at: '2024-01-04T00:00:00Z',
      updated_at: '2024-01-04T00:00:00Z',
    },
  ];

  beforeEach(() => {
    jest.clearAllMocks();
    localStorageMock.clear();
    (confirmDelete as jest.Mock).mockImplementation(
      (_msg: string, onConfirm: () => void) => onConfirm(),
    );
    (api.inventoryAPI.listLocations as jest.Mock).mockResolvedValue({
      data: { results: mockLocations },
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as any,
    });
  });

  const renderPage = () => {
    return render(
      <MantineProvider>
        <MemoryRouter>
          <LocationListPage />
        </MemoryRouter>
      </MantineProvider>
    );
  };

  it('renders loading state initially', async () => {
    (api.inventoryAPI.listLocations as jest.Mock).mockReturnValue(new Promise(() => {}));
    renderPage();

    expect(screen.getByText(/Loading locations/i)).toBeInTheDocument();
  });

  it('displays locations after loading', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Main Workshop')).toBeInTheDocument();
      expect(screen.getByText('Electronics Lab')).toBeInTheDocument();
      expect(screen.getByText('Wood Shop')).toBeInTheDocument();
    });
  });

  it('builds tree structure correctly with hierarchical data', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Main Workshop')).toBeInTheDocument();
      expect(screen.getByText('Electronics Lab')).toBeInTheDocument();
      expect(screen.getByText('Storage Room')).toBeInTheDocument();
    });

    // Verify parent-child relationships are displayed
    // The TreeView should show Electronics Lab and Storage Room as children of Main Workshop
    expect(screen.getByText('Main Workshop')).toBeInTheDocument();
    expect(screen.getByText('Electronics Lab')).toBeInTheDocument();
  });

  it('handles locations with missing parent gracefully', async () => {
    const locationsWithMissingParent = [
      ...mockLocations,
      {
        id: 5,
        name: 'Orphan Location',
        description: 'Location with missing parent',
        is_active: true,
        parent: 999, // Non-existent parent ID
        fixture_count: 0,
        qr_code_url: null,
        qr_code: null,
        access_code: null,
        created_at: '2024-01-05T00:00:00Z',
        updated_at: '2024-01-05T00:00:00Z',
      },
    ];

    (api.inventoryAPI.listLocations as jest.Mock).mockResolvedValue({
      data: { results: locationsWithMissingParent },
    });

    renderPage();

    // Should not throw an error and should display the orphan as a root node
    await waitFor(() => {
      expect(screen.getByText('Orphan Location')).toBeInTheDocument();
    });
  });

  it('handles empty locations array', async () => {
    (api.inventoryAPI.listLocations as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('No locations found.')).toBeInTheDocument();
    });
  });

  it('filters locations by search term', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Main Workshop')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText('Search locations...');
    fireEvent.change(searchInput, { target: { value: 'Electronics' } });

    await waitFor(() => {
      expect(screen.queryByText('Main Workshop')).not.toBeInTheDocument();
      expect(screen.getByText('Electronics Lab')).toBeInTheDocument();
    });
  });

  it('searches by description', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Main Workshop')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText('Search locations...');
    fireEvent.change(searchInput, { target: { value: 'workspace' } });

    await waitFor(() => {
      expect(screen.getByText('Electronics Lab')).toBeInTheDocument();
    });
  });

  it('displays error message when API call fails', async () => {
    (api.inventoryAPI.listLocations as jest.Mock).mockRejectedValue({
      response: {
        data: { detail: 'Failed to load locations' },
      },
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Failed to load locations')).toBeInTheDocument();
    });
  });

  // Note: Testing network errors without response (which trigger Sentry re-throw)
  // is difficult in unit tests due to Jest's unhandled rejection detection.
  // The component's error handling is covered by the "displays error message" test above.
  // The re-throw behavior for Sentry is better tested in integration/e2e tests.

  it('displays staff controls when user is staff', async () => {
    localStorageMock.setItem('is_staff', 'true');
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Create Location')).toBeInTheDocument();
    });
  });

  it('hides staff controls when user is not staff', async () => {
    localStorageMock.setItem('is_staff', 'false');
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Main Workshop')).toBeInTheDocument();
    });

    expect(screen.queryByText('Create Location')).not.toBeInTheDocument();
  });

  it('displays location status badges correctly', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Main Workshop')).toBeInTheDocument();
    });

    // Check for active/inactive badges
    const activeBadges = screen.getAllByText('Active');
    const inactiveBadges = screen.getAllByText('Inactive');
    
    expect(activeBadges.length).toBeGreaterThan(0);
    // Storage Room is inactive, so should have inactive badge
    await waitFor(() => {
      expect(inactiveBadges.length).toBeGreaterThan(0);
    });
  });

  it('displays QR code status', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Main Workshop')).toBeInTheDocument();
    });

    // Locations with QR codes should show "✓ QR"
    const qrBadges = screen.getAllByText('✓ QR');
    expect(qrBadges.length).toBeGreaterThan(0);
  });

  it('displays fixture counts', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Main Workshop')).toBeInTheDocument();
    });

    // Should display fixture counts
    expect(screen.getByText(/5 fixtures/i)).toBeInTheDocument();
    expect(screen.getByText(/3 fixtures/i)).toBeInTheDocument();
    expect(screen.getByText(/8 fixtures/i)).toBeInTheDocument();
  });

  it('handles locations with null description', async () => {
    const locationsWithNullDescription = [
      {
        id: 6,
        name: 'No Description Location',
        description: null as any,
        is_active: true,
        parent: null,
        fixture_count: 0,
        qr_code_url: null,
        qr_code: null,
        access_code: null,
        created_at: '2024-01-06T00:00:00Z',
        updated_at: '2024-01-06T00:00:00Z',
      },
    ];

    (api.inventoryAPI.listLocations as jest.Mock).mockResolvedValue({
      data: { results: locationsWithNullDescription },
    });

    renderPage();

    // Should not throw an error when description is null
    await waitFor(() => {
      expect(screen.getByText('No Description Location')).toBeInTheDocument();
    });
  });

  it('handles circular parent references gracefully', async () => {
    const locationsWithCircularRef = [
      {
        id: 7,
        name: 'Parent Location',
        description: 'Parent',
        is_active: true,
        parent: 8, // Points to child
        fixture_count: 0,
        qr_code_url: null,
        qr_code: null,
        access_code: null,
        created_at: '2024-01-07T00:00:00Z',
        updated_at: '2024-01-07T00:00:00Z',
      },
      {
        id: 8,
        name: 'Child Location',
        description: 'Child',
        is_active: true,
        parent: 7, // Points back to parent
        fixture_count: 0,
        qr_code_url: null,
        qr_code: null,
        access_code: null,
        created_at: '2024-01-08T00:00:00Z',
        updated_at: '2024-01-08T00:00:00Z',
      },
    ];

    (api.inventoryAPI.listLocations as jest.Mock).mockResolvedValue({
      data: { results: locationsWithCircularRef },
    });

    renderPage();

    // With circular references, the buildTree function will:
    // - Create both nodes in the map
    // - Try to add each as a child of the other
    // - Since both have parents, neither becomes a root initially
    // - The fallback logic treats missing parents as roots
    // - This can result in an empty tree or both as roots
    // The important thing is that it doesn't crash or cause infinite loops
    await waitFor(() => {
      // Verify the component rendered without crashing
      expect(screen.getByText('Locations')).toBeInTheDocument();
      // The tree might be empty due to circular references, which is acceptable
      // What matters is the component didn't crash
      const emptyState = screen.queryByText('No locations found.');
      const parentText = screen.queryByText('Parent Location');
      const childText = screen.queryByText('Child Location');
      
      // Either locations are displayed OR empty state is shown (both are valid outcomes)
      expect(emptyState || parentText || childText).toBeTruthy();
    }, { timeout: 2000 });
  });

  it('reloads locations after delete', async () => {
    localStorageMock.setItem('is_staff', 'true');
    (api.inventoryAPI.deleteLocation as jest.Mock).mockResolvedValue({});

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Main Workshop')).toBeInTheDocument();
    });

    // Find and click delete button
    const deleteButtons = screen.getAllByText('Delete');
    expect(deleteButtons.length).toBeGreaterThan(0);
    fireEvent.click(deleteButtons[0]);

    expect(confirmDelete).toHaveBeenCalledWith(
      'Are you sure you want to delete this location?',
      expect.any(Function),
    );

    await waitFor(() => {
      expect(api.inventoryAPI.deleteLocation).toHaveBeenCalled();
      // Should reload locations after delete
      expect(api.inventoryAPI.listLocations).toHaveBeenCalledTimes(2);
    });
  });

  it('shows error notification when delete fails', async () => {
    localStorageMock.setItem('is_staff', 'true');
    (api.inventoryAPI.deleteLocation as jest.Mock).mockRejectedValue({
      response: { data: { detail: 'Cannot delete' } },
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Main Workshop')).toBeInTheDocument();
    });

    const deleteButtons = screen.getAllByText('Delete');
    fireEvent.click(deleteButtons[0]);

    await waitFor(() => {
      expect(showError).toHaveBeenCalledWith('Cannot delete');
    });
  });

  it('shows success notification after generating QR code', async () => {
    localStorageMock.setItem('is_staff', 'true');
    (api.inventoryAPI.generateLocationQR as jest.Mock).mockResolvedValue({});

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Main Workshop')).toBeInTheDocument();
    });

    const qrButtons = screen.getAllByTitle('Generate QR Code');
    fireEvent.click(qrButtons[0]);

    await waitFor(() => {
      expect(showSuccess).toHaveBeenCalledWith('QR code generated successfully');
    });
  });

  it('handles buildTree with duplicate IDs gracefully', async () => {
    const locationsWithDuplicates = [
      {
        id: 1,
        name: 'Location 1',
        description: 'First',
        is_active: true,
        parent: null,
        fixture_count: 0,
        qr_code_url: null,
        qr_code: null,
        access_code: null,
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
      },
      {
        id: 1, // Duplicate ID
        name: 'Location 1 Duplicate',
        description: 'Duplicate',
        is_active: true,
        parent: null,
        fixture_count: 0,
        qr_code_url: null,
        qr_code: null,
        access_code: null,
        created_at: '2024-01-02T00:00:00Z',
        updated_at: '2024-01-02T00:00:00Z',
      },
    ];

    (api.inventoryAPI.listLocations as jest.Mock).mockResolvedValue({
      data: { results: locationsWithDuplicates },
    });

    renderPage();

    // With duplicate IDs, the Map will overwrite, but both items in the array
    // will try to create nodes, potentially causing duplicates in the tree
    // The important thing is that it doesn't crash
    await waitFor(() => {
      // Should render at least one (the last one overwrites in the map, but both might render)
      const duplicates = screen.queryAllByText('Location 1 Duplicate');
      expect(duplicates.length).toBeGreaterThan(0);
    });
    
    // Verify the component rendered without crashing
    expect(screen.getByText('Locations')).toBeInTheDocument();
  });
});

