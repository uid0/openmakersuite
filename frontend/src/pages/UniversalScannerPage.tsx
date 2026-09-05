/**
 * Universal Scanner (`/scan`).
 *
 * Mobile-first scan-and-act page: auto-focused text input takes
 * Bluetooth-keyboard scanner output (most BT scanners type the payload
 * then press Enter), or open the camera tile for html5-qrcode capture.
 *
 * On submit, POSTs to `/api/scanner/dispatch/` which resolves the
 * payload to an action descriptor, then fires the corresponding
 * side-effect endpoint. Every one of them fires the same way — on the
 * scan, with no further confirmation step — so the list below no longer
 * marks some "auto" and one "member confirmed"; there is no such
 * distinction in `runSideEffect`:
 *
 *   inventory_reorder  → POST /reorders/requests/ for a fixed quantity of 1
 *                        ("Mark for reorder" — a purchaser sizes the order,
 *                        so this deliberately does NOT read the item's own
 *                        reorder quantity; nothing here shows one either)
 *   inventory_receive  → show item + supplier, deep-link to item page
 *                        (real receive flow lives on item detail)
 *   asset_checkin      → POST /inventory/assets/<id>/scan/
 *   location_checkin   → POST /location-checkins/check-ins/
 *   fixture / donation_item → nav link
 *   unknown            → error card
 *
 * Kiosk-mode anonymous — no badge-in (deferred). Stays on /scan after
 * each scan so a shipping clerk scanning a stack of UPCs doesn't have
 * to navigate back between scans.
 */
