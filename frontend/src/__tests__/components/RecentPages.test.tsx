/**
 * Recent Pages Component Tests
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import RecentPages from '../../components/RecentPages';

const renderWithRouter = (initialEntries = ['/'], isCollapsed = false) => {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <RecentPages isCollapsed={isCollapsed} />
    </MemoryRouter>
  );
};

const seedRecentPages = () => {
  localStorage.setItem(
    'recentPages',
    JSON.stringify([
      { path: '/inventory', label: 'Inventory Dashboard', timestamp: Date.now() },
    ])
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

  describe('resize handle', () => {
    const getScrollContainer = () => screen.getByTestId('recent-pages-scroll');
    const getHandle = () => screen.getByRole('separator', { name: /resize recent pages/i });
    const queryHandle = () =>
      screen.queryByRole('separator', { name: /resize recent pages/i });

    it('renders a resize handle when expanded with recent pages', () => {
      seedRecentPages();
      renderWithRouter(['/']);
      expect(getHandle()).toBeInTheDocument();
    });

    it('hides the resize handle when sidebar is collapsed', () => {
      seedRecentPages();
      renderWithRouter(['/'], true);
      expect(queryHandle()).not.toBeInTheDocument();
    });

    it('uses default height of 200px when no saved height', () => {
      seedRecentPages();
      renderWithRouter(['/']);
      expect(getScrollContainer().style.height).toBe('200px');
    });

    it('loads persisted height from localStorage on mount', () => {
      localStorage.setItem('recentPagesHeight', '350');
      seedRecentPages();
      renderWithRouter(['/']);
      expect(getScrollContainer().style.height).toBe('350px');
    });

    it('clamps persisted height below the minimum', () => {
      localStorage.setItem('recentPagesHeight', '20');
      seedRecentPages();
      renderWithRouter(['/']);
      expect(getScrollContainer().style.height).toBe('100px');
    });

    it('clamps persisted height above the maximum', () => {
      localStorage.setItem('recentPagesHeight', '9999');
      seedRecentPages();
      renderWithRouter(['/']);
      expect(getScrollContainer().style.height).toBe('600px');
    });

    it('drags to grow the panel and persists the new height', () => {
      seedRecentPages();
      renderWithRouter(['/']);

      fireEvent.mouseDown(getHandle(), { clientY: 500 });
      // Drag up 100px grows panel (anchored at bottom)
      fireEvent.mouseMove(document, { clientY: 400 });
      expect(getScrollContainer().style.height).toBe('300px');

      fireEvent.mouseUp(document);
      expect(localStorage.getItem('recentPagesHeight')).toBe('300');
    });

    it('clamps drag within min/max bounds', () => {
      seedRecentPages();
      renderWithRouter(['/']);

      // Drag way down (shrinks) past minimum
      fireEvent.mouseDown(getHandle(), { clientY: 500 });
      fireEvent.mouseMove(document, { clientY: 9999 });
      expect(getScrollContainer().style.height).toBe('100px');
      fireEvent.mouseUp(document);

      // Drag way up (grows) past maximum
      fireEvent.mouseDown(getHandle(), { clientY: 500 });
      fireEvent.mouseMove(document, { clientY: -9999 });
      expect(getScrollContainer().style.height).toBe('600px');
      fireEvent.mouseUp(document);
    });

    it('ignores mousemove after mouseup (drag state cleanup)', () => {
      seedRecentPages();
      renderWithRouter(['/']);

      fireEvent.mouseDown(getHandle(), { clientY: 500 });
      fireEvent.mouseMove(document, { clientY: 450 });
      expect(getScrollContainer().style.height).toBe('250px');
      fireEvent.mouseUp(document);
      // Further moves after mouseup should not change height
      fireEvent.mouseMove(document, { clientY: 100 });
      expect(getScrollContainer().style.height).toBe('250px');
    });
  });
});

