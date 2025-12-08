/**
 * Breadcrumbs Component Tests
 */
import { render, screen } from '@testing-library/react';
import React from 'react';
import { BrowserRouter, MemoryRouter } from 'react-router-dom';
import Breadcrumbs from '../../components/Breadcrumbs';

const renderWithRouter = (initialEntries = ['/']) => {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <Breadcrumbs />
    </MemoryRouter>
  );
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
});

