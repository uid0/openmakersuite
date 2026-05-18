/**
 * Breadcrumbs Component Tests
 */
import { render, screen } from '@testing-library/react';
import React from 'react';
import { BrowserRouter, MemoryRouter } from 'react-router-dom';
import Breadcrumbs from '../../components/Breadcrumbs';
import { BreadcrumbProvider, useSetBreadcrumb } from '../../contexts/BreadcrumbContext';

const renderWithRouter = (initialEntries = ['/']) => {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <Breadcrumbs />
    </MemoryRouter>
  );
};

const LabelSetter: React.FC<{ label: string | undefined }> = ({ label }) => {
  useSetBreadcrumb(label);
  return null;
};

describe('Breadcrumbs Component', () => {
  it('does not render on home page', () => {
    renderWithRouter(['/']);
    expect(screen.queryByRole('navigation', { name: /breadcrumb/i })).not.toBeInTheDocument();
  });

  it('renders breadcrumbs for inventory routes', () => {
    renderWithRouter(['/inventory']);
    expect(screen.getByText(/home/i)).toBeInTheDocument();
    expect(screen.getByText(/inventory/i)).toBeInTheDocument();
  });

  it('renders breadcrumbs for nested routes', () => {
    renderWithRouter(['/inventory/assets']);
    expect(screen.getByText(/home/i)).toBeInTheDocument();
    expect(screen.getByText(/inventory/i)).toBeInTheDocument();
    expect(screen.getByText(/assets/i)).toBeInTheDocument();
  });

  it('renders breadcrumbs for purchasing routes', () => {
    renderWithRouter(['/purchasing/orders']);
    expect(screen.getByText(/home/i)).toBeInTheDocument();
    expect(screen.getByText(/purchasing/i)).toBeInTheDocument();
    expect(screen.getByText(/purchase orders/i)).toBeInTheDocument();
  });

  it('renders breadcrumbs for facilities routes', () => {
    renderWithRouter(['/facilities/tv-dashboard']);
    expect(screen.getByText(/home/i)).toBeInTheDocument();
    expect(screen.getByText(/facilities/i)).toBeInTheDocument();
    expect(screen.getByText(/tv dashboard/i)).toBeInTheDocument();
  });

  it('makes breadcrumb segments clickable', () => {
    renderWithRouter(['/inventory/assets']);
    const homeLink = screen.getByRole('link', { name: /home/i });
    expect(homeLink).toHaveAttribute('href', '/');
  });

  it('shows current page as non-clickable', () => {
    renderWithRouter(['/inventory/assets']);
    const assetsBreadcrumb = screen.getByText(/assets/i);
    expect(assetsBreadcrumb).toHaveAttribute('aria-current', 'page');
  });

  it('renders non-routable ancestor segments as non-clickable spans (AC-4)', () => {
    // /maintenance/work-orders has no list-page route — only :id child
    // routes. The "Work Orders" segment must therefore not be a Link.
    renderWithRouter(['/maintenance/work-orders/abc123']);
    expect(screen.queryByRole('link', { name: /work orders/i })).not.toBeInTheDocument();
    // It still shows up as text:
    expect(screen.getByText(/work orders/i)).toBeInTheDocument();
    // And ancestors that ARE routable remain clickable:
    expect(screen.getByRole('link', { name: /maintenance/i })).toBeInTheDocument();
  });

  it('uses canonical labels for major workspaces (AC-5)', () => {
    renderWithRouter(['/facilities/tv-dashboard']);
    // Canonical "TV Dashboard", not raw-slug "Tv-dashboard".
    expect(screen.getByText('TV Dashboard')).toBeInTheDocument();
  });

  it('linkifies the new overview-page parent paths (AC-3)', () => {
    // /purchasing was previously an orphan path that would 404 on click.
    // It now has a route handler, so the breadcrumb segment is a link.
    renderWithRouter(['/purchasing/orders']);
    const purchasingLink = screen.getByRole('link', { name: /^purchasing$/i });
    expect(purchasingLink).toHaveAttribute('href', '/purchasing');
  });

  it('hides bare UUID segments between a parent listing and an action', () => {
    // /facilities/electrical/breakers/<uuid>/edit used to render the raw
    // UUID as its own breadcrumb chip. Hide it so the trail reads
    // "Electrical → Breakers → Edit".
    renderWithRouter([
      '/facilities/electrical/breakers/550e8400-e29b-41d4-a716-446655440000/edit',
    ]);
    expect(screen.queryByText(/550e8400/)).not.toBeInTheDocument();
    expect(screen.getByText(/breakers/i)).toBeInTheDocument();
    expect(screen.getByText(/edit/i)).toBeInTheDocument();
  });

  it('hides numeric ID segments too', () => {
    renderWithRouter(['/facilities/electrical/panels/42']);
    expect(screen.queryByText('42')).not.toBeInTheDocument();
    expect(screen.getByText(/panels/i)).toBeInTheDocument();
  });

  describe('dynamic label via BreadcrumbContext', () => {
    it('replaces the trailing segment when a page registers a label', () => {
      render(
        <MemoryRouter initialEntries={['/inventory/assets/abc123/edit']}>
          <BreadcrumbProvider>
            <LabelSetter label="Bridgeport Mill" />
            <Breadcrumbs />
          </BreadcrumbProvider>
        </MemoryRouter>,
      );
      // Trailing static "Edit" is swapped for the asset name.
      expect(screen.queryByText(/^edit$/i)).not.toBeInTheDocument();
      expect(screen.getByText('Bridgeport Mill')).toBeInTheDocument();
      expect(screen.getByText('Bridgeport Mill')).toHaveAttribute('aria-current', 'page');
      // Earlier segments are untouched.
      expect(screen.getByText(/home/i)).toBeInTheDocument();
      expect(screen.getByText(/assets/i)).toBeInTheDocument();
    });

    it('falls back to the static label when no dynamic label is set', () => {
      render(
        <MemoryRouter initialEntries={['/inventory/assets/abc123/edit']}>
          <BreadcrumbProvider>
            <LabelSetter label={undefined} />
            <Breadcrumbs />
          </BreadcrumbProvider>
        </MemoryRouter>,
      );
      expect(screen.getByText(/^edit$/i)).toBeInTheDocument();
    });

    it('renders normally without a provider (safe no-op outside WorkspaceLayout)', () => {
      // Breadcrumbs is also rendered in places where the provider isn't
      // mounted (storybook, isolated tests). Verify it doesn't throw.
      renderWithRouter(['/inventory/assets']);
      expect(screen.getByText(/assets/i)).toBeInTheDocument();
    });
  });
});

