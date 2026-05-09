import {
  Alert,
  Box,
  Card,
  Container,
  Grid,
  Group,
  Loader,
  SimpleGrid,
  Stack,
  Text,
} from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { BudgetGauge } from '../components/analytics/BudgetGauge';
import { CategorySpendChart } from '../components/analytics/CategorySpendChart';
import { EquipmentStatusGrid } from '../components/analytics/EquipmentStatusGrid';
import { MaintenanceForecastList } from '../components/analytics/MaintenanceForecastList';
import { ShareLinkButton } from '../components/analytics/ShareLinkButton';
import { TopUsersTable } from '../components/analytics/TopUsersTable';
import { VolumeTrendChart } from '../components/analytics/VolumeTrendChart';
import PageHero from '../components/landing/PageHero';
import '../styles/landing.css';
import { pulseAPI, AnalyticsPulseResponse } from '../services/api';
import { extractErrorMessage } from '../utils/extractErrorMessage';

interface AnalyticsDashboardPageProps {
  /**
   * When true, the page renders the board-shareable view: no share button,
   * a notice that this is a read-only snapshot, and the token from the URL
   * is forwarded on the API call. Wired by the parent Route.
   */
  shared?: boolean;
}

const fmtMoney = (raw: string) => {
  const n = Number(raw);
  if (!isFinite(n)) return raw;
  return n.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });
};

const SummaryStat: React.FC<{ label: string; value: string; hint?: string; emphasis?: boolean }> = ({
  label,
  value,
  hint,
  emphasis,
}) => (
  <Card withBorder p="md" radius="md">
    <Text size="sm" c="dimmed" fw={500}>{label}</Text>
    <Text size="1.6rem" fw={700} c={emphasis ? 'green.7' : undefined}>{value}</Text>
    {hint && <Text size="xs" c="dimmed" mt={2}>{hint}</Text>}
  </Card>
);

export const AnalyticsDashboardPage: React.FC<AnalyticsDashboardPageProps> = ({ shared = false }) => {
  const [searchParams] = useSearchParams();
  const [data, setData] = useState<AnalyticsPulseResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const token = shared ? searchParams.get('token') ?? undefined : undefined;
    setLoading(true);
    setError(null);
    pulseAPI
      .getPulse({ token })
      .then((resp) => {
        if (!cancelled) {
          setData(resp.data);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(extractErrorMessage(err, 'Could not load analytics.'));
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [shared, searchParams]);

  if (loading) {
    return (
      <Box className="landing-surface">
        <Container size="xl" py="xl">
          <Group justify="center"><Loader /></Group>
        </Container>
      </Box>
    );
  }

  if (error || !data) {
    return (
      <Box className="landing-surface">
        <Container size="xl" py="xl">
          <Alert color="red" icon={<IconAlertTriangle size={16} />} title="Could not load analytics">
            {error ?? 'Unknown error'}
          </Alert>
        </Container>
      </Box>
    );
  }

  const { summary } = data;

  return (
    <Box className="landing-surface" data-testid="analytics-dashboard-page">
      <Container size="xl" py="lg">
        <PageHero
          eyebrow="Analytics pulse"
          title="Makerspace pulse"
          description={`${summary.period_start} → ${summary.period_end}${shared ? ' · shared link (read-only)' : ''}`}
          action={!shared ? <ShareLinkButton baseUrl={window.location.origin} /> : undefined}
        />

        <Stack gap="lg">
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing="md">
          <SummaryStat
            label="Net value created"
            value={fmtMoney(summary.internal_net_value)}
            hint="External estimate − internal cost"
            emphasis
          />
          <SummaryStat
            label="External spend (closed)"
            value={fmtMoney(summary.external_actual_cost)}
            hint={`${summary.external_closed_count} third-party WO${summary.external_closed_count === 1 ? '' : 's'}`}
          />
          <SummaryStat
            label="Internal WOs completed"
            value={String(summary.internal_completed_count)}
          />
          <SummaryStat
            label="Total to makerspace"
            value={fmtMoney(summary.total_value_to_makerspace)}
            hint="Net internal − external spent"
          />
        </SimpleGrid>

        <Grid>
          <Grid.Col span={{ base: 12, lg: 4 }}>
            <BudgetGauge budget={data.monthly_budget} externalActualCost={summary.external_actual_cost} />
          </Grid.Col>
          <Grid.Col span={{ base: 12, lg: 8 }}>
            <VolumeTrendChart data={data.wo_volume_trend} />
          </Grid.Col>
        </Grid>

        <CategorySpendChart data={data.category_spend} />

        <Grid>
          <Grid.Col span={{ base: 12, lg: 6 }}>
            <TopUsersTable users={data.top_users} />
          </Grid.Col>
          <Grid.Col span={{ base: 12, lg: 6 }}>
            <MaintenanceForecastList entries={data.maintenance_forecast} />
          </Grid.Col>
        </Grid>

        <EquipmentStatusGrid rows={data.utilization} />
        </Stack>
      </Container>
    </Box>
  );
};

export default AnalyticsDashboardPage;
