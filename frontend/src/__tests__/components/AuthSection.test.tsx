/**
 * Tests for AuthSection — unified login/logout behaviour.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';

import AuthSection from '../../components/AuthSection';
import { authAPI } from '../../services/api';

vi.mock('../../services/api', () => ({
  authAPI: {
    login: jest.fn(),
    register: jest.fn(),
    logout: jest.fn(),
    refresh: jest.fn(),
  },
}));

const mockedAuth = authAPI as jest.Mocked<typeof authAPI>;

describe('AuthSection — unified auth flow', () => {
  beforeEach(() => {
    localStorage.clear();
    jest.clearAllMocks();
  });

  it('stores JWT and user flags after successful login', async () => {
    mockedAuth.login.mockResolvedValueOnce({
      data: {
        access: 'access-jwt',
        refresh: 'refresh-jwt',
        username: 'alice',
        email: 'alice@example.com',
        is_staff: true,
        is_superuser: false,
      },
    } as any);

    const onAuthChange = jest.fn();
    render(<AuthSection onAuthChange={onAuthChange} />);

    fireEvent.click(screen.getByRole('button', { name: /^login$/i }));
    fireEvent.change(screen.getByPlaceholderText(/username/i), {
      target: { value: 'alice' },
    });
    fireEvent.change(screen.getByPlaceholderText(/password/i), {
      target: { value: 'secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^login$/i }));

    await waitFor(() =>
      expect(mockedAuth.login).toHaveBeenCalledWith('alice', 'secret'),
    );
    await waitFor(() =>
      expect(localStorage.getItem('token')).toBe('access-jwt'),
    );
    expect(localStorage.getItem('refresh_token')).toBe('refresh-jwt');
    expect(localStorage.getItem('username')).toBe('alice');
    expect(localStorage.getItem('is_staff')).toBe('true');
    expect(localStorage.getItem('is_superuser')).toBe('false');
    expect(onAuthChange).toHaveBeenLastCalledWith(true, 'alice');
  });

  it('shows an error message when login fails', async () => {
    mockedAuth.login.mockRejectedValueOnce({
      response: { data: { detail: 'Invalid credentials' } },
    });

    render(<AuthSection onAuthChange={jest.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /^login$/i }));
    fireEvent.change(screen.getByPlaceholderText(/username/i), {
      target: { value: 'alice' },
    });
    fireEvent.change(screen.getByPlaceholderText(/password/i), {
      target: { value: 'wrong' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^login$/i }));

    expect(await screen.findByText('Invalid credentials')).toBeInTheDocument();
    expect(localStorage.getItem('token')).toBeNull();
  });

  it('calls the backend logout endpoint and clears local auth', async () => {
    localStorage.setItem('token', 'stale');
    localStorage.setItem('refresh_token', 'stale');
    localStorage.setItem('username', 'alice');
    localStorage.setItem('is_staff', 'false');
    localStorage.setItem('is_superuser', 'false');
    mockedAuth.logout.mockResolvedValueOnce({ data: { detail: 'Logged out' } } as any);

    const onAuthChange = jest.fn();
    render(<AuthSection onAuthChange={onAuthChange} />);

    // Component renders in the "logged in" branch because localStorage is set.
    await waitFor(() => expect(onAuthChange).toHaveBeenCalledWith(true, 'alice'));

    fireEvent.click(screen.getByRole('button', { name: /logout/i }));

    await waitFor(() => expect(mockedAuth.logout).toHaveBeenCalled());
    await waitFor(() => expect(localStorage.getItem('token')).toBeNull());
    expect(localStorage.getItem('refresh_token')).toBeNull();
    expect(localStorage.getItem('username')).toBeNull();
    expect(onAuthChange).toHaveBeenLastCalledWith(false);
  });

  it('still clears local auth when the backend logout request fails', async () => {
    localStorage.setItem('token', 'stale');
    localStorage.setItem('username', 'alice');
    mockedAuth.logout.mockRejectedValueOnce(new Error('network'));

    const onAuthChange = jest.fn();
    render(<AuthSection onAuthChange={onAuthChange} />);

    fireEvent.click(screen.getByRole('button', { name: /logout/i }));

    await waitFor(() => expect(localStorage.getItem('token')).toBeNull());
    expect(onAuthChange).toHaveBeenLastCalledWith(false);
  });
});
