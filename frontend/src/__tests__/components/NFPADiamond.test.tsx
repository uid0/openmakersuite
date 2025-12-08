/**
 * Tests for NFPADiamond component
 */
import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import React from 'react';
import NFPADiamond from '../../components/NFPADiamond';

// Ensure matchMedia is available
if (typeof window !== 'undefined' && !window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: jest.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: jest.fn(),
      removeListener: jest.fn(),
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
      dispatchEvent: jest.fn(),
    })),
    configurable: true,
  });
}

const renderWithProvider = (component: React.ReactElement) => {
  return render(<MantineProvider>{component}</MantineProvider>);
};

describe('NFPADiamond Component', () => {
  it('renders with all ratings', () => {
    renderWithProvider(
      <NFPADiamond
        health={2}
        flammability={3}
        instability={1}
        special="W,OX"
      />
    );

    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument();
    // Check for special hazards - it appears in both diamond and label, so use getAllByText
    const specialTexts = screen.getAllByText(/W,OX/);
    expect(specialTexts.length).toBeGreaterThan(0);
    expect(screen.getByText(/Special:/)).toBeInTheDocument();
  });

  it('renders with missing ratings', () => {
    renderWithProvider(
      <NFPADiamond
        health={2}
        flammability={null}
        instability={1}
        special=""
      />
    );

    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText('?')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument();
  });

  it('renders with no data', () => {
    renderWithProvider(
      <NFPADiamond
        health={null}
        flammability={null}
        instability={null}
        special=""
      />
    );

    expect(screen.getAllByText('?')).toHaveLength(3);
  });

  it('displays labels correctly', () => {
    renderWithProvider(
      <NFPADiamond
        health={2}
        flammability={3}
        instability={1}
        special="W"
      />
    );

    expect(screen.getByText(/Health:/)).toBeInTheDocument();
    expect(screen.getByText(/Fire:/)).toBeInTheDocument();
    expect(screen.getByText(/Instability:/)).toBeInTheDocument();
    expect(screen.getByText(/Special:/)).toBeInTheDocument();
  });

  it('handles custom size', () => {
    const { container } = renderWithProvider(
      <NFPADiamond
        health={1}
        flammability={1}
        instability={1}
        special=""
        size={200}
      />
    );

    const diamond = container.querySelector('div[style*="width: 200px"]');
    expect(diamond).toBeInTheDocument();
  });
});
