/**
 * Tests for ThirdPartyWorkOrderPage stepper.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ThirdPartyWorkOrderPage from '../../pages/ThirdPartyWorkOrderPage';
import {
  thirdPartyMaintenanceAPI,
  ThirdPartyWorkOrderDto,
} from '../../services/api';

vi.mock('../../services/api', () => ({
  thirdPartyMaintenanceAPI: {
    retrieve: jest.fn(),
    setNte: jest.fn(),
    authorizeEmergency: jest.fn(),
    advanceToSourcing: jest.fn(),
    waiveQuoteRequirement: jest.fn(),
    advanceToScheduled: jest.fn(),
    vendorArrived: jest.fn(),
    signOff: jest.fn(),
    advanceToFinancialReview: jest.fn(),
    close: jest.fn(),
    addQuote: jest.fn(),
  },
}));

const mocked = thirdPartyMaintenanceAPI as jest.Mocked<typeof thirdPartyMaintenanceAPI>;

const baseWo: ThirdPartyWorkOrderDto = {
  id: 'wo-1',
  short_id: 'TPWO-ABCDEF12',
  title: 'HVAC repair',
  asset: 'a-1',
  asset_name: 'AC Unit 4',
  vendor: 'v-1',
  vendor_name: 'ACME HVAC',
  work_type: 'standard',
  work_type_display: 'Standard',
  is_emergency: false,
  status: 'requested',
  status_display: 'Requested',
  nte_amount: null,
  par_cost_buffer: '0',
  actual_invoice_total: null,
  dispatch_fee: null,
  downtime_start: null,
  downtime_end: null,
  total_downtime: null,
  keyfob_id: '',
  warranty_recovery: false,
  variance_status: '',
  asset_links: [],
  attachments: [],
  quotes: [],
  workflow: {
    has_nte: false,
    has_active_emergency_authorization: false,
    has_required_quotes: false,
    quote_count: 0,
    has_photo_evidence: false,
    has_invoice_and_fsr: false,
    variance_status: '',
  },
  notes: '',
  internal_notes: '',
  created_at: '2026-04-28T00:00:00Z',
  updated_at: '2026-04-28T00:00:00Z',
  closed_at: null,
};

const renderPage = (path = '/maintenance/third-party/wo-1') =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/maintenance/third-party/:id"
            element={<ThirdPartyWorkOrderPage />}
          />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('ThirdPartyWorkOrderPage', () => {
  beforeEach(() => {
    Object.values(mocked).forEach((fn) => (fn as jest.Mock).mockReset());
  });

  it('renders the WO header and active step from status', async () => {
    mocked.retrieve.mockResolvedValueOnce({ data: baseWo } as never);
    renderPage();
    await waitFor(() => expect(mocked.retrieve).toHaveBeenCalled());
    expect(await screen.findByText(/TPWO-ABCDEF12/)).toBeInTheDocument();
    expect(screen.getByText('Requested')).toBeInTheDocument();
  });

  it('shows variance-blocked alert during financial review', async () => {
    mocked.retrieve.mockResolvedValueOnce({
      data: {
        ...baseWo,
        status: 'financial_review',
        status_display: 'Financial Review',
        nte_amount: '1000.00',
        actual_invoice_total: '1500.00',
        variance_status: 'blocked',
        workflow: {
          ...baseWo.workflow,
          has_nte: true,
          has_required_quotes: true,
          has_photo_evidence: true,
          has_invoice_and_fsr: true,
          variance_status: 'blocked',
        },
      },
    } as never);
    renderPage();
    expect(
      await screen.findByText(/Variance over budget/),
    ).toBeInTheDocument();
  });

  it('blocks closure when invoice or FSR missing', async () => {
    mocked.retrieve.mockResolvedValueOnce({
      data: {
        ...baseWo,
        status: 'financial_review',
        status_display: 'Financial Review',
        nte_amount: '1000.00',
        actual_invoice_total: '950.00',
        variance_status: 'auto_approved',
        workflow: {
          ...baseWo.workflow,
          has_nte: true,
          has_required_quotes: true,
          has_photo_evidence: true,
          has_invoice_and_fsr: false,
          variance_status: 'auto_approved',
        },
      },
    } as never);
    renderPage();
    const closeBtn = await screen.findByRole('button', {
      name: /Close Work Order/,
    });
    expect(closeBtn).toBeDisabled();
    expect(screen.getByText(/Need Invoice \+ FSR/)).toBeInTheDocument();
  });

  it('disables advance-to-scheduled when no quotes', async () => {
    mocked.retrieve.mockResolvedValueOnce({
      data: {
        ...baseWo,
        status: 'sourcing',
        status_display: 'Sourcing',
        nte_amount: '500.00',
        workflow: {
          ...baseWo.workflow,
          has_nte: true,
          has_required_quotes: false,
        },
      },
    } as never);
    renderPage();
    const btn = await screen.findByRole('button', {
      name: /Advance to Scheduled/,
    });
    expect(btn).toBeDisabled();
  });

  it('advances to sourcing when NTE is set', async () => {
    mocked.retrieve.mockResolvedValueOnce({
      data: {
        ...baseWo,
        nte_amount: '500.00',
        workflow: { ...baseWo.workflow, has_nte: true },
      },
    } as never);
    mocked.advanceToSourcing.mockResolvedValueOnce({
      data: { ...baseWo, status: 'sourcing' },
    } as never);
    mocked.retrieve.mockResolvedValueOnce({
      data: { ...baseWo, status: 'sourcing', status_display: 'Sourcing' },
    } as never);
    renderPage();
    const advance = await screen.findByRole('button', {
      name: /Advance to Sourcing/,
    });
    expect(advance).not.toBeDisabled();
    fireEvent.click(advance);
    await waitFor(() =>
      expect(mocked.advanceToSourcing).toHaveBeenCalledWith('wo-1'),
    );
  });
});
