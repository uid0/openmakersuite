/**
 * NotificationBadge Component Tests
 */
import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import NotificationBadge from '../../components/NotificationBadge';

const renderBadge = (count: number, onClick?: () => void) => {
  return render(
    <MantineProvider>
      <NotificationBadge count={count} onClick={onClick} />
    </MantineProvider>
  );
};

describe('NotificationBadge Component', () => {
  it('does not render when count is 0', () => {
    const { container } = renderBadge(0);
    // Component returns null, but MantineProvider still renders styles
    // Check that the badge button is not in the document
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const badgeButton = container.querySelector('.notification-badge');
    expect(badgeButton).toBeNull();
  });

  it('renders count when count is greater than 0', () => {
    renderBadge(5);
    expect(screen.getByText('5')).toBeInTheDocument();
  });

  it('shows 99+ when count exceeds 99', () => {
    renderBadge(100);
    expect(screen.getByText('99+')).toBeInTheDocument();
  });

  it('calls onClick when clicked', () => {
    const mockOnClick = jest.fn();
    renderBadge(3, mockOnClick);

    const badge = screen.getByRole('button', { name: /3 unread notifications/i });
    expect(badge).toBeInTheDocument();

    badge.click();
    expect(mockOnClick).toHaveBeenCalledTimes(1);
  });

  it('exposes the unread count in its accessible name', () => {
    renderBadge(7);
    expect(
      screen.getByRole('button', { name: /7 unread notifications/i })
    ).toBeInTheDocument();
  });

  it('renders single-digit, double-digit, and 99+ counts', () => {
    const { rerender } = renderBadge(1);
    expect(screen.getByText('1')).toBeInTheDocument();

    rerender(
      <MantineProvider>
        <NotificationBadge count={42} />
      </MantineProvider>
    );
    expect(screen.getByText('42')).toBeInTheDocument();

    rerender(
      <MantineProvider>
        <NotificationBadge count={250} />
      </MantineProvider>
    );
    expect(screen.getByText('99+')).toBeInTheDocument();
  });
});
