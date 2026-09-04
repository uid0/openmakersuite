/**
 * The supplier lead-time surface names what its lateness is measured against.
 *
 * `variance_days`, `was_late`, `average_variance` and `on_time_percentage` all
 * score the supplier link's STANDING QUOTED lead time — never the delivery date
 * the operator confirmed on the order. Those are two promises, and a vendor that
 * quotes 3 days, has the order confirmed for day 10 and delivers on day 10
 * reaches this component as `expected_delivery_date == actual_delivery_date`
 * beside `variance_days: 7, was_late: true`.
 *
 * The captain chases vendors off this screen, so a card reading a bare
 * "On-Time Percentage" tells them a supplier missed agreed dates it in fact
 * met. These tests fail if any label goes back to a bare lateness.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import LeadTimeChart, {
  LeadTimeTooltip,
  confirmedDatePhrase,
} from '../../components/LeadTimeChart';

/** Quote 3, confirm day 10, deliver day 10: over the quote, on the agreed day. */
const keptPromiseBrokenQuote = {
  average_lead_time: 10,
  min_lead_time: 10,
  max_lead_time: 10,
  average_variance: 7,
  total_orders: 1,
  on_time_percentage: 0,
  variance_measured_against: 'quoted_lead_time',
  recent_logs: [
    {
      item_name: 'Filament',
      order_date: '2026-03-01T00:00:00Z',
      expected_delivery_date: '2026-03-11',
      actual_delivery_date: '2026-03-11',
      estimated_lead_time_days: 3,
      actual_lead_time_days: 10,
      variance_days: 7,
      was_late: true,
      met_confirmed_date: true,
    },
  ],
};

const renderChart = (analytics = keptPromiseBrokenQuote) =>
  render(
    <MantineProvider>
      <LeadTimeChart analytics={analytics} />
    </MantineProvider>
  );

describe('lead-time labels name the yardstick', () => {
  it('never offers a bare "On-Time Percentage" card', () => {
    renderChart();

    expect(screen.getByText('Within Quoted Lead Time')).toBeInTheDocument();
    expect(screen.queryByText('On-Time Percentage')).not.toBeInTheDocument();
  });

  it('never offers a bare "Average Variance" card', () => {
    renderChart();

    expect(screen.getByText('Avg Variance vs. Quoted Lead Time')).toBeInTheDocument();
    expect(screen.queryByText('Average Variance')).not.toBeInTheDocument();
  });

  it('says on the surface that the quote, not the agreed date, is the yardstick', () => {
    renderChart();

    expect(
      screen.getByText(/not against\s+the delivery dates confirmed on the orders/i)
    ).toBeInTheDocument();
  });

  it('titles the chart by what it compares rather than a bare "Lead Time"', () => {
    renderChart();

    expect(screen.getByText('Quoted vs. Actual Lead Time (Recent Orders)')).toBeInTheDocument();
  });
});

describe('confirmedDatePhrase', () => {
  it('reports a met confirmed date', () => {
    expect(confirmedDatePhrase(true)).toBe('Met the confirmed delivery date');
  });

  it('reports a missed confirmed date', () => {
    expect(confirmedDatePhrase(false)).toBe('Missed the confirmed delivery date');
  });

  it('says nothing at all when no date was confirmed on the order', () => {
    // Not "missed": there is no agreed date to have missed, and inventing one
    // would assert the second promise the whole fix exists to stop asserting.
    expect(confirmedDatePhrase(null)).toBeNull();
    expect(confirmedDatePhrase(undefined)).toBeNull();
  });
});

describe('LeadTimeTooltip', () => {
  const row = {
    name: 'Filament',
    estimated: 3,
    actual: 10,
    variance: 7,
    date: '01/03/2026',
    metConfirmedDate: true as boolean | null | undefined,
  };

  const renderTooltip = (payloadRow: typeof row) =>
    render(
      <MantineProvider>
        <LeadTimeTooltip active payload={[{ payload: payloadRow }]} />
      </MantineProvider>
    );

  it('does not present a kept confirmed date as simply late', () => {
    renderTooltip(row);

    // The vendor really did break its quote — that number is unchanged.
    expect(screen.getByText(/\+7 days vs\. quoted lead time/i)).toBeInTheDocument();
    // And the promise it kept is right there beside it.
    expect(screen.getByText('Met the confirmed delivery date')).toBeInTheDocument();
    expect(screen.queryByText(/7 days late/i)).not.toBeInTheDocument();
  });

  it('omits the confirmed-date line when the order confirmed no date', () => {
    renderTooltip({ ...row, metConfirmedDate: null });

    expect(screen.getByText(/\+7 days vs\. quoted lead time/i)).toBeInTheDocument();
    expect(screen.queryByText(/confirmed delivery date/i)).not.toBeInTheDocument();
  });

  it('renders nothing when not hovering a bar', () => {
    render(
      <MantineProvider>
        <LeadTimeTooltip active={false} payload={[{ payload: row }]} />
      </MantineProvider>
    );

    // Not `toBeEmptyDOMElement`: MantineProvider always injects its <style>
    // tags, so the container is never literally empty. What matters is that no
    // row content is drawn.
    expect(screen.queryByText('Filament')).not.toBeInTheDocument();
    expect(screen.queryByText(/vs\. quoted lead time/i)).not.toBeInTheDocument();
  });
});
