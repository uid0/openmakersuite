/**
 * The Playwright `createTestUser` fixture must return the created user's id.
 *
 * e2e/sig-dashboard.spec.ts seeds a SIG admin with `user: sigAdmin.id`, but the
 * fixture only returned `{ username, password, token }` — /auth/register/ and
 * /auth/login/ carry no id — so that request went out with no user at all.
 * Playwright specs need a live backend, so this drives the fixture against a
 * stubbed fetch instead.
 */
import { API_BASE_URL, createTestUser } from '../../../e2e/fixtures';

vi.mock('@playwright/test', () => ({ expect: vi.fn() }));

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

describe('createTestUser (e2e fixture)', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const routeProfile = (url: string, init?: RequestInit) => {
    expect(url).toBe(`${API_BASE_URL}/membership/profile/me/`);
    expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer tok-sigadmin');
    return json(200, { id: 42, username: 'sigadmin' });
  };

  test('a newly registered user comes back with their id', async () => {
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) =>
      url.endsWith('/auth/register/')
        ? json(201, { access: 'tok-sigadmin', username: 'sigadmin' })
        : routeProfile(url, init),
    );

    const user = await createTestUser('sigadmin', 'sigadmin123');

    expect(user).toEqual({
      id: 42,
      username: 'sigadmin',
      password: 'sigadmin123',
      token: 'tok-sigadmin',
    });
  });

  test('an existing user who logs in instead comes back with their id', async () => {
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/auth/register/')) {
        return json(400, { detail: 'Username already exists. Please choose another.' });
      }
      if (url.endsWith('/auth/login/')) {
        return json(200, { access: 'tok-sigadmin', username: 'sigadmin' });
      }
      return routeProfile(url, init);
    });

    const user = await createTestUser('sigadmin', 'sigadmin123');

    expect(user.id).toBe(42);
  });

  test('a profile lookup that fails is an error, not a user without an id', async () => {
    fetchMock.mockImplementation(async (url: string) =>
      url.endsWith('/auth/register/')
        ? json(201, { access: 'tok-sigadmin', username: 'sigadmin' })
        : json(500, { detail: 'boom' }),
    );

    await expect(createTestUser('sigadmin', 'sigadmin123')).rejects.toThrow(/user id/i);
  });
});
