/**
 * Tests for StatusState — the canonical loading/empty/forbidden/missing/error
 * /offline surface (AC-19, AC-20).
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';
import StatusState from '../../components/StatusState';

const renderWithMantine = (node: React.ReactElement) =>
  render(<MantineProvider>{node}</MantineProvider>);

describe('StatusState', () => {
  it('renders default loading variant with status role and polite live region', () => {
    renderWithMantine(<StatusState variant="loading" />);

    const region = screen.getByTestId('status-state-loading');
    expect(region).toHaveAttribute('role', 'status');
    expect(region).toHaveAttribute('aria-live', 'polite');
    expect(region).toHaveAttribute('data-variant', 'loading');
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it('renders empty variant with default copy and gray icon container', () => {
    renderWithMantine(<StatusState variant="empty" />);

    expect(screen.getByTestId('status-state-empty')).toBeInTheDocument();
    expect(screen.getByText(/nothing here yet/i)).toBeInTheDocument();
  });

  it('renders forbidden variant with assertive alert role', () => {
    renderWithMantine(<StatusState variant="forbidden" />);

    const region = screen.getByTestId('status-state-forbidden');
    expect(region).toHaveAttribute('role', 'alert');
    expect(region).toHaveAttribute('aria-live', 'assertive');
    expect(screen.getByText(/do not have access/i)).toBeInTheDocument();
  });

  it('renders missing variant with custom title and description override', () => {
    renderWithMantine(
      <StatusState
        variant="missing"
        title="No such asset"
        description="That asset tag does not exist in this makerspace."
      />
    );

    expect(screen.getByText('No such asset')).toBeInTheDocument();
    expect(
      screen.getByText('That asset tag does not exist in this makerspace.')
    ).toBeInTheDocument();
  });

  it('renders error variant with action buttons that fire callbacks', () => {
    const retry = jest.fn();
    const cancel = jest.fn();

    renderWithMantine(
      <StatusState
        variant="error"
        actions={[
          { label: 'Try again', onClick: retry },
          { label: 'Cancel', onClick: cancel, variant: 'subtle' },
        ]}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(retry).toHaveBeenCalledTimes(1);
    expect(cancel).toHaveBeenCalledTimes(1);
  });

  it('renders offline variant pointing to network recovery', () => {
    renderWithMantine(<StatusState variant="offline" />);

    expect(screen.getByTestId('status-state-offline')).toBeInTheDocument();
    expect(screen.getByText(/offline/i)).toBeInTheDocument();
    expect(screen.getByText(/check your network connection/i)).toBeInTheDocument();
  });

  it('respects an explicit testId override', () => {
    renderWithMantine(
      <StatusState variant="loading" testId="custom-loader" />
    );

    expect(screen.getByTestId('custom-loader')).toBeInTheDocument();
    expect(screen.queryByTestId('status-state-loading')).not.toBeInTheDocument();
  });
});