import {
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  ScrollArea,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { IconCamera, IconCheck, IconX } from '@tabler/icons-react';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import WorkspacePage from '../components/landing/WorkspacePage';
import QRScanner from '../components/QRScanner';
import {
  assetsAPI,
  locationCheckinsAPI,
  reorderAPI,
  scannerAPI,
  ScanDispatchResult,
} from '../services/api';
import { showError, showSuccess } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';

type HistoryEntry = {
  id: string;
  at: Date;
  result: ScanDispatchResult;
  outcome: 'success' | 'error' | 'pending';
  outcomeMessage: string;
};

const HISTORY_LIMIT = 8;

const actionLabel: Record<ScanDispatchResult['action'], string> = {
  inventory_reorder: 'Mark for reorder',
  inventory_receive: 'Receive',
  asset_checkin: 'Asset check-in',
  location_checkin: 'Location check-in',
  fixture: 'Fixture',
  donation_item: 'Donation item',
  unknown: 'Unknown',
};

const actionColor: Record<ScanDispatchResult['action'], string> = {
  inventory_reorder: 'orange',
  inventory_receive: 'teal',
  asset_checkin: 'blue',
  location_checkin: 'grape',
  fixture: 'cyan',
  donation_item: 'lime',
  unknown: 'red',
};

const UniversalScannerPage: React.FC = () => {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [payload, setPayload] = useState('');
  const [busy, setBusy] = useState(false);
  const [cameraOpen, setCameraOpen] = useState(false);
  const [history, setHistory] = useState<HistoryEntry[]>([]);

  const focusInput = useCallback(() => {
    setTimeout(() => inputRef.current?.focus(), 0);
  }, []);

  useEffect(() => {
    focusInput();
  }, [focusInput]);

  const pushHistory = useCallback((entry: HistoryEntry) => {
    setHistory((prev) => [entry, ...prev].slice(0, HISTORY_LIMIT));
  }, []);

  const runSideEffect = useCallback(
    async (result: ScanDispatchResult): Promise<{ outcome: HistoryEntry['outcome']; message: string }> => {
      try {
        switch (result.action) {
          case 'inventory_reorder': {
            if (!result.target_id) return { outcome: 'error', message: 'Missing item id.' };
            await reorderAPI.createRequest({ item: result.target_id, quantity: 1 });
            return { outcome: 'success', message: 'Reorder request created.' };
          }
          case 'asset_checkin': {
            if (!result.target_id) return { outcome: 'error', message: 'Missing asset id.' };
            await assetsAPI.scanAsset(result.target_id);
            return { outcome: 'success', message: 'Asset scan recorded.' };
          }
          case 'location_checkin': {
            if (!result.target_id) return { outcome: 'error', message: 'Missing location id.' };
            await locationCheckinsAPI.create({
              location: Number(result.target_id),
              checkin_type: 'anonymous',
            });
            return { outcome: 'success', message: 'Location check-in recorded.' };
          }
          case 'inventory_receive': {
            // Real receive flow lives on the item detail page. The page
            // just confirms the scan resolved + lets the user open the
            // item to enter a quantity.
            return {
              outcome: 'success',
              message: result.is_package
                ? 'Package UPC matched — open the item to receive.'
                : 'Unit UPC matched — open the item to receive.',
            };
          }
          case 'fixture':
          case 'donation_item': {
            return { outcome: 'success', message: 'Resolved — open the target.' };
          }
          case 'project_storage_stint': {
            // The warden console reads the stint server-side; the
            // dispatcher just confirms the QR matched a real PS-XXX row.
            // The scan history surfaces target_url so the user can
            // click into the warden detail page.
            return { outcome: 'success', message: 'Stint resolved — open to take warden action.' };
          }
          case 'unknown':
          default:
            return { outcome: 'error', message: result.message ?? 'Unknown payload.' };
        }
      } catch (err) {
        return { outcome: 'error', message: extractErrorMessage(err, 'Action failed.') };
      }
    },
    [],
  );

  const handleSubmit = useCallback(
    async (raw: string) => {
      const trimmed = raw.trim();
      if (!trimmed || busy) return;
      setBusy(true);
      try {
        const dispatch = await scannerAPI.dispatch(trimmed);
        const result = dispatch.data;
        const sideEffect = await runSideEffect(result);
        pushHistory({
          id: `${Date.now()}-${Math.random()}`,
          at: new Date(),
          result,
          outcome: sideEffect.outcome,
          outcomeMessage: sideEffect.message,
        });
        if (sideEffect.outcome === 'success') {
          showSuccess(`${actionLabel[result.action]}: ${result.target_name ?? trimmed}`);
        } else {
          showError(sideEffect.message, 'Scan failed');
        }
      } catch (err) {
        const msg = extractErrorMessage(err, 'Could not reach the scanner dispatcher.');
        showError(msg, 'Dispatch failed');
        pushHistory({
          id: `${Date.now()}-${Math.random()}`,
          at: new Date(),
          result: { action: 'unknown', raw_payload: trimmed },
          outcome: 'error',
          outcomeMessage: msg,
        });
      } finally {
        setPayload('');
        setBusy(false);
        focusInput();
      }
    },
    [busy, focusInput, pushHistory, runSideEffect],
  );

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleSubmit(payload);
    }
  };

  const handleCameraSuccess = (decoded: string) => {
    setCameraOpen(false);
    handleSubmit(decoded);
  };

  return (
    <WorkspacePage
      testId="universal-scanner-page"
      hero={{
        eyebrow: 'Scanner',
        title: 'Scan',
        description:
          'Point a Bluetooth scanner at a barcode or QR, or tap the camera tile. The page figures out the right action.',
      }}
      containerSize="md"
    >
      <Stack gap="lg">
        <Card withBorder padding="md" radius="md">
          <Stack gap="sm">
            <TextInput
              ref={inputRef}
              size="xl"
              autoFocus
              autoComplete="off"
              spellCheck={false}
              placeholder="Scan or type a code, then press Enter"
              value={payload}
              onChange={(e) => setPayload(e.currentTarget.value)}
              onKeyDown={onKeyDown}
              disabled={busy}
              data-testid="universal-scanner-input"
            />
            <Group justify="space-between">
              <Button
                variant="default"
                leftSection={<IconCamera size={18} />}
                onClick={() => setCameraOpen(true)}
                disabled={busy}
              >
                Open camera
              </Button>
              <Button onClick={() => handleSubmit(payload)} disabled={busy || !payload.trim()}>
                Submit
              </Button>
            </Group>
          </Stack>
        </Card>

        {cameraOpen && (
          <Card withBorder padding="sm" radius="md">
            <Stack gap="xs">
              <Group justify="space-between">
                <Title order={5}>Camera</Title>
                <Button size="xs" variant="subtle" onClick={() => setCameraOpen(false)}>
                  Close
                </Button>
              </Group>
              <QRScanner
                onScanSuccess={handleCameraSuccess}
                onScanError={(err) => showError(err.message, 'Camera error')}
              />
            </Stack>
          </Card>
        )}

        {history.length > 0 && (
          <Stack gap="sm" data-testid="universal-scanner-history">
            <Title order={4}>Recent scans</Title>
            <ScrollArea.Autosize mah={520}>
              <Stack gap="xs">
                {history.map((entry) => (
                  <Card
                    key={entry.id}
                    withBorder
                    padding="sm"
                    radius="md"
                    style={{
                      borderColor:
                        entry.outcome === 'success'
                          ? 'var(--mantine-color-teal-4)'
                          : 'var(--mantine-color-red-4)',
                    }}
                  >
                    <Stack gap={4}>
                      <Group justify="space-between" wrap="nowrap">
                        <Group gap="xs" wrap="nowrap">
                          {entry.outcome === 'success' ? (
                            <IconCheck color="var(--mantine-color-teal-6)" size={18} />
                          ) : (
                            <IconX color="var(--mantine-color-red-6)" size={18} />
                          )}
                          <Badge color={actionColor[entry.result.action]} variant="light">
                            {actionLabel[entry.result.action]}
                          </Badge>
                          <Text fw={600} lineClamp={1}>
                            {entry.result.target_name ?? entry.result.raw_payload}
                          </Text>
                        </Group>
                        <Text size="xs" c="dimmed">
                          {entry.at.toLocaleTimeString()}
                        </Text>
                      </Group>
                      <Text size="sm" c={entry.outcome === 'success' ? undefined : 'red'}>
                        {entry.outcomeMessage}
                      </Text>
                      {entry.result.target_url && (
                        <Anchor
                          size="sm"
                          component="button"
                          onClick={() => navigate(entry.result.target_url!)}
                        >
                          Open {entry.result.target_type ?? 'target'} →
                        </Anchor>
                      )}
                      {entry.result.supplier_name && (
                        <Text size="xs" c="dimmed">
                          Supplier: {entry.result.supplier_name}
                          {typeof entry.result.current_stock === 'number' &&
                            ` · stock ${entry.result.current_stock}`}
                          {entry.result.is_package && ' · package UPC'}
                        </Text>
                      )}
                    </Stack>
                  </Card>
                ))}
              </Stack>
            </ScrollArea.Autosize>
          </Stack>
        )}
      </Stack>
    </WorkspacePage>
  );
};

export default UniversalScannerPage;
