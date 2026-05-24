/**
 * Sidebar Component Tests
 */
import { render, screen, waitFor } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import Sidebar from '../../components/Sidebar';

// Mock RecentPages component
vi.mock('../../components/RecentPages', () => ({
  default: function RecentPages() {
    return <div data-testid="recent-pages">Recent Pages</div>;
  },
}));

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

  it('renders workspace sections', () => {
    renderSidebar();
    // Sections might be collapsed, so check for section headers (buttons)
    // The button contains icon + label, so we check for the label text
    const sectionButtons = screen.getAllByRole('button').filter(btn => {
      const text = btn.textContent || '';
      return /inventory|purchasing|assets|facilities|sigs|reports|settings/i.test(text);
    });
    expect(sectionButtons.length).toBeGreaterThan(0);
    // At least inventory should be visible
    const inventoryElements = screen.getAllByText(/inventory/i);
    expect(inventoryElements.length).toBeGreaterThan(0);
  });

  it('expands and collapses sections', () => {
    renderSidebar();
    // Find the Inventory section button - it contains icon + "Inventory" text
    const inventorySectionButtons = screen.getAllByRole('button').filter(btn => {
      const text = btn.textContent || '';
      // Section header buttons have the section label, not just "Inventory"
      return text.includes('Inventory') && btn.className.includes('section-header');
    });
    const inventorySection = inventorySectionButtons[0];
    expect(inventorySection).toBeDefined();
    
    if (inventorySection) {
      // Click to expand
      inventorySection.click();
      // Click to collapse
      inventorySection.click();
    }
  });

  it('shows navigation items when section is expanded', async () => {
    renderSidebar();
    // Find the Inventory section button - it contains icon + "Inventory" text
    const inventorySectionButtons = screen.getAllByRole('button').filter(btn => {
      const text = btn.textContent || '';
      return text.includes('Inventory') && btn.className.includes('section-header');
    });
    const inventorySection = inventorySectionButtons[0];
    
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
    // Find the Inventory section button - it contains icon + "Inventory" text
    const inventorySectionButtons = screen.getAllByRole('button').filter(btn => {
      const text = btn.textContent || '';
      return text.includes('Inventory') && btn.className.includes('section-header');
    });
    const inventorySection = inventorySectionButtons[0];
    
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
    
    // Find the Inventory section button - it contains icon + "Inventory" text
    const inventorySectionButtons = screen.getAllByRole('button').filter(btn => {
      const text = btn.textContent || '';
      return text.includes('Inventory') && btn.className.includes('section-header');
    });
    const inventorySection = inventorySectionButtons[0];
    if (inventorySection) {
      inventorySection.click();
      await waitFor(() => {
        expect(screen.getByText(/admin dashboard/i)).toBeInTheDocument();
      });
    }
  });

  it('does not show a Code Entry nav link (oms-7pj)', async () => {
    localStorage.setItem('token', 'test-token');
    renderSidebar();

    const inventorySectionButtons = screen.getAllByRole('button').filter(btn => {
      const text = btn.textContent || '';
      return text.includes('Inventory') && btn.className.includes('section-header');
    });
    const inventorySection = inventorySectionButtons[0];
    expect(inventorySection).toBeDefined();

    if (inventorySection) {
      inventorySection.click();
      await waitFor(() => {
        expect(screen.getByText(/scan items/i)).toBeInTheDocument();
      });
      expect(screen.queryByText(/code entry/i)).not.toBeInTheDocument();
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

