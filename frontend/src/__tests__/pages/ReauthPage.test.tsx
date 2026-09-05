/**
 * The remedy a refused /media/ download offers, walked end to end.
 *
 * DAY 16. An operator signs in, then leaves the app alone for over a
 * fortnight. The browser has dropped the session cookie (Django's default
 * SESSION_COOKIE_AGE is 14 days) but localStorage still holds a refresh token
 * good for 30, so the first 401 refreshes and every API call works — this
 * browser considers them signed in. They click a purchase-order invoice link,
 * which is an ordinary navigation, and the server refuses it with a page that
 * says "Sign in".
 *
 * The control below is why that page cannot point at `/`, and the test above it
 * is the route that fixes it.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

import AuthSection from '../../components/AuthSection';
import ReauthPage from '../../pages/ReauthPage';
import { REAUTH_PATH } from '../../services/authStorage';

vi.mock('../../services/api', () => ({
  authAPI: {
    login: vi.fn(),
    logout: vi.fn().mockResolvedValue({}),
    register: vi.fn(),
  },
}));

/** What day 16 looks like in this browser: signed in, as far as it knows. */
const signInLocally = () => {
  localStorage.setItem('token', 'still-valid-refreshable-access-token');
  localStorage.setItem('refresh_token', 'still-valid-refresh-token');
  localStorage.setItem('username', 'zzqq-operator');
};

const renderAt = (path: string) =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={REAUTH_PATH} element={<ReauthPage />} />
          <Route path="/" element={<AuthSection onAuthChange={() => {}} />} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('the remedy a refused vendor download offers', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });
  afterEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  it('CONTROL: `/` is a dead end — it greets the refused reader as signed in', async () => {
    signInLocally();

    renderAt('/');

    // This is what the old remedy sent them to, and there is nothing here they
    // can act on: no form, no way back to the document.
    expect(await screen.findByText(/Welcome back/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Login' })).not.toBeInTheDocument();
  });

  it('presents a sign-in form to a reader who still holds a refresh token', async () => {
    const user = userEvent.setup();
    signInLocally();

    renderAt(REAUTH_PATH);

    const login = await screen.findByRole('button', { name: 'Login' });
    expect(screen.queryByText(/Welcome back/)).not.toBeInTheDocument();

    await user.click(login);
    expect(await screen.findByPlaceholderText('Username')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Password')).toBeInTheDocument();
  });

  it('drops the stale credential rather than leaving it to fail again', async () => {
    signInLocally();

    renderAt(REAUTH_PATH);

    await waitFor(() => expect(localStorage.getItem('token')).toBeNull());
    expect(localStorage.getItem('refresh_token')).toBeNull();
    expect(localStorage.getItem('username')).toBeNull();
  });

  it('keeps the destination the refusal page stored, so the document opens', async () => {
    // The refusal page reads the address bar it is already sitting on and puts
    // it here; nothing on the server side of that ever names the path.
    sessionStorage.setItem('oms_pending_return_to', '/media/supplier_agreements/terms.pdf');
    signInLocally();

    renderAt(REAUTH_PATH);

    await screen.findByRole('button', { name: 'Login' });
    expect(sessionStorage.getItem('oms_pending_return_to')).toBe(
      '/media/supplier_agreements/terms.pdf',
    );
  });
});
