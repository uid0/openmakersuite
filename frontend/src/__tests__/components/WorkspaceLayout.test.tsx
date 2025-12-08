/**
 * WorkspaceLayout Component Tests
 */
import { render, screen } from '@testing-library/react';
import React from 'react';
import { BrowserRouter, MemoryRouter } from 'react-router-dom';
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

const renderWithRouter = (path: string) => {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <WorkspaceLayout>
        <div>Test Content</div>
      </WorkspaceLayout>
    </MemoryRouter>
  );
};

describe('WorkspaceLayout Component', () => {
  it('renders sidebar and breadcrumbs for normal routes', () => {
    renderWithRouter('/inventory');
    expect(screen.getByTestId('sidebar')).toBeInTheDocument();
    expect(screen.getByTestId('breadcrumbs')).toBeInTheDocument();
    expect(screen.getByText('Test Content')).toBeInTheDocument();
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

