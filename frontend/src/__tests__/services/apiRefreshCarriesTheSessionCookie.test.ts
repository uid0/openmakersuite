/**
 * The token refresh is also what keeps `/media/` open, and it has to carry the
 * cookie to do it (op-anonymous-read-posture).
 *
 * `auth_views._renew_session_from_token` slides the Django session cookie the
 * gated media prefixes run on, and it slides it ONLY when the refresh request
 * presents that cookie — by design, so a bearer token can never mint a session.
 * The SPA issues its refresh through the DEFAULT axios instance rather than the
 * `api` one, to avoid re-entering the response interceptor, and that bypass
 * also bypasses `api`'s `withCredentials: true`.
 *
 * A cross-origin XHR without credentials sends no cookie. `resolveApiBaseUrl()`
 * returns `http://localhost:8000/api` on localhost and `VITE_API_URL` can name
 * a separate API host in any split deployment, so in both the slide became a
 * silent no-op and every gated download 403'd from day 15. The backend test
 * cannot see this: Django's test client always carries its cookie jar.
 */
import axios from 'axios';
import MockAdapter from 'axios-mock-adapter';
import { describe, expect, it, beforeEach, afterEach } from 'vitest';

import api from '../../services/api';

let apiMock: MockAdapter;
let axiosMock: MockAdapter;

describe('the refresh the media session depends on', () => {
  beforeEach(() => {
    apiMock = new MockAdapter(api);
    axiosMock = new MockAdapter(axios);
    localStorage.clear();
    localStorage.setItem('token', 'stale-access-token');
    localStorage.setItem('refresh_token', 'good-refresh-token');
  });

  afterEach(() => {
    apiMock.restore();
    axiosMock.restore();
    localStorage.clear();
  });

  it('is sent with credentials, so the browser attaches the session cookie', async () => {
    const sent: (boolean | undefined)[] = [];
    axiosMock.onPost(/\/auth\/refresh\/$/).reply((config) => {
      sent.push(config.withCredentials);
      return [200, { access: 'fresh-access-token' }];
    });

    let seen = 0;
    apiMock.onGet('/inventory/items/').reply(() => {
      seen += 1;
      return seen === 1 ? [401, { detail: 'token expired' }] : [200, { results: [] }];
    });

    await api.get('/inventory/items/');

    expect(sent).toEqual([true]);
  });

  it('CONTROL: the instance every other request goes through already does', () => {
    expect(api.defaults.withCredentials).toBe(true);
  });
});
