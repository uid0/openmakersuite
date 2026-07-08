/**
 * Tests for the Asset Meters section (EAM bead-1): meter display (value, unit,
 * measured/estimated, source), and the staff-gated manual-first controls —
 * record reading (absolute / delta / estimated), adjust with a required reason,
 * and add meter.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import AssetMetersSection from '../../components/assets/AssetMetersSection';
import { assetMetersAPI } from '../../services/api';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    assetMetersAPI: {
      list: jest.fn(),
      create: jest.fn(),
      recordReading: jest.fn(),
      adjust: jest.fn(),
      delete: jest.fn(),
      listReadings: jest.fn(),
    },
  };
});

const mockMetersApi = assetMetersAPI as jest.Mocked<typeof assetMetersAPI>;

const buildMeter = (overrides: Partial<any> = {}) => ({
  id: 'm1',
  asset: 'a1',
  name: 'Water dispensed',
  meter_type: 'volume_gallons',
  meter_type_display: 'Volume (gallons)',
  unit: 'gallons',
  source: 'manual',
  source_display: 'Manual entry',
  current_value: '500.0000',
  current_is_estimated: false,
  rollup_watermark_at: null,
  is_active: true,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
  ...overrides,
});

const renderSection = (
  meters: any[] = [],
  canManage = false,
  onChanged = vi.fn(),
  assetId = 'a1',
) => {
  render(
    <MantineProvider env="test">
      <AssetMetersSection
        meters={meters}
        canManage={canManage}
        assetId={assetId}
        onChanged={onChanged}
      />
    </MantineProvider>,
  );
  return { onChanged };
};

describe('AssetMetersSection', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
  });

  it('renders a meter with value, unit, measured badge, and source', () => {
    renderSection([buildMeter()], false);
    expect(screen.getByText('Water dispensed')).toBeInTheDocument();
    expect(screen.getByTestId('asset-meter-value-m1')).toHaveTextContent('500');
    expect(screen.getByText('(measured)')).toBeInTheDocument();
    expect(screen.getByText('Source: Manual entry')).toBeInTheDocument();
  });

  it('flags an estimated reading', () => {
    renderSection([buildMeter({ current_is_estimated: true })], false);
    expect(screen.getByTestId('asset-meter-estimated-m1')).toHaveTextContent('estimated');
  });

  it('shows the empty state when there are no meters', () => {
    renderSection([], false);
    expect(screen.getByTestId('asset-meters-empty')).toBeInTheDocument();
  });

  it('hides management controls when canManage is false', () => {
    renderSection([buildMeter()], false);
    expect(screen.queryByTestId('asset-meters-add')).not.toBeInTheDocument();
    expect(screen.queryByTestId('asset-meter-record-m1')).not.toBeInTheDocument();
    expect(screen.queryByTestId('asset-meter-adjust-m1')).not.toBeInTheDocument();
  });

  it('records an absolute reading and reloads', async () => {
    const onChanged = vi.fn();
    mockMetersApi.recordReading.mockResolvedValue({
      data: { meter: buildMeter({ current_value: '750.0000' }), reading: {} },
    } as any);

    renderSection([buildMeter()], true, onChanged);
    fireEvent.click(screen.getByTestId('asset-meter-record-m1'));
    fireEvent.change(screen.getByTestId('asset-meter-record-value-m1'), {
      target: { value: '750' },
    });
    fireEvent.click(screen.getByTestId('asset-meter-record-submit-m1'));

    await waitFor(() => {
      expect(mockMetersApi.recordReading).toHaveBeenCalledTimes(1);
    });
    expect(mockMetersApi.recordReading).toHaveBeenCalledWith('m1', {
      value: 750,
      is_absolute: true,
      is_estimated: false,
    });
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it('records a delta reading when mode is "Add"', async () => {
    mockMetersApi.recordReading.mockResolvedValue({ data: { meter: buildMeter(), reading: {} } } as any);

    renderSection([buildMeter()], true);
    fireEvent.click(screen.getByTestId('asset-meter-record-m1'));
    fireEvent.change(screen.getByTestId('asset-meter-record-mode-m1'), {
      target: { value: 'delta' },
    });
    fireEvent.change(screen.getByTestId('asset-meter-record-value-m1'), {
      target: { value: '25' },
    });
    fireEvent.click(screen.getByTestId('asset-meter-record-submit-m1'));

    await waitFor(() => {
      expect(mockMetersApi.recordReading).toHaveBeenCalledWith('m1', {
        value: 25,
        is_absolute: false,
        is_estimated: false,
      });
    });
  });

  it('marks a reading estimated when the box is checked', async () => {
    mockMetersApi.recordReading.mockResolvedValue({ data: { meter: buildMeter(), reading: {} } } as any);

    renderSection([buildMeter()], true);
    fireEvent.click(screen.getByTestId('asset-meter-record-m1'));
    fireEvent.change(screen.getByTestId('asset-meter-record-value-m1'), {
      target: { value: '42' },
    });
    fireEvent.click(screen.getByTestId('asset-meter-record-estimated-m1'));
    fireEvent.click(screen.getByTestId('asset-meter-record-submit-m1'));

    await waitFor(() => {
      expect(mockMetersApi.recordReading).toHaveBeenCalledWith('m1', {
        value: 42,
        is_absolute: true,
        is_estimated: true,
      });
    });
  });

  it('adjusts a meter with a reason and reloads', async () => {
    const onChanged = vi.fn();
    mockMetersApi.adjust.mockResolvedValue({
      data: { meter: buildMeter({ current_value: '90.0000' }), reading: {} },
    } as any);

    renderSection([buildMeter()], true, onChanged);
    fireEvent.click(screen.getByTestId('asset-meter-adjust-m1'));
    fireEvent.change(screen.getByTestId('asset-meter-adjust-target-m1'), {
      target: { value: '90' },
    });
    fireEvent.change(screen.getByTestId('asset-meter-adjust-reason-m1'), {
      target: { value: 'physical recount' },
    });
    fireEvent.click(screen.getByTestId('asset-meter-adjust-submit-m1'));

    await waitFor(() => {
      expect(mockMetersApi.adjust).toHaveBeenCalledWith('m1', {
        target: 90,
        reason: 'physical recount',
      });
    });
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it('blocks an adjustment with a blank reason', async () => {
    renderSection([buildMeter()], true);
    fireEvent.click(screen.getByTestId('asset-meter-adjust-m1'));
    fireEvent.change(screen.getByTestId('asset-meter-adjust-target-m1'), {
      target: { value: '90' },
    });
    // Whitespace passes the HTML `required` attr but fails the trim() guard.
    fireEvent.change(screen.getByTestId('asset-meter-adjust-reason-m1'), {
      target: { value: '   ' },
    });
    fireEvent.click(screen.getByTestId('asset-meter-adjust-submit-m1'));

    const form = screen.getByTestId('asset-meter-adjust-form-m1');
    expect(within(form).getByRole('alert')).toHaveTextContent('reason is required');
    expect(mockMetersApi.adjust).not.toHaveBeenCalled();
  });

  it('adds a new meter with staff controls', async () => {
    const onChanged = vi.fn();
    mockMetersApi.create.mockResolvedValue({ data: buildMeter({ id: 'm2' }) } as any);

    renderSection([], true, onChanged);
    fireEvent.click(screen.getByTestId('asset-meters-add'));
    fireEvent.change(screen.getByTestId('asset-meter-name'), {
      target: { value: 'Spindle runtime' },
    });
    fireEvent.change(screen.getByTestId('asset-meter-type'), {
      target: { value: 'runtime_hours' },
    });
    fireEvent.click(screen.getByTestId('asset-meter-add-submit'));

    await waitFor(() => {
      expect(mockMetersApi.create).toHaveBeenCalledTimes(1);
    });
    expect(mockMetersApi.create).toHaveBeenCalledWith({
      asset: 'a1',
      name: 'Spindle runtime',
      meter_type: 'runtime_hours',
      unit: 'hours',
      source: 'manual',
    });
    expect(onChanged).toHaveBeenCalledTimes(1);
  });
});
