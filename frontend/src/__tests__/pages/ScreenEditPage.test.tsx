/**
 * Resilience tests for the Screen edit page (#457 R5, AC-19).
 *
 * Committee/SIG leaders configure kiosk screens here, so each non-happy state
 * must render a readable, recoverable surface rather than a blank screen or a
 * raw exception:
 *  - loading while the screen is fetched;
 *  - a readable error + recovery action when the screen can't be loaded
 *    (missing / forbidden / network);
 *  - a clear alert when saving fails;
 *  - content blocks rendered in their configured order (reorder mechanism).
 *
 * NOTE: ScreenEditPage reorders content blocks via each row's "Order" field
 * (the table re-sorts by `order`); there is no drag-and-drop widget, so the
 * reorder coverage exercises that order field rather than a DnD interaction.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ScreenEditPage from '../../pages/ScreenEditPage';
import { screensAPI } from '../../services/api';
import { Screen, ScreenContentBlock } from '../../types';

vi.mock('../../services/api');

const mockScreensAPI = screensAPI as jest.Mocked<typeof screensAPI>;

const buildBlock = (overrides: Partial<ScreenContentBlock> = {}): ScreenContentBlock => ({
  id: 'block-1',
  screen: 'screen-1',
  block_type: 'custom_text',
  block_type_display: 'Custom Text',
  title: 'Block',
  body: '',
  config: {},
  order: 0,
  is_enabled: true,
  ...overrides,
});

const buildScreen = (overrides: Partial<Screen> = {}): Screen => ({
  id: 'screen-1',
  slug: 'lobby',
  name: 'Lobby Screen',
  description: '',
  is_active: true,
  rotation_interval_seconds: 15,
  refresh_interval_seconds: 60,
  access_token: 'secret-token',
  is_online: true,
  content_blocks: [],
  ...overrides,
});

const renderPage = (slug = 'lobby') =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={[`/facilities/screens/${slug}`]}>
        <Routes>
          <Route path="/facilities/screens/:slug" element={<ScreenEditPage />} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('ScreenEditPage resilience', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('shows a readable loading state while the screen is fetched', () => {
    mockScreensAPI.getScreen.mockReturnValue(new Promise(() => {}) as never);
    renderPage();

    expect(screen.getByText('Loading screen…')).toBeInTheDocument();
  });

  it('shows a readable error state with a recovery action when the screen fails to load', async () => {
    mockScreensAPI.getScreen.mockRejectedValue(new Error('500'));
    renderPage();

    expect(await screen.findByText('Unable to load screen.')).toBeInTheDocument();
    // AC-19: a recovery path, not a dead end.
    expect(screen.getByRole('button', { name: 'Back to Screens' })).toBeInTheDocument();
  });

  it('alerts the user when saving metadata fails instead of throwing', async () => {
    const user = userEvent.setup();
    mockScreensAPI.getScreen.mockResolvedValue({ data: buildScreen() } as never);
    mockScreensAPI.updateScreen.mockRejectedValue(new Error('403'));
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});

    renderPage();

    const saveButton = await screen.findByRole('button', { name: 'Save' });
    await user.click(saveButton);

    await waitFor(() =>
      expect(alertSpy).toHaveBeenCalledWith('Unable to save. Check permissions.'),
    );
    alertSpy.mockRestore();
  });

  it('shows a readable empty state when the screen has no content blocks', async () => {
    mockScreensAPI.getScreen.mockResolvedValue({ data: buildScreen({ content_blocks: [] }) } as never);
    renderPage();

    expect(
      await screen.findByText(/This screen has no content blocks yet/i),
    ).toBeInTheDocument();
  });

  it('renders content block rows sorted by their order field', async () => {
    mockScreensAPI.getScreen.mockResolvedValue({
      data: buildScreen({
        content_blocks: [
          // Array order (Alpha, Bravo) deliberately differs from display order.
          buildBlock({ id: 'a', title: 'Alpha', order: 1 }),
          buildBlock({ id: 'b', title: 'Bravo', order: 0 }),
        ],
      }),
    } as never);

    renderPage();

    await screen.findByTestId('block-row-a');
    const rows = screen.getAllByTestId(/^block-row-/);
    // Bravo (order 0) renders before Alpha (order 1) regardless of array order.
    expect(rows.map((r) => r.getAttribute('data-testid'))).toEqual([
      'block-row-b',
      'block-row-a',
    ]);

    // The per-row "Order" field is the reorder control and reflects each value.
    expect(within(rows[0]).getByDisplayValue('0')).toBeInTheDocument();
    expect(within(rows[1]).getByDisplayValue('1')).toBeInTheDocument();
  });
});
