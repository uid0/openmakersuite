/**
 * Regression guard for the @mantine/notifications (v9) auto-close timer leak
 * that reddened the frontend CI suite (gh-660 / oms-ltzrn).
 *
 * NotificationContainer schedules its auto-close `setTimeout(handleHide, …)`
 * from two effects sharing one `autoCloseTimeout` ref, so the first timer's id
 * is orphaned and survives unmount. Left alone, it fires after the test file's
 * jsdom is torn down and throws "window is not defined", failing the whole run
 * (flaky — passes in isolation, fails in the full suite).
 *
 * `setupTests.ts` clears leaked long-lived timers in a global afterEach. This
 * test proves it works: a notification's auto-close timer must NOT survive into
 * the next test. If the afterEach guard is removed, the orphaned timer fires
 * during the second test's wait and flips `closedAfterTeardown` — turning this
 * test deterministically red instead of letting the leak flake the suite.
 */
import { MantineProvider } from '@mantine/core';
import { Notifications, notifications } from '@mantine/notifications';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

// Survives across the two tests (module scope). Set if a notification's
// auto-close timer fires after its test ended — i.e. it leaked.
let closedAfterTeardown = false;
const AUTO_CLOSE_MS = 1200; // >= setupTests' leaked-timer threshold (1000ms)

describe('@mantine/notifications auto-close timer leak guard', () => {
  it('shows a notification, scheduling an auto-close timer', async () => {
    render(
      <MantineProvider>
        <Notifications />
      </MantineProvider>,
    );
    notifications.show({
      message: 'leak-guard',
      autoClose: AUTO_CLOSE_MS,
      onClose: () => {
        // Reached only if the auto-close timer actually fires. By the time the
        // next test runs, this test is over, so any firing here is a leak.
        closedAfterTeardown = true;
      },
    });
    expect(await screen.findByText('leak-guard')).toBeInTheDocument();
    // Test ends well before AUTO_CLOSE_MS; the global afterEach must clear the
    // pending auto-close timer.
  });

  it('does not let the previous auto-close timer fire after teardown', async () => {
    // Wait past the auto-close delay. If the timer leaked, it fires in here.
    await new Promise((resolve) => setTimeout(resolve, AUTO_CLOSE_MS + 600));
    expect(closedAfterTeardown).toBe(false);
  });
});
