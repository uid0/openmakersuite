/**
 * Tests for MaintenanceHistorySection (oms-0xxlp2).
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';

import MaintenanceHistorySection from '../../components/assets/MaintenanceHistorySection';
import * as api from '../../services/api';

jest.mock('../../services/api', () => {
  const actual = jest.requireActual('../../services/api');
  return {
    __esModule: true,
    ...actual,
    default: { get: jest.fn() },
    assetsAPI: {
      ...actual.assetsAPI,
      getMaintenanceHistory: jest.fn(),
    },
    maintenanceRecordsAPI: {
      list: jest.fn(),
      get: jest.fn(),
      create: jest.fn(),
      update: jest.fn(),
      delete: jest.fn(),
    },
  };
});

const mockedApi = api as unknown as {
  default: { get: jest.Mock };
  assetsAPI: { getMaintenanceHistory: jest.Mock };
  maintenanceRecordsAPI: {
    create: jest.Mock;
    list: jest.Mock;
    get: jest.Mock;
    update: jest.Mock;
    delete: jest.Mock;
  };
};

const renderSection = (canManage = false) =>
  render(
    <MantineProvider>
      <MaintenanceHistorySection assetId="asset-1" canManage={canManage} />
    </MantineProvider>,
  );

const emptyResponse = {
  data: { count: 0, total_cost: '0', results: [] },
};

const oneHistoricalRow = {
  data: {
    count: 1,
    total_cost: '450.00',
    results: [
      {
        id: 'rec-1',
        source: 'historical' as const,
        title: 'Annual HVAC service',
        description: 'Filter swap',
        completed_on: '2024-03-14',
        performed_by: {
          vendor: { id: 'v-1', name: 'ACME HVAC' },
          internal_user: null,
        },
        cost: '450.00',
        invoice_number: 'INV-1',
        notes: '',
        attachment_url: null,
        detail_url: null,
      },
    ],
  },
};

const oneWorkOrderRow = {
  data: {
    count: 1,
    total_cost: '2300.00',
    results: [
      {
        id: 'wo-1',
        source: 'workorder' as const,
        title: 'Compressor swap',
        description: '',
        completed_on: '2023-01-05',
        performed_by: {
          vendor: { id: 'v-2', name: 'CompressCo' },
          internal_user: null,
        },
        cost: '2300.00',
        invoice_number: '',
        notes: '',
        attachment_url: null,
        detail_url: '/work-orders/wo-1',
      },
    ],
  },
};

describe('MaintenanceHistorySection', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockedApi.default.get.mockResolvedValue({ data: { results: [] } });
  });

  it('shows the empty state when there are no records', async () => {
    mockedApi.assetsAPI.getMaintenanceHistory.mockResolvedValue(emptyResponse);
    renderSection();
    await waitFor(() => {
      expect(
        screen.getByText(/No maintenance recorded/i),
      ).toBeInTheDocument();
    });
    expect(screen.getByTestId('maintenance-history-rollup').textContent).toMatch(
      /Total in range: \$0/i,
    );
  });

  it('renders historical and workorder rows when present', async () => {
    mockedApi.assetsAPI.getMaintenanceHistory.mockResolvedValue({
      data: {
        count: 2,
        total_cost: '2750.00',
        results: [...oneHistoricalRow.data.results, ...oneWorkOrderRow.data.results],
      },
    });
    renderSection();
    await waitFor(() => {
      expect(screen.getByText('Annual HVAC service')).toBeInTheDocument();
    });
    expect(screen.getByText('Compressor swap')).toBeInTheDocument();
    expect(screen.getAllByText('Logged Work').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Outsourced WO').length).toBeGreaterThan(0);
    expect(screen.getByText('$450.00')).toBeInTheDocument();
    expect(screen.getByText('$2300.00')).toBeInTheDocument();
    expect(screen.getByTestId('maintenance-history-rollup').textContent).toMatch(
      /Total in range: \$2750.00 across 2 records/i,
    );
  });

  it('hides the Log Historical Work button from non-staff', async () => {
    mockedApi.assetsAPI.getMaintenanceHistory.mockResolvedValue(emptyResponse);
    renderSection(false);
    await waitFor(() => {
      expect(screen.getByText(/No maintenance recorded/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/\+ Log Historical Work/i)).not.toBeInTheDocument();
  });

  it('shows the Log Historical Work button for staff and toggles the form', async () => {
    mockedApi.assetsAPI.getMaintenanceHistory.mockResolvedValue(emptyResponse);
    renderSection(true);
    await waitFor(() => {
      expect(screen.getByText(/No maintenance recorded/i)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText(/\+ Log Historical Work/i));
    expect(screen.getByText(/Log Historical Work$/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Title \*/i)).toBeInTheDocument();
  });

  it('re-fetches when the source filter changes', async () => {
    mockedApi.assetsAPI.getMaintenanceHistory.mockResolvedValue(emptyResponse);
    renderSection();
    await waitFor(() => {
      expect(mockedApi.assetsAPI.getMaintenanceHistory).toHaveBeenCalledTimes(1);
    });
    fireEvent.click(screen.getByText('Outsourced WO'));
    await waitFor(() => {
      expect(mockedApi.assetsAPI.getMaintenanceHistory).toHaveBeenLastCalledWith(
        'asset-1',
        expect.objectContaining({ source: 'workorder' }),
      );
    });
  });

  it('blocks form submission when title is missing', async () => {
    mockedApi.assetsAPI.getMaintenanceHistory.mockResolvedValue(emptyResponse);
    renderSection(true);
    await waitFor(() => {
      expect(screen.getByText(/No maintenance recorded/i)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText(/\+ Log Historical Work/i));
    fireEvent.click(screen.getByText('Save record'));
    // HTML5 required validation prevents submit; no API call should fire.
    expect(mockedApi.maintenanceRecordsAPI.create).not.toHaveBeenCalled();
  });
});
