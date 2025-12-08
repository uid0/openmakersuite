/**
 * Recent Pages Component Tests
 */
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { BrowserRouter, MemoryRouter } from 'react-router-dom';
import RecentPages from '../../components/RecentPages';

const renderWithRouter = (initialEntries = ['/']) => {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <RecentPages />
    </MemoryRouter>
  );
};

describe('RecentPages Component', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('does not render when no recent pages', () => {
    // Clear localStorage and render on a route that gets skipped
    localStorage.clear();
    renderWithRouter(['/facilities/tv-dashboard']);
    // TV dashboard routes are skipped, so no recent pages should be tracked
    // Wait a bit for any async tracking to complete
    const recentSection = screen.queryByText(/recent/i);
    expect(recentSection).not.toBeInTheDocument();
  });

  it('tracks and displays recently visited pages', async () => {
    // Set up recent pages in localStorage
    localStorage.setItem('recentPages', JSON.stringify([
      { path: '/inventory', label: 'Inventory Dashboard', timestamp: Date.now() },
    ]));

    renderWithRouter(['/']);
    
    // Should show recent pages
    await waitFor(() => {
      expect(screen.getByText(/recent/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/inventory dashboard/i)).toBeInTheDocument();
  });

  it('displays page labels correctly', () => {
    localStorage.setItem('recentPages', JSON.stringify([
      { path: '/inventory', label: 'Inventory Dashboard', timestamp: Date.now() },
      { path: '/purchasing/orders', label: 'Purchase Orders', timestamp: Date.now() - 1000 },
    ]));

    renderWithRouter(['/']);
    expect(screen.getByText(/recent/i)).toBeInTheDocument();
    expect(screen.getByText(/inventory dashboard/i)).toBeInTheDocument();
    expect(screen.getByText(/purchase orders/i)).toBeInTheDocument();
  });

  it('limits to max recent pages', () => {
    const manyPages = Array.from({ length: 15 }, (_, i) => ({
      path: `/page-${i}`,
      label: `Page ${i}`,
      timestamp: Date.now() - i * 1000,
    }));
    localStorage.setItem('recentPages', JSON.stringify(manyPages));

    renderWithRouter(['/']);
    const links = screen.getAllByRole('link');
    // Should be limited to MAX_RECENT_PAGES (8)
    expect(links.length).toBeLessThanOrEqual(8);
  });

  it('skips tracking for TV dashboard routes', () => {
    renderWithRouter(['/facilities/tv-dashboard']);
    // Should not track TV dashboard
    expect(localStorage.getItem('recentPages')).toBeNull();
  });
});

