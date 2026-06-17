/**
 * NotificationCenter Component Tests
 *
 * Covers the notification drawer for the #457 resilience program: the drawer's
 * modal focus-trap, keyboard dismissal, the empty state, and rendering of an
 * error-type notification.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import NotificationCenter from '../../components/NotificationCenter';
import type { BannerNotification, Notification } from '../../contexts/NotificationContext';

interface HookValue {
  banners: BannerNotification[];
  backendNotifications: Notification[];
  unreadCount: number;
  loadNotifications: ReturnType<typeof vi.fn>;
  markAsRead: ReturnType<typeof vi.fn>;
  markAllAsRead: ReturnType<typeof vi.fn>;
  deleteNotification: ReturnType<typeof vi.fn>;
  dismissBanner: ReturnType<typeof vi.fn>;
  clearBanners: ReturnType<typeof vi.fn>;
}

let hookValue: HookValue;

vi.mock('../../hooks/useNotifications', () => ({
  useNotifications: () => hookValue,
}));

const makeNotification = (overrides: Partial<Notification> = {}): Notification => ({
  id: '1',
  type: 'info',
  title: 'Title',
  message: 'Message',
  read: false,
  createdAt: new Date('2026-01-01T00:00:00Z'),
  ...overrides,
});

beforeEach(() => {
  hookValue = {
    banners: [],
    backendNotifications: [],
    unreadCount: 0,
    loadNotifications: vi.fn(),
    markAsRead: vi.fn(),
    markAllAsRead: vi.fn(),
    deleteNotification: vi.fn(),
    dismissBanner: vi.fn(),
    clearBanners: vi.fn(),
  };
});

const renderCenter = (isOpen = true) => {
  const onClose = vi.fn();
  const utils = render(
    <MantineProvider>
      <MemoryRouter>
        <NotificationCenter isOpen={isOpen} onClose={onClose} />
      </MemoryRouter>
    </MantineProvider>
  );
  return { onClose, ...utils };
};

describe('NotificationCenter', () => {
  it('renders a modal drawer and loads notifications when opened', () => {
    renderCenter(true);
    const dialog = screen.getByRole('dialog');
    expect(dialog).toBeInTheDocument();
    // aria-modal marks the drawer as a focus-trapping modal surface.
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(hookValue.loadNotifications).toHaveBeenCalled();
  });

  it('does not render the drawer when closed', () => {
    renderCenter(false);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('traps focus inside the drawer when open', async () => {
    renderCenter(true);
    const dialog = screen.getByRole('dialog');
    await waitFor(() =>
      expect(dialog).toContainElement(document.activeElement as HTMLElement)
    );
  });

  it('closes on Escape (keyboard dismissal)', () => {
    const { onClose } = renderCenter(true);
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('shows the empty state when there are no notifications', () => {
    renderCenter(true);
    expect(screen.getByText(/no notifications/i)).toBeInTheDocument();
  });

  it('renders an error-type notification', () => {
    hookValue.backendNotifications = [
      makeNotification({
        id: 'e1',
        type: 'error',
        title: 'Sync failed',
        message: 'Could not reach server',
        read: false,
      }),
    ];
    hookValue.unreadCount = 1;

    renderCenter(true);

    expect(screen.getByText('Sync failed')).toBeInTheDocument();
    expect(screen.getByText('Could not reach server')).toBeInTheDocument();
    expect(screen.getByText('✕')).toBeInTheDocument();
  });

  it('invokes markAllAsRead from the header action', async () => {
    hookValue.backendNotifications = [makeNotification({ read: false })];
    hookValue.unreadCount = 1;
    const user = userEvent.setup();

    renderCenter(true);
    await user.click(screen.getByRole('button', { name: /mark all read/i }));

    expect(hookValue.markAllAsRead).toHaveBeenCalled();
  });
});
