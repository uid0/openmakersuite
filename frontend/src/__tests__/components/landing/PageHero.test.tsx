/**
 * Smoke tests for the shared PageHero primitive (AC-2).
 */
import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import React from 'react';

import PageHero from '../../../components/landing/PageHero';

const renderHero = (props: Partial<React.ComponentProps<typeof PageHero>> = {}) =>
  render(
    <MantineProvider>
      <PageHero title="Run the shop." {...props} />
    </MantineProvider>
  );

describe('PageHero', () => {
  it('renders the title as an h1', () => {
    renderHero();
    expect(screen.getByRole('heading', { level: 1, name: /run the shop/i })).toBeInTheDocument();
  });

  it('renders the eyebrow when provided', () => {
    renderHero({ eyebrow: 'OpenMakerSuite' });
    expect(screen.getByTestId('page-hero-eyebrow')).toHaveTextContent('OpenMakerSuite');
  });

  it('omits the eyebrow when not provided', () => {
    renderHero();
    expect(screen.queryByTestId('page-hero-eyebrow')).not.toBeInTheDocument();
  });

  it('renders the description when provided', () => {
    renderHero({ description: 'One operations layer.' });
    expect(screen.getByText(/one operations layer/i)).toBeInTheDocument();
  });

  it('renders the action slot when provided', () => {
    renderHero({ action: <button>Share</button> });
    expect(screen.getByTestId('page-hero-action')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /share/i })).toBeInTheDocument();
  });

  it('omits the action slot when not provided', () => {
    renderHero();
    expect(screen.queryByTestId('page-hero-action')).not.toBeInTheDocument();
  });
});
