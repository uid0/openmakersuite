/**
 * Asset power chain at `/facilities/electrical/power-chain` and
 * `/facilities/electrical/power-chain/:assetId`.
 *
 * "What feeds this?" — given an asset, walks back through cables,
 * outlets, circuits, and breakers to the panel. Lets a maintainer pick
 * an asset by tag from the asset list, or jump straight to one via the
 * URL (useful when linked from an asset detail page).
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
  IconCpu,
  IconLayoutGrid,
  IconPlugConnected,
  IconRouteAltLeft,
  IconSitemap,
} from '@tabler/icons-react';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import PageHero from '../components/landing/PageHero';
import AssetPowerChainEditor from './AssetPowerChainEditor';
import { AssetPowerChain, assetsAPI, electricalSafetyAPI } from '../services/api';
import { Asset } from '../types';
import '../styles/landing.css';

const HOP_ICON: Record<AssetPowerChain['chain'][number]['kind'], React.ReactNode> = {
  panel: <IconLayoutGrid size={14} />,
  breaker: <IconBolt size={14} />,
  circuit: <IconCircuitGround size={14} />,
  outlet: <IconPlugConnected size={14} />,
  cable: <IconRouteAltLeft size={14} />,
  port: <IconCpu size={14} />,
};

const HOP_TITLE: Record<AssetPowerChain['chain'][number]['kind'], string> = {
  panel: 'Panel',
  breaker: 'Breaker',
  circuit: 'Circuit',
  outlet: 'Outlet',
  cable: 'Cable',
  port: 'Port',
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
            description='Pick an asset to walk back through cables, outlets, circuits, and breakers to its panel.'
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
                  The chain shows every hop from the asset's power port back to the panel that
                  feeds it. Useful when planning a shutdown.
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
                    <Text fw={500}>This asset is not wired into a power topology.</Text>
                    <Text size="sm" c="dimmed" ta="center" maw={420}>
                      Add a port below and wire it to an outlet to see the chain.
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

              <AssetPowerChainEditor
                assetId={chain.asset.id}
                isStaff={localStorage.getItem('is_staff') === 'true' || localStorage.getItem('is_superuser') === 'true'}
                onChange={() => loadChain(chain.asset.id)}
              />
            </Stack>
          )}
        </Stack>
      </Container>
    </Box>
  );
};

export default AssetPowerChainPage;
