/**
 * Asset power chain at `/facilities/electrical/power-chain` and
 * `/facilities/electrical/power-chain/:assetId`.
 *
 * "What feeds this?" — given an asset, shows the panel + breaker that
 * power it (and the upstream disconnect when present). Lets a
 * maintainer pick an asset by tag, or jump straight to one via the URL
 * (useful when linked from an asset detail page).
 */
import {
  Anchor,
  Badge,
  Box,
  Container,
  Group,
  Paper,
  Select,
  Stack,
  Text,
  Timeline,
} from '@mantine/core';
import {
  IconAlertCircle,
  IconBolt,
  IconCircuitGround,
  IconLayoutGrid,
  IconLockOpen,
  IconRouteAltLeft,
} from '@tabler/icons-react';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import PageHero from '../components/landing/PageHero';
import { AssetPowerChain, assetsAPI, electricalSafetyAPI } from '../services/api';
import { Asset } from '../types';
import '../styles/landing.css';

const HOP_ICON: Record<AssetPowerChain['chain'][number]['kind'], React.ReactNode> = {
  panel: <IconLayoutGrid size={14} />,
  breaker: <IconBolt size={14} />,
  circuit: <IconCircuitGround size={14} />,
  disconnect: <IconLockOpen size={14} />,
};

const HOP_TITLE: Record<AssetPowerChain['chain'][number]['kind'], string> = {
  panel: 'Panel',
  breaker: 'Breaker',
  circuit: 'Circuit',
  disconnect: 'Disconnect',
};

const AssetPowerChainPage: React.FC = () => {
  const { assetId } = useParams<{ assetId?: string }>();
  const navigate = useNavigate();

  const [assets, setAssets] = useState<Asset[]>([]);
  const [chain, setChain] = useState<AssetPowerChain | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Asset picker: lazy-load the assets list once.
  useEffect(() => {
    let cancelled = false;
    assetsAPI
      .listAssets({ page_size: 500, is_active: true })
      .then((response) => {
        if (cancelled) return;
        setAssets(response.data.results ?? []);
      })
      .catch(() => {
        // Picker is a convenience — degrade silently if assets fail to
        // load. The URL-driven flow still works.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const loadChain = useCallback(
    (id: string) => {
      let cancelled = false;
      setLoading(true);
      setError(null);
      electricalSafetyAPI
        .getAssetPowerChain(id)
        .then((response) => {
          if (cancelled) return;
          setChain(response.data);
        })
        .catch((err) => {
          if (cancelled) return;
          setError(err?.response?.data?.detail || 'Failed to load power chain.');
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
      return () => {
        cancelled = true;
      };
    },
    [],
  );

  useEffect(() => {
    if (!assetId) {
      setChain(null);
      setError(null);
      return;
    }
    return loadChain(assetId);
  }, [assetId]);

  const assetOptions = useMemo(
    () =>
      assets.map((asset) => ({
        value: String(asset.id),
        label: asset.asset_tag ? `${asset.asset_tag} — ${asset.name}` : asset.name,
      })),
    [assets],
  );

  return (
    <Box className="landing-surface" data-testid="asset-power-chain">
      <Container size="lg" py="xl">
        <Stack gap="xl">
          <PageHero
            eyebrow="Electrical · Lookup"
            title="Asset power chain"
            description='Pick an asset to see the panel and breaker that feed it, plus any upstream disconnect.'
          />

          <Paper withBorder p="md" radius="md">
            <Select
              label="Asset"
              placeholder="Type to filter by tag or name"
              data={assetOptions}
              value={assetId ?? null}
              onChange={(value) => {
                if (value) {
                  navigate(`/facilities/electrical/power-chain/${value}`);
                } else {
                  navigate('/facilities/electrical/power-chain');
                }
              }}
              searchable
              clearable
              nothingFoundMessage="No matching assets"
              data-testid="asset-power-chain-picker"
            />
          </Paper>

          {error && (
            <Paper withBorder p="md" radius="md" bg="red.0" c="red.9">
              <Group gap="xs">
                <IconAlertCircle size={16} />
                <Text>{error}</Text>
              </Group>
            </Paper>
          )}

          {loading && (
            <Paper withBorder p="xl" radius="md">
              <Text c="dimmed" ta="center">
                Loading…
              </Text>
            </Paper>
          )}

          {!assetId && !loading && (
            <Paper withBorder p="xl" radius="md">
              <Stack gap="xs" align="center">
                <IconRouteAltLeft size={28} stroke={1.6} />
                <Text fw={500}>Pick an asset to start.</Text>
                <Text size="sm" c="dimmed" ta="center" maw={420}>
                  The chain shows the panel and breaker that feed the asset. Useful when planning a
                  shutdown.
                </Text>
              </Stack>
            </Paper>
          )}

          {chain && !loading && (
            <Stack gap="md">
              <Paper withBorder p="md" radius="md" data-testid="asset-summary">
                <Group justify="space-between" wrap="wrap">
                  <Stack gap={2}>
                    <Text component="span" className="landing-eyebrow">
                      Asset
                    </Text>
                    <Text fw={600} size="lg">
                      {chain.asset.name}
                    </Text>
                    <Text size="sm" c="dimmed">
                      {chain.asset.asset_tag}
                    </Text>
                  </Stack>
                  {chain.asset.is_critical && (
                    <Badge color="red" variant="filled" radius="sm">
                      critical
                    </Badge>
                  )}
                </Group>
              </Paper>

              {chain.chain.length === 0 ? (
                <Paper withBorder p="xl" radius="md" bg="yellow.0">
                  <Stack gap="xs" align="center">
                    <IconAlertCircle size={24} />
                    <Text fw={500}>This asset is not wired into a panel yet.</Text>
                    <Text size="sm" c="dimmed" ta="center" maw={420}>
                      Set the feeding breaker on the asset's edit form to populate the chain.
                    </Text>
                  </Stack>
                </Paper>
              ) : (
                <Paper withBorder p="md" radius="md" data-testid="asset-chain-timeline">
                  <Timeline
                    active={chain.chain.length}
                    bulletSize={26}
                    lineWidth={2}
                    color="blue"
                  >
                    {chain.chain.map((hop, index) => (
                      <Timeline.Item
                        key={`${hop.kind}-${hop.id}-${index}`}
                        bullet={HOP_ICON[hop.kind]}
                        title={
                          <Group gap="xs">
                            <Text fw={600}>{HOP_TITLE[hop.kind]}</Text>
                            <Badge size="xs" variant="light" color="gray">
                              {hop.type}
                            </Badge>
                          </Group>
                        }
                      >
                        {hop.kind === 'panel' ? (
                          <Anchor
                            component="a"
                            href={`/facilities/electrical/panels/${hop.id}`}
                            size="sm"
                          >
                            {String(hop.label)}
                          </Anchor>
                        ) : hop.kind === 'breaker' ? (
                          <Anchor
                            component="a"
                            href={`/facilities/electrical/breakers/${hop.id}/trip-impact`}
                            size="sm"
                          >
                            {String(hop.label)}
                          </Anchor>
                        ) : hop.kind === 'circuit' ? (
                          <Anchor
                            component="a"
                            href={`/facilities/electrical/circuits/${hop.id}/load`}
                            size="sm"
                          >
                            {String(hop.label)}
                          </Anchor>
                        ) : (
                          <Text size="sm" c="dimmed">
                            {String(hop.label)}
                          </Text>
                        )}
                      </Timeline.Item>
                    ))}
                  </Timeline>
                </Paper>
              )}
            </Stack>
          )}
        </Stack>
      </Container>
    </Box>
  );
};

export default AssetPowerChainPage;
