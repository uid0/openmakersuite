/**
 * Sidebar Component Tests
 */
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { BrowserRouter } from 'react-router-dom';
import Sidebar from '../../components/Sidebar';

// Mock RecentPages component
jest.mock('../../components/RecentPages', () => {
  return function RecentPages() {
    return <div data-testid="recent-pages">Recent Pages</div>;
  };
});

const renderSidebar = () => {
  return render(
    <BrowserRouter>
      <Sidebar />
    </BrowserRouter>
  );
};

describe('Sidebar Component', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('renders sidebar with logo', () => {
    renderSidebar();
    expect(screen.getByText(/dallasmakerspace/i)).toBeInTheDocument();
  });

  it('renders workspace sections', () => {
    renderSidebar();
    // Sections might be collapsed, so check for section headers (buttons)
    const sectionButtons = screen.getAllByRole('button').filter(btn => 
      btn.textContent?.match(/inventory|purchasing|assets|facilities|sigs|settings/i)
    );
    expect(sectionButtons.length).toBeGreaterThan(0);
    // At least inventory should be visible
    expect(screen.getByText(/inventory/i)).toBeInTheDocument();
  });

  it('can toggle collapse state', async () => {
    renderSidebar();
    const toggleButton = screen.getByLabelText(/collapse sidebar|expand sidebar/i);
    expect(toggleButton).toBeInTheDocument();
    
    const initialState = localStorage.getItem('sidebarCollapsed');
    
    // Click to toggle
    toggleButton.click();
    
    // Wait for state to update
    await waitFor(() => {
      const newState = localStorage.getItem('sidebarCollapsed');
      expect(newState).not.toBe(initialState);
    });
  });

  it('expands and collapses sections', () => {
    renderSidebar();
    const inventorySection = screen.getByText(/inventory/i).closest('button');
    expect(inventorySection).toBeInTheDocument();
    
    if (inventorySection) {
      // Click to expand
      inventorySection.click();
      // Click to collapse
      inventorySection.click();
    }
  });

  it('shows navigation items when section is expanded', async () => {
    renderSidebar();
    const inventorySection = screen.getByText(/inventory/i).closest('button');
    
    if (inventorySection) {
      inventorySection.click();
      // Wait for items to appear - look for the Inventory Dashboard link specifically
      await waitFor(() => {
        const dashboardLinks = screen.getAllByText(/dashboard/i);
        expect(dashboardLinks.length).toBeGreaterThan(0);
      });
    }
  });

  it('hides auth-required items when not logged in', async () => {
    renderSidebar();
    const inventorySection = screen.getByText(/inventory/i).closest('button');
    
    if (inventorySection) {
      inventorySection.click();
      // Admin Dashboard should not be visible when not logged in
      await waitFor(() => {
        expect(screen.queryByText(/admin dashboard/i)).not.toBeInTheDocument();
      });
    }
  });

  it('shows auth-required items when logged in', async () => {
    localStorage.setItem('token', 'test-token');
    renderSidebar();
    
    const inventorySection = screen.getByText(/inventory/i).closest('button');
    if (inventorySection) {
      inventorySection.click();
      await waitFor(() => {
        expect(screen.getByText(/admin dashboard/i)).toBeInTheDocument();
      });
    }
  });

  it('shows staff-only items when user is staff', async () => {
    localStorage.setItem('token', 'test-token');
    localStorage.setItem('is_staff', 'true');
    renderSidebar();
    
    const settingsSection = screen.getByText(/settings/i).closest('button');
    if (settingsSection) {
      settingsSection.click();
      await waitFor(() => {
        expect(screen.getByText(/django admin/i)).toBeInTheDocument();
      });
    }
  });
});

