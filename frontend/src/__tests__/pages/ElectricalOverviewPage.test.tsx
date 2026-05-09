/**
 * Tests for ElectricalOverviewPage (oms-b25 [6/7]).
 */
import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import ElectricalOverviewPage from '../../pages/ElectricalOverviewPage';

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <ElectricalOverviewPage />
      </MemoryRouter>
    </MantineProvider>,
  );

describe('ElectricalOverviewPage', () => {
  it('renders the workspace hero with the canonical title', () => {
    renderPage();
    expect(screen.getByTestId('page-hero-title')).toHaveTextContent(/electrical/i);
  });

  it('links the panel directory card to the new visualization', () => {
    renderPage();
    const panelCard = screen.getByTestId('electrical-overview-card-panels');
    expect(panelCard).toHaveAttribute('href', '/facilities/electrical/panels');
  });

  it('keeps the legacy fixture inventory accessible from the overview', () => {
    renderPage();
    const fixturesCard = screen.getByTestId('electrical-overview-card-fixtures');
    expect(fixturesCard).toHaveAttribute('href', '/facilities/electrical/fixtures');
  });

  it('exposes the asset power-chain lookup', () => {
    renderPage();
    const lookupCard = screen.getByTestId('electrical-overview-card-power-chain');
    expect(lookupCard).toHaveAttribute('href', '/facilities/electrical/power-chain');
  });
});
