/**
 * Tests for SessionExpiredBanner — verifies the banner appears on the
 * `oms:session-expired` event, persists a recoverable return-to path, and is
 * hidden again when dismissed (AC-17).
 */
import { MantineProvider } from '@mantine/core';
import { act, fireEvent, render, screen } from '@testing-library/react';
import React from 'react';
import SessionExpiredBanner, {
  consumePendingReturnTo,
} from '../../components/SessionExpiredBanner';

const renderWithMantine = (node: React.ReactElement) =>
  render(<MantineProvider>{node}</MantineProvider>);

const dispatchSessionExpired = (pathname?: string) => {
  act(() => {
    if (pathname) {
      window.dispatchEvent(
        new CustomEvent('oms:session-expired', { detail: { pathname } })
      );
    } else {
      window.dispatchEvent(new CustomEvent('oms:session-expired'));
    }
  });
};

describe('SessionExpiredBanner', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it('is hidden by default', () => {
    renderWithMantine(<SessionExpiredBanner />);
    expect(
      screen.queryByTestId('session-expired-banner')
    ).not.toBeInTheDocument();
  });

  it('appears on session-expired event and stores recoverable pathname', () => {
    renderWithMantine(<SessionExpiredBanner />);

    dispatchSessionExpired('/inventory/items/42/edit');

    expect(screen.getByTestId('session-expired-banner')).toBeInTheDocument();
    expect(screen.getByText(/your session expired/i)).toBeInTheDocument();
    expect(screen.getByText(/where you left off/i)).toBeInTheDocument();
    expect(consumePendingReturnTo()).toBe('/inventory/items/42/edit');
  });

  it('shows a generic message and stores nothing for non-recoverable paths', () => {
    renderWithMantine(<SessionExpiredBanner />);

    dispatchSessionExpired('/');

    expect(screen.getByTestId('session-expired-banner')).toBeInTheDocument();
    // The "where you left off" trailer only renders when we have a path to
    // restore to. Public landings should just say "Sign in again to continue."
    expect(screen.queryByText(/where you left off/i)).not.toBeInTheDocument();
    expect(consumePendingReturnTo()).toBeNull();
  });

  it('hides again when the user clicks Dismiss', () => {
    renderWithMantine(<SessionExpiredBanner />);

    dispatchSessionExpired('/inventory/items');
    expect(screen.getByTestId('session-expired-banner')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /dismiss/i }));

    expect(
      screen.queryByTestId('session-expired-banner')
    ).not.toBeInTheDocument();
  });

  it('exposes alert role for assistive technology', () => {
    renderWithMantine(<SessionExpiredBanner />);

    dispatchSessionExpired('/inventory/items');

    const banner = screen.getByTestId('session-expired-banner');
    expect(banner).toHaveAttribute('role', 'alert');
    expect(banner).toHaveAttribute('aria-live', 'assertive');
  });
});

describe('consumePendingReturnTo', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it('returns null when nothing is stored', () => {
    expect(consumePendingReturnTo()).toBeNull();
  });

  it('returns and clears the stored value on consume', () => {
    sessionStorage.setItem('oms_pending_return_to', '/maintenance/work-orders/9');
    expect(consumePendingReturnTo()).toBe('/maintenance/work-orders/9');
    expect(consumePendingReturnTo()).toBeNull();
  });
});
