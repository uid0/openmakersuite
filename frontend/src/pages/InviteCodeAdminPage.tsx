/**
 * Invite Code admin (`/admin/invite-codes`).
 *
 * Staff-only surface to mint + revoke single-use invite codes that
 * outside users (incoming board members, committee members) redeem on
 * `/invite/<code>` to self-create their OMS account. The redeem flow
 * adds them to whichever Django Groups are attached to the code; door
 * access via ForgeKey follows from group membership.
 */
import {
  Anchor,
  Badge,
  Box,
  Button,
  Card,
  CopyButton,
  Group,
  Modal,
  MultiSelect,
  Stack,
  Table,
  Text,
  Textarea,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { DateInput } from '@mantine/dates';
import { useForm } from '@mantine/form';
import { IconCopy, IconPlus, IconTicket } from '@tabler/icons-react';
import dayjs from 'dayjs';
import React, { useCallback, useEffect, useMemo, useState } from 'react';

import WorkspacePage from '../components/landing/WorkspacePage';
import {
  inviteCodesAdminAPI,
  InviteCodeListEntry,
  sigAPI,
} from '../services/api';
import { SIG } from '../types';
import { showError, showSuccess } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const STATE_COLOR: Record<InviteCodeListEntry['state'], string> = {
  open: 'green',
  redeemed: 'blue',
  revoked: 'gray',
  expired: 'red',
};

function inviteUrl(code: string): string {
  return `${window.location.origin}/invite/${code}`;
}

const InviteCodeAdminPage: React.FC = () => {
  const [codes, setCodes] = useState<InviteCodeListEntry[]>([]);
  const [groups, setGroups] = useState<SIG[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [lastMinted, setLastMinted] = useState<InviteCodeListEntry | null>(null);

  const form = useForm({
    initialValues: {
      intended_label: '',
      intended_groups: [] as string[],
      expires_at: dayjs().add(14, 'day').toDate() as Date | null,
      notes: '',
    },
    validate: {
      intended_label: (v) =>
        v.trim().length === 0 ? 'Label is required' : null,
      expires_at: (v) => (!v ? 'Pick an expiration date' : null),
    },
  });

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [codesRes, groupsRes] = await Promise.all([
        inviteCodesAdminAPI.list(),
        sigAPI.listMySIGs(),
      ]);
      setCodes(codesRes.data?.results ?? []);
      setGroups(groupsRes.data?.results ?? []);
    } catch (err) {
      showError(
        extractErrorMessage(err, 'Failed to load invite codes.'),
        'Could not load invite codes',
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const groupOptions = useMemo(
    () => groups.map((g) => ({ value: String(g.id), label: g.name })),
    [groups],
  );

  const handleMint = form.onSubmit(async (values) => {
    if (!values.expires_at) return;
    setCreating(true);
    try {
      const expires_at = dayjs(values.expires_at).endOf('day').toISOString();
      const response = await inviteCodesAdminAPI.create({
        intended_label: values.intended_label.trim(),
        intended_groups: values.intended_groups.map((id) => Number(id)),
        notes: values.notes,
        expires_at,
      });
      setLastMinted(response.data);
      setModalOpen(false);
      form.reset();
      await loadData();
      showSuccess(`Code minted for ${response.data.intended_label}`);
    } catch (err) {
      showError(
        extractErrorMessage(err, 'Failed to mint invite code.'),
        'Mint failed',
      );
    } finally {
      setCreating(false);
    }
  });

  const handleRevoke = async (entry: InviteCodeListEntry) => {
    if (!window.confirm(`Revoke "${entry.intended_label}"? This cannot be undone.`)) {
      return;
    }
    try {
      await inviteCodesAdminAPI.revoke(entry.id);
      await loadData();
      showSuccess('Code revoked.');
    } catch (err) {
      showError(extractErrorMessage(err, 'Failed to revoke code.'), 'Revoke failed');
    }
  };

  return (
    <WorkspacePage
      testId="invite-code-admin-page"
      hero={{
        eyebrow: 'Admin · Onboarding',
        title: 'Invite codes',
        description:
          'Mint single-use codes for board / committee onboarding. Each code creates one OMS account on redeem and adds the new user to the groups you specify here.',
        action: (
          <Button
            leftSection={<IconPlus size={16} />}
            onClick={() => {
              setLastMinted(null);
              setModalOpen(true);
            }}
          >
            Mint new code
          </Button>
        ),
      }}
    >
      <Stack gap="lg">
        {lastMinted && (
          <Card withBorder padding="md" radius="md" data-testid="invite-code-just-minted">
            <Stack gap="xs">
              <Group gap="xs">
                <IconTicket size={20} />
                <Title order={4}>New code: {lastMinted.intended_label}</Title>
                <Badge color="green" variant="light">
                  open
                </Badge>
              </Group>
              <Text size="sm" c="dimmed">
                Send this link to the invitee. It expires{' '}
                {dayjs(lastMinted.expires_at).format('MMM D, YYYY')} and works once.
              </Text>
              <Group gap="xs" wrap="nowrap">
                <TextInput
                  readOnly
                  value={inviteUrl(lastMinted.code)}
                  style={{ flex: 1 }}
                  size="sm"
                />
                <CopyButton value={inviteUrl(lastMinted.code)}>
                  {({ copied, copy }) => (
                    <Button
                      leftSection={<IconCopy size={14} />}
                      onClick={copy}
                      variant={copied ? 'filled' : 'default'}
                      size="sm"
                    >
                      {copied ? 'Copied' : 'Copy link'}
                    </Button>
                  )}
                </CopyButton>
              </Group>
            </Stack>
          </Card>
        )}

        <Card withBorder padding={0} radius="md">
          {loading ? (
            <Box p="md">
              <Text c="dimmed">Loading invite codes…</Text>
            </Box>
          ) : codes.length === 0 ? (
            <Box p="md">
              <Text c="dimmed">
                No invite codes yet. Mint one above to onboard your first
                board / committee member.
              </Text>
            </Box>
          ) : (
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Label</Table.Th>
                  <Table.Th>Groups</Table.Th>
                  <Table.Th>State</Table.Th>
                  <Table.Th>Expires</Table.Th>
                  <Table.Th>Created by</Table.Th>
                  <Table.Th>Redeemed by</Table.Th>
                  <Table.Th>Actions</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {codes.map((entry) => (
                  <Table.Tr key={entry.id} data-testid={`invite-code-row-${entry.id}`}>
                    <Table.Td>
                      <Text fw={500}>{entry.intended_label}</Text>
                      {entry.notes && (
                        <Text size="xs" c="dimmed">
                          {entry.notes}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      {entry.intended_group_names.length === 0 ? (
                        <Text size="xs" c="dimmed">
                          —
                        </Text>
                      ) : (
                        <Group gap={4}>
                          {entry.intended_group_names.map((name) => (
                            <Badge key={name} variant="light" size="sm">
                              {name}
                            </Badge>
                          ))}
                        </Group>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Badge color={STATE_COLOR[entry.state]} variant="light">
                        {entry.state}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm">
                        {dayjs(entry.expires_at).format('MMM D, YYYY')}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm">{entry.created_by_username ?? '—'}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm">{entry.redeemed_by_username ?? '—'}</Text>
                      {entry.redeemed_at && (
                        <Text size="xs" c="dimmed">
                          {dayjs(entry.redeemed_at).format('MMM D HH:mm')}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      {entry.state === 'open' ? (
                        <Group gap="xs" wrap="nowrap">
                          <CopyButton value={inviteUrl(entry.code)}>
                            {({ copied, copy }) => (
                              <Tooltip
                                label={copied ? 'Copied!' : 'Copy redemption link'}
                              >
                                <Button
                                  size="xs"
                                  variant="default"
                                  onClick={copy}
                                  leftSection={<IconCopy size={12} />}
                                >
                                  Link
                                </Button>
                              </Tooltip>
                            )}
                          </CopyButton>
                          <Button
                            size="xs"
                            variant="subtle"
                            color="red"
                            onClick={() => handleRevoke(entry)}
                          >
                            Revoke
                          </Button>
                        </Group>
                      ) : (
                        <Text size="xs" c="dimmed">
                          —
                        </Text>
                      )}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </Card>
      </Stack>

      <Modal opened={modalOpen} onClose={() => setModalOpen(false)} title="Mint invite code" size="md">
        <form onSubmit={handleMint}>
          <Stack gap="sm">
            <TextInput
              label="Label"
              placeholder="Board Member — Q3 2026"
              description="Shown to the invitee on the redemption page."
              required
              {...form.getInputProps('intended_label')}
            />
            <MultiSelect
              label="Groups"
              placeholder="Pick the groups the new user will join"
              data={groupOptions}
              searchable
              description="ForgeKey door access follows from group membership."
              {...form.getInputProps('intended_groups')}
            />
            <DateInput
              label="Expires"
              required
              minDate={dayjs().toDate()}
              {...form.getInputProps('expires_at')}
            />
            <Textarea
              label="Notes (staff-only)"
              placeholder="For Jane Doe — incoming Board chair as of August"
              autosize
              minRows={2}
              {...form.getInputProps('notes')}
            />
            <Group justify="flex-end" gap="sm">
              <Button variant="default" onClick={() => setModalOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" loading={creating}>
                Mint code
              </Button>
            </Group>
          </Stack>
        </form>
      </Modal>
    </WorkspacePage>
  );
};

export default InviteCodeAdminPage;
