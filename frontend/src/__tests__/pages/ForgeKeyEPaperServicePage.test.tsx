/**
 * Tests for the ForgeKey ePaper "work order" page (scan-to-log).
 *
 * Covers:
 *   1. The work order surfaces where the power is, the tools to gather
 *      (with where-they-live + on-hand counts), and the consumables.
 *   2. A free-text tool location (no inventory link) still renders.
 *   3. "Mark complete" is gated on login, and when logged in it POSTs the
 *      completion for the right item.
 *   4. Missing `did` shows the help card.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ForgeKeyEPaperServicePage from '../../pages/ForgeKeyEPaperServicePage';
import { forgekeyAPI, inventoryAPI } from '../../services/api';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    forgekeyAPI: {
      ...actual.forgekeyAPI,
      getEPaperServiceInfo: jest.fn(),
      completeEPaperService: jest.fn(),
    },
    inventoryAPI: {
      ...actual.inventoryAPI,
      listLocations: jest.fn(),
    },
  };
});

const mockForgekey = forgekeyAPI as jest.Mocked<typeof forgekeyAPI>;
const mockLocations = inventoryAPI as jest.Mocked<typeof inventoryAPI>;

const DID = '11111111-2222-3333-4444-555555555555';

const buildServiceInfo = (overrides: Partial<any> = {}) => ({
  display_id: DID,
  bound: true,
  asset: {
    id: 'a1',
    name: 'Lathe',
    asset_tag: 'LA-001',
    location: 'Machine shop',
    location_id: 'loc5',
  },
  power: {
    wiring_type: 'Hardwired',
    breaker: {
      label: 'Lathe feed',
      position: '12',
      amperage: 20,
      panel: 'Panel A',
      panel_location: 'Electrical room',
    },
    disconnect: null,
    breaker_location: '',
    electrical_box: '',
    suite: '',
  },
  loto: { instructions: '', energy_sources: [] },
  items: [
    {
      id: 'i1',
      title: 'Lube',
      interval_days: 30,
      status: 'ok',
      days_until_due: 10,
      status_line: 'Due in 10 days',
      last_completed: '2026-05-01',
      instructions: 'Use way oil only.',
      estimated_time_minutes: 20,
      steps: [{ order: 1, title: 'Wipe ways', description: '', is_required: true }],
      tools: [
        {
          name: '17mm wrench',
          quantity: 1,
          is_required: true,
          notes: '',
          location: 'Tool crib',
          on_hand: 3,
          sku: '',
          inventory_item_id: 'inv1',
        },
        {
          name: 'Torque wrench',
          quantity: 1,
          is_required: false,
          notes: '',
          location: 'Calibration shelf, bay 2',
          on_hand: null,
          sku: '',
          inventory_item_id: null,
        },
      ],
      materials: [
        {
          name: 'Way oil',
          quantity: '50',
          unit: 'ml',
          location: 'Oil cabinet',
          on_hand: 7,
          sku: '',
          inventory_item_id: 'inv2',
        },
      ],
    },
  ],
  primary_item_id: 'i1',
  ...overrides,
});

const renderPage = (initialEntry = `/forgekey/epaper/service?did=${DID}`) =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/forgekey/epaper/service" element={<ForgeKeyEPaperServicePage />} />
          <Route path="/" element={<div>HOME</div>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('ForgeKeyEPaperServicePage', () => {
  beforeEach(() => {
    jest.resetAllMocks();
    localStorage.clear();
    mockLocations.listLocations.mockResolvedValue({
      data: [
        { id: 'loc5', name: 'Machine shop' },
        { id: 'loc9', name: 'Annex bench' },
      ],
    } as any);
  });

  afterEach(() => {
    localStorage.clear();
  });

  it('surfaces power, tools (where + on-hand), and consumables', async () => {
    mockForgekey.getEPaperServiceInfo.mockResolvedValue({ data: buildServiceInfo() } as any);

    renderPage();

    expect(await screen.findByText('Lathe')).toBeInTheDocument();

    // Power — where to kill it before servicing.
    expect(screen.getByText(/find this before servicing/)).toBeInTheDocument();
    expect(screen.getByText(/Panel A/)).toBeInTheDocument();
    expect(screen.getByText(/Electrical room/)).toBeInTheDocument();

    // Tools to gather, with where they live + how many are on hand.
    expect(screen.getByText('Tools to gather')).toBeInTheDocument();
    expect(screen.getByText('17mm wrench')).toBeInTheDocument();
    expect(screen.getByText(/Tool crib/)).toBeInTheDocument();
    expect(screen.getByText(/3 on hand/)).toBeInTheDocument();
    // A free-text tool location (no inventory link) still renders.
    expect(screen.getByText(/Calibration shelf, bay 2/)).toBeInTheDocument();

    // Consumables, with location + on-hand.
    expect(screen.getByText('Consumables')).toBeInTheDocument();
    expect(screen.getByText(/Way oil/)).toBeInTheDocument();
    expect(screen.getByText(/Oil cabinet/)).toBeInTheDocument();
    expect(screen.getByText(/7 on hand/)).toBeInTheDocument();
  });

  it('gates completion on login when no token is present', async () => {
    mockForgekey.getEPaperServiceInfo.mockResolvedValue({ data: buildServiceInfo() } as any);

    renderPage();

    expect(await screen.findByText('Log in to mark complete')).toBeInTheDocument();
    expect(mockForgekey.completeEPaperService).not.toHaveBeenCalled();
  });

  it('logs a completion for the item when logged in', async () => {
    localStorage.setItem('token', 'jwt');
    mockForgekey.getEPaperServiceInfo.mockResolvedValue({ data: buildServiceInfo() } as any);
    mockForgekey.completeEPaperService.mockResolvedValue({
      data: { ok: true, item_id: 'i1', title: 'Lube', status_line: 'Logged just now' },
    } as any);

    renderPage();

    const btn = await screen.findByText('Mark complete');
    fireEvent.click(btn);

    await waitFor(() => {
      expect(mockForgekey.completeEPaperService).toHaveBeenCalledWith(
        DID,
        expect.objectContaining({ item_id: 'i1', location_id: 'loc5' }),
      );
    });
    expect(await screen.findByText('Completed — thank you')).toBeInTheDocument();
  });

  it('attaches a photo of the work when one is selected', async () => {
    localStorage.setItem('token', 'jwt');
    mockForgekey.getEPaperServiceInfo.mockResolvedValue({ data: buildServiceInfo() } as any);
    mockForgekey.completeEPaperService.mockResolvedValue({
      data: {
        ok: true,
        item_id: 'i1',
        title: 'Lube',
        status_line: 'Logged',
        photo_attached: true,
      },
    } as any);

    const { container } = renderPage();

    // The completion controls only render once logged in.
    await screen.findByTestId('completion-details');

    const file = new File(['x'], 'work.jpg', { type: 'image/jpeg' });
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    // The staged filename confirms the photo is attached.
    expect(await screen.findByText('work.jpg')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Mark complete'));

    await waitFor(() => {
      expect(mockForgekey.completeEPaperService).toHaveBeenCalledWith(
        DID,
        expect.objectContaining({ item_id: 'i1', photo: file }),
      );
    });
  });

  it('shows the help card when did is missing', async () => {
    renderPage('/forgekey/epaper/service');

    expect(await screen.findByText('No display_id')).toBeInTheDocument();
    expect(mockForgekey.getEPaperServiceInfo).not.toHaveBeenCalled();
  });
});
