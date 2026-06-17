/**
 * OfflineIndicator Component Tests
 *
 * Anchors AC-16 (offline coverage) for the #457 resilience program: the
 * indicator is hidden while online and visible while offline, driven by the
 * shared offline helper (navigator.onLine + online/offline events).
 */
import { MantineProvider } from '@mantine/core';
import { act, render, screen } from '@testing-library/react';
import OfflineIndicator from '../../components/OfflineIndicator';
import { expectNoA11yViolations } from '../helpers/axe';
import { goOffline, goOnline } from '../helpers/offline';

const renderIndicator = () =>
  render(
    <MantineProvider>
      <OfflineIndicator />
    </MantineProvider>
  );

describe('OfflineIndicator', () => {
  it('renders nothing while online', () => {
    renderIndicator();
    expect(screen.queryByText(/you're offline/i)).not.toBeInTheDocument();
  });

  it('shows the offline alert when offline at mount', () => {
    goOffline();
    renderIndicator();
    expect(screen.getByText(/you're offline/i)).toBeInTheDocument();
    expect(screen.getByText(/some features may be limited/i)).toBeInTheDocument();
  });

  it('appears and disappears as connectivity toggles', () => {
    renderIndicator();
    expect(screen.queryByText(/you're offline/i)).not.toBeInTheDocument();

    act(() => goOffline());
    expect(screen.getByText(/you're offline/i)).toBeInTheDocument();

    act(() => goOnline());
    expect(screen.queryByText(/you're offline/i)).not.toBeInTheDocument();
  });

  it('has no accessibility violations while offline', async () => {
    goOffline();
    const { container } = renderIndicator();
    await expectNoA11yViolations(container);
  });
});
