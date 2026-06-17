/**
 * ErrorFallback Component Tests
 *
 * Covers the error-recovery surface for the #457 resilience program:
 * render-on-catch, the recovery action, the alert role, focus management, and
 * a clean axe audit.
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import ErrorFallback from '../../components/ErrorFallback';
import { expectNoA11yViolations } from '../helpers/axe';

/** Minimal boundary that renders ErrorFallback when a child throws. */
class TestErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <ErrorFallback
          error={this.state.error}
          resetError={() => this.setState({ error: null })}
        />
      );
    }
    return this.props.children;
  }
}

describe('ErrorFallback', () => {
  it('renders the error UI with an alert role', () => {
    render(<ErrorFallback error={new Error('boom')} resetError={vi.fn()} />);
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
  });

  it('invokes the recovery action when "Try Again" is clicked', async () => {
    const resetError = vi.fn();
    const user = userEvent.setup();
    render(<ErrorFallback error={new Error('boom')} resetError={resetError} />);

    await user.click(screen.getByRole('button', { name: /try again/i }));
    expect(resetError).toHaveBeenCalledTimes(1);
  });

  it('moves focus to the recovery button on mount', () => {
    render(<ErrorFallback error={new Error('boom')} resetError={vi.fn()} />);
    expect(screen.getByRole('button', { name: /try again/i })).toHaveFocus();
  });

  it('renders as the fallback when a child component throws', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    const Boom = () => {
      throw new Error('kaboom');
    };

    render(
      <TestErrorBoundary>
        <Boom />
      </TestErrorBoundary>
    );

    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
    consoleError.mockRestore();
  });

  it('has no accessibility violations', async () => {
    const { container } = render(
      <ErrorFallback error={new Error('boom')} resetError={vi.fn()} />
    );
    await expectNoA11yViolations(container);
  });
});
