/**
 * A stale access token must not turn a public page into an error page
 * (op-anonymous-read-posture).
 *
 * `/reorders/analytics/transparency/` and `/reorders/purchase-orders/` sat on
 * the response interceptor's `isPublicEndpoint` list, which SKIPS the
 * refresh-and-retry path, under the justification "public endpoints that don't
 * require authentication". This branch made that untrue of both:
 *
 *  - purchase orders became `IsAuthenticated` on every action; and
 *  - the transparency action lost `authentication_classes=[]`, which it had to,
 *    because that setting made `request.user` `AnonymousUser` for a SIGNED-IN
 *    caller and no vendor gate could have told the two apart. It therefore runs
 *    `CSRFExemptJWTAuthentication`, so an expired Bearer raises `InvalidToken`
 *    and DRF answers 401 before `AllowAny` is ever consulted.
 *
 * The failing sequence, and what each test below replays: a member signs in,
 * leaves the tab, the access token expires, they open the page. The request
 * interceptor attaches the stale token, the server answers 401, and — with the
 * URL on that list — no refresh was attempted, so a page that is public to
 * everybody else showed them an error.
 *
 * `logistics_dashboard` KEEPS `authentication_classes=[]` and stays on the
 * list; the last test is the control that says so, because if the list had
 * simply been emptied these tests would pass while a genuinely unauthenticated
 * endpoint started chasing refreshes on somebody else's 401.
 */
import axios from 'axios';
import MockAdapter from 'axios-mock-adapter';
import api from '../../services/api';

/** The response interceptor refreshes through the DEFAULT axios instance. */
let apiMock: MockAdapter;
let axiosMock: MockAdapter;

const LEDGER = {
  summary: {
    total_orders_with_financial_data: 0,
    total_amount_spent: 0,
    last_updated: '2026-01-15T12:00:00Z',
    transparency_note: 'Dallas Makerspace publishes what it spends.',
  },
  orders: [],
  ledger: [],
  purchase_orders: [],
};

/** 401 once with the stale token, then the real payload with the fresh one. */
const expiresOnceThenSucceeds = (url: string, payload: unknown) => {
  let seen = 0;
  apiMock.onGet(url).reply(() => {
    seen += 1;
    return seen === 1 ? [401, { detail: 'Given token not valid for any token type' }] : [200, payload];
  });
  return () => seen;
};

describe('a stale access token on a page a logged-out visitor can read', () => {
  beforeEach(() => {
    apiMock = new MockAdapter(api);
    axiosMock = new MockAdapter(axios);
    localStorage.clear();
    localStorage.setItem('token', 'stale-access-token');
    localStorage.setItem('refresh_token', 'good-refresh-token');
    axiosMock.onPost(/\/auth\/refresh\/$/).reply(200, { access: 'fresh-access-token' });
  });

  afterEach(() => {
    apiMock.restore();
    axiosMock.restore();
    localStorage.clear();
  });

  it('refreshes and retries the transparency feed instead of failing the page', async () => {
    const attempts = expiresOnceThenSucceeds('/reorders/analytics/transparency/', LEDGER);

    const response = await api.get('/reorders/analytics/transparency/');

    expect(response.status).toBe(200);
    expect(response.data).toEqual(LEDGER);
    expect(attempts()).toBe(2);
    expect(localStorage.getItem('token')).toBe('fresh-access-token');
  });

  it('sends the retry with the refreshed token, not the stale one', async () => {
    let seen = 0;
    const sentAuthorization: (string | undefined)[] = [];
    apiMock.onGet('/reorders/analytics/transparency/').reply((config) => {
      seen += 1;
      sentAuthorization.push(config.headers?.Authorization as string | undefined);
      return seen === 1 ? [401, {}] : [200, LEDGER];
    });

    await api.get('/reorders/analytics/transparency/');

    expect(sentAuthorization).toEqual([
      'Bearer stale-access-token',
      'Bearer fresh-access-token',
    ]);
  });

  it('refreshes and retries the purchase-order list too', async () => {
    const orders = { count: 0, results: [] };
    const attempts = expiresOnceThenSucceeds('/reorders/purchase-orders/', orders);

    const response = await api.get('/reorders/purchase-orders/');

    expect(response.status).toBe(200);
    expect(response.data).toEqual(orders);
    expect(attempts()).toBe(2);
  });

  it('CONTROL: does not chase a refresh for logistics_dashboard, which runs no auth', async () => {
    let seen = 0;
    apiMock.onGet('/reorders/analytics/logistics_dashboard/').reply(() => {
      seen += 1;
      return [401, { detail: 'nope' }];
    });

    await expect(api.get('/reorders/analytics/logistics_dashboard/')).rejects.toBeTruthy();
    expect(seen).toBe(1);
    // The stale token is left alone: nothing here proved it was the problem.
    expect(localStorage.getItem('token')).toBe('stale-access-token');
  });
});
