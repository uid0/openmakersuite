/**
 * ForgeKey Certificates — staff PKI visibility + CA rotation.
 *
 * Read-only view of the internal CA(s) and issued device (mTLS) certificates.
 * Staff can rotate the root CA (mints a fresh self-signed root; devices must be
 * re-flashed / rebuilt to trust it). Issuance + revocation stay in the
 * enrollment flow / Django admin.
 */
import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Modal,
  Paper,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import React, { useCallback, useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import WorkspacePage from '../components/landing/WorkspacePage';
import {
  ForgeKeyCertificateAuthority,
  ForgeKeyDeviceCertificate,
  forgekeyAPI,
} from '../services/api';
import { showError, showSuccess } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const asList = <T,>(data: { results?: T[] } | T[]): T[] =>
  Array.isArray(data) ? data : (data.results ?? []);

const CERT_STATUS_COLORS: Record<string, string> = {
  active: 'green',
  expired: 'yellow',
  revoked: 'red',
};

const fmtDate = (iso: string | null): string => (iso ? new Date(iso).toLocaleDateString() : '—');

const ForgeKeyCertificatesPage: React.FC = () => {
  const isStaff = typeof window !== 'undefined' && localStorage.getItem('is_staff') === 'true';
  const isSuperuser =
    typeof window !== 'undefined' && localStorage.getItem('is_superuser') === 'true';

  const [cas, setCas] = useState<ForgeKeyCertificateAuthority[]>([]);
  const [certs, setCerts] = useState<ForgeKeyDeviceCertificate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [confirmRotate, setConfirmRotate] = useState(false);
  const [rotating, setRotating] = useState(false);

  const load = useCallback(async () => {
    try {
      const [caRes, certRes] = await Promise.all([
        forgekeyAPI.listCertificateAuthorities(),
        forgekeyAPI.listDeviceCertificates(),
      ]);
      setCas(asList(caRes.data));
      setCerts(asList(certRes.data));
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to load certificates.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isStaff && !isSuperuser) return;
    load();
  }, [isStaff, isSuperuser, load]);

  if (!isStaff && !isSuperuser) {
    return <Navigate to="/" replace />;
  }

  const handleRotate = async () => {
    setRotating(true);
    try {
      await forgekeyAPI.rotateCA();
      await load();
      setConfirmRotate(false);
      showSuccess('Root CA rotated — re-flash / rebuild firmware to trust the new root.');
    } catch (err) {
      showError(extractErrorMessage(err, 'Failed to rotate the CA.'));
    } finally {
      setRotating(false);
    }
  };

  const activeCa = cas.find((c) => c.is_active) || null;
  const retired = cas.filter((c) => !c.is_active);

  return (
    <WorkspacePage
      testId="forgekey-certificates-page"
      hero={{
        eyebrow: 'Facilities · ForgeKey',
        title: 'Certificates (PKI)',
        description:
          'The internal CA + issued device certificates. Read-only; rotate the root CA here.',
      }}
    >
      {error && (
        <Alert color="red" variant="light" data-testid="certs-error">
          {error}
        </Alert>
      )}

      {loading ? (
        <Group justify="center" p="xl">
          <Loader />
        </Group>
      ) : (
        <Stack gap="lg">
          <Card withBorder p="md" radius="md" data-testid="active-ca">
            <Group justify="space-between" mb="xs">
              <Title order={5}>Root certificate authority</Title>
              <Button
                color="red"
                variant="light"
                onClick={() => setConfirmRotate(true)}
                data-testid="rotate-ca"
              >
                Rotate CA…
              </Button>
            </Group>
            {activeCa ? (
              <Stack gap={4}>
                <Text size="sm">
                  <Text span fw={600}>
                    CN:
                  </Text>{' '}
                  {activeCa.common_name || activeCa.name}
                </Text>
                <Text size="xs" c="dimmed" ff="monospace">
                  {activeCa.fingerprint_sha256}
                </Text>
                <Text size="sm">
                  Valid {fmtDate(activeCa.not_before)} → {fmtDate(activeCa.not_after)}
                </Text>
                <Text size="sm" c="dimmed">
                  {activeCa.active_cert_count ?? 0} active · {activeCa.revoked_cert_count ?? 0}{' '}
                  revoked device certs
                </Text>
              </Stack>
            ) : (
              <Text c="dimmed" data-testid="no-ca">
                No active CA. Bootstrap one with <code>manage.py forgekey_ca init</code>.
              </Text>
            )}
            {retired.length > 0 && (
              <Text size="xs" c="dimmed" mt="sm">
                {retired.length} retired CA(s) in history.
              </Text>
            )}
          </Card>

          <Paper withBorder>
            <Group p="sm" justify="space-between">
              <Title order={5}>Device certificates</Title>
              <Text size="xs" c="dimmed">
                Issued via enrollment · revoke in admin
              </Text>
            </Group>
            {certs.length === 0 ? (
              <Text c="dimmed" p="md" data-testid="certs-empty">
                No device certificates issued yet.
              </Text>
            ) : (
              <Table.ScrollContainer minWidth={720}>
                <Table highlightOnHover>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Device</Table.Th>
                      <Table.Th>Serial</Table.Th>
                      <Table.Th>Expires</Table.Th>
                      <Table.Th>Status</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {certs.map((c) => (
                      <Table.Tr key={c.id} data-testid={`cert-${c.id}`}>
                        <Table.Td>
                          <Text size="sm">{c.device_chip_id || '—'}</Text>
                          <Text size="xs" c="dimmed" ff="monospace" lineClamp={1}>
                            {c.fingerprint_sha256}
                          </Text>
                        </Table.Td>
                        <Table.Td>
                          <Text size="sm" ff="monospace">
                            {c.serial}
                          </Text>
                        </Table.Td>
                        <Table.Td>
                          <Text size="sm" c="dimmed">
                            {fmtDate(c.not_after)}
                          </Text>
                        </Table.Td>
                        <Table.Td>
                          <Badge color={CERT_STATUS_COLORS[c.status] || 'gray'}>{c.status}</Badge>
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            )}
          </Paper>
        </Stack>
      )}

      <Modal
        opened={confirmRotate}
        onClose={() => setConfirmRotate(false)}
        title="Rotate the root CA?"
      >
        <Stack gap="sm">
          <Text size="sm">
            This mints a <strong>brand-new self-signed root CA</strong> and retires the current one.
            Devices won&rsquo;t trust the new root until they&rsquo;re <strong>re-flashed</strong>{' '}
            (or rebuilt via the firmware pipeline with the new CA embedded). This can&rsquo;t be
            undone.
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setConfirmRotate(false)}>
              Cancel
            </Button>
            <Button
              color="red"
              loading={rotating}
              onClick={handleRotate}
              data-testid="rotate-ca-confirm"
            >
              Rotate root CA
            </Button>
          </Group>
        </Stack>
      </Modal>
    </WorkspacePage>
  );
};

export default ForgeKeyCertificatesPage;
