/**
 * ForgeKey ePaper Bind Page
 *
 * Mobile-first staff page that's the second step of the ePaper bind
 * UX: the panel paints a QR pointing here with `?did=<uuid>`, staff
 * scans with their phone, picks an asset, and POSTs the binding to
 * OMS. The panel then renders the asset's PM countdown on its next
 * wake (the firmware handles that on its own via image.png).
 *
 * Staff-only: the bind endpoint requires a JWT. If the visitor isn't
 * authenticated as staff we punt them home rather than showing the
 * picker — the login flow lives at AuthSection and will restore the
 * intended URL via the existing SessionExpiredBanner machinery.
 */
import { Alert, Button, Loader, Paper, Stack, Text, TextInput, Title } from '@mantine/core';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { assetsAPI, forgekeyAPI } from '../services/api';
import { Asset } from '../types';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const SEARCH_DEBOUNCE_MS = 300;
// 50 is enough to show every active asset in the current fleet (~80)
// without paginating, while still keeping the picker scannable on a
// phone. Bump this if the active-asset count grows much past it.
const MAX_SUGGESTIONS = 50;

const ForgeKeyEPaperBindPage: React.FC = () => {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const did = params.get('did') ?? '';

  const isStaff =
    typeof window !== 'undefined' && localStorage.getItem('is_staff') === 'true';
  const isSuperuser =
    typeof window !== 'undefined' && localStorage.getItem('is_superuser') === 'true';

  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [assets, setAssets] = useState<Asset[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [bindError, setBindError] = useState<string | null>(null);
  const [boundAsset, setBoundAsset] = useState<{ id: string; name: string } | null>(null);

  // Debounce so each keystroke doesn't fire a request — same shape as
  // PurchaseOrderFormPage uses for its product search.
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query]);

  const loadAssets = useCallback(async (search: string) => {
    setSearching(true);
    setSearchError(null);
    try {
      const response = await assetsAPI.listAssets({
        search: search || undefined,
        is_active: true,
        page_size: MAX_SUGGESTIONS,
        ordering: 'name',
      });
      setAssets(response.data.results);
    } catch (err) {
      setSearchError(extractErrorMessage(err, 'Failed to load assets.'));
      setAssets([]);
    } finally {
      setSearching(false);
    }
  }, []);

  useEffect(() => {
    // No point loading the picker before we know what panel to bind
    // to, or for visitors who can't POST the bind anyway.
    if (!did || (!isStaff && !isSuperuser)) {
      return;
    }
    loadAssets(debouncedQuery);
  }, [did, debouncedQuery, loadAssets, isStaff, isSuperuser]);

  const handleBind = useCallback(
    async (asset: Asset) => {
      if (!did) {
        setBindError('Missing display_id (did) in URL. Re-scan the panel QR.');
        return;
      }
      setSubmitting(true);
      setBindError(null);
      try {
        const response = await forgekeyAPI.bindEPaper(did, asset.id);
        setBoundAsset({ id: response.data.asset_id, name: response.data.asset_name });
      } catch (err) {
        setBindError(extractErrorMessage(err, 'Failed to bind panel.'));
      } finally {
        setSubmitting(false);
      }
    },
    [did],
  );

  const heading = useMemo(() => {
    if (boundAsset) {
      return 'Panel bound';
    }
    return 'Bind ePaper panel';
  }, [boundAsset]);

  // Non-staff visitors get the same redirect-home treatment as the
  // other ForgeKey admin surfaces; the auth/login route is reachable
  // from there.
  if (!isStaff && !isSuperuser) {
    return <Navigate to="/" replace />;
  }

  if (!did) {
    return (
      <Paper p="lg" m="md" withBorder>
        <Title order={3}>No display_id</Title>
        <Text mt="sm">
          This page expects a <code>?did=</code> query parameter from the
          panel's bind QR. If you arrived here some other way, scan the
          QR on the panel face instead.
        </Text>
      </Paper>
    );
  }

  if (boundAsset) {
    return (
      <Paper p="lg" m="md" withBorder>
        <Stack gap="sm">
          <Title order={2}>{heading}</Title>
          <Text size="lg">
            Panel <code>{did}</code> now shows PM status for:
          </Text>
          <Text size="xl" fw={700}>
            {boundAsset.name}
          </Text>
          <Text c="dimmed" size="sm">
            The panel will pick up the change on its next wake (within
            the configured wake interval). To re-bind to a different
            asset, scan the panel QR again.
          </Text>
          <Button variant="light" onClick={() => navigate('/')}>
            Done
          </Button>
        </Stack>
      </Paper>
    );
  }

  return (
    <Paper p="lg" m="md" withBorder>
      <Stack gap="md">
        <Stack gap={4}>
          <Title order={2}>{heading}</Title>
          <Text size="sm" c="dimmed">
            display_id: <code>{did}</code>
          </Text>
        </Stack>

        <TextInput
          label="Search assets"
          placeholder="Name, serial #, or asset tag (e.g. DMS-…)"
          value={query}
          onChange={(event) => setQuery(event.currentTarget.value)}
          size="md"
          autoFocus
        />

        {bindError && (
          <Alert color="red" variant="light">
            {bindError}
          </Alert>
        )}
        {searchError && (
          <Alert color="red" variant="light">
            {searchError}
          </Alert>
        )}

        {searching ? (
          <Stack align="center" gap="xs" py="md">
            <Loader size="sm" />
            <Text size="sm" c="dimmed">
              Searching…
            </Text>
          </Stack>
        ) : assets.length === 0 ? (
          <Text size="sm" c="dimmed">
            No active assets matched. Try a shorter query, or check the
            asset's <em>Active</em> flag in OMS.
          </Text>
        ) : (
          <Stack gap="xs">
            {assets.map((asset) => (
              <Paper
                key={asset.id}
                p="md"
                withBorder
                radius="md"
                style={{ cursor: submitting ? 'wait' : 'pointer' }}
                onClick={() => !submitting && handleBind(asset)}
                data-testid={`asset-row-${asset.id}`}
              >
                <Stack gap={2}>
                  <Text fw={600}>{asset.name}</Text>
                  <Text size="xs" c="dimmed">
                    {asset.location_name || 'No location'}
                    {asset.asset_tag ? ` · ${asset.asset_tag}` : ''}
                  </Text>
                </Stack>
              </Paper>
            ))}
          </Stack>
        )}
      </Stack>
    </Paper>
  );
};

export default ForgeKeyEPaperBindPage;
