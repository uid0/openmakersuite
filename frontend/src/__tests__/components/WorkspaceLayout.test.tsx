/**
 * WorkspaceLayout Component Tests
 */
import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import WorkspaceLayout from '../../components/WorkspaceLayout';

// Mock Sidebar and Breadcrumbs
jest.mock('../../components/Sidebar', () => {
  return function Sidebar() {
    return <div data-testid="sidebar">Sidebar</div>;
  };
});

jest.mock('../../components/Breadcrumbs', () => {
  return function Breadcrumbs() {
    return <div data-testid="breadcrumbs">Breadcrumbs</div>;
  };
});

// Mock useCommandPalette hook
jest.mock('../../hooks/useCommandPalette', () => ({
  useCommandPalette: () => ({
    isOpen: false,
    open: jest.fn(),
    close: jest.fn(),
    toggle: jest.fn(),
  }),
}));

const renderWithRouter = (path: string) => {
  return render(
    <MantineProvider>
      <MemoryRouter initialEntries={[path]}>
        <WorkspaceLayout>
          <div>Test Content</div>
        </WorkspaceLayout>
      </MemoryRouter>
    </MantineProvider>
  );
};

describe('WorkspaceLayout Component', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('renders sidebar, menu toggle, logo, and breadcrumbs for normal routes', () => {
    renderWithRouter('/inventory');
    expect(screen.getByTestId('sidebar')).toBeInTheDocument();
    expect(screen.getByTestId('breadcrumbs')).toBeInTheDocument();
    expect(screen.getByText('Test Content')).toBeInTheDocument();
    expect(screen.getByText(/dallasmakerspace/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/collapse sidebar|expand sidebar/i)).toBeInTheDocument();
  });

  it('hides sidebar for TV dashboard routes', () => {
    renderWithRouter('/facilities/tv-dashboard');
    expect(screen.queryByTestId('sidebar')).not.toBeInTheDocument();
    expect(screen.queryByTestId('breadcrumbs')).not.toBeInTheDocument();
    expect(screen.getByText('Test Content')).toBeInTheDocument();
  });

  it('hides sidebar for logistics dashboard routes', () => {
    renderWithRouter('/facilities/logistics');
    expect(screen.queryByTestId('sidebar')).not.toBeInTheDocument();
    expect(screen.queryByTestId('breadcrumbs')).not.toBeInTheDocument();
    expect(screen.getByText('Test Content')).toBeInTheDocument();
  });

  it('hides sidebar for legacy TV dashboard routes', () => {
    renderWithRouter('/tv-dashboard');
    expect(screen.queryByTestId('sidebar')).not.toBeInTheDocument();
  });

  it('hides sidebar for legacy logistics routes', () => {
    renderWithRouter('/tv-logistics');
    expect(screen.queryByTestId('sidebar')).not.toBeInTheDocument();
  });
});

