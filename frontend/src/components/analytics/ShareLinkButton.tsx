import { Button, CopyButton, Group, Modal, NumberInput, Stack, Text, TextInput, Tooltip } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { IconCheck, IconCopy, IconShare } from '@tabler/icons-react';
import React, { useState } from 'react';

import { pulseAPI } from '../../services/api';
import { extractErrorMessage } from '../../utils/extractErrorMessage';

interface ShareLinkButtonProps {
  /** Origin URL to compose into the share link, e.g. window.location.origin. */
  baseUrl: string;
}

export const ShareLinkButton: React.FC<ShareLinkButtonProps> = ({ baseUrl }) => {
  const [opened, { open, close }] = useDisclosure(false);
  const [ttlDays, setTtlDays] = useState<number | string>(30);
  const [submitting, setSubmitting] = useState(false);
  const [shareUrl, setShareUrl] = useState<string | null>(null);

  const reset = () => {
    setTtlDays(30);
    setShareUrl(null);
  };

  const onClose = () => {
    close();
    reset();
  };

  const onMint = async () => {
    setSubmitting(true);
    try {
      const ttl = typeof ttlDays === 'number' ? ttlDays : parseInt(String(ttlDays), 10);
      const resp = await pulseAPI.createShare(ttl);
      const url = `${baseUrl}/analytics/shared?token=${encodeURIComponent(resp.data.token)}`;
      setShareUrl(url);
    } catch (err) {
      notifications.show({
        color: 'red',
        title: 'Could not create share link',
        message: extractErrorMessage(err, 'Unknown error'),
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <Button leftSection={<IconShare size={16} />} variant="light" onClick={open}>
        Share with board
      </Button>
      <Modal opened={opened} onClose={onClose} title="Share analytics dashboard" size="md">
        <Stack>
          <Text size="sm">
            Generates a signed URL that anyone can use to view the dashboard read-only,
            even without an OMS account. The URL expires after the chosen number of days.
          </Text>
          {shareUrl ? (
            <Stack gap="xs">
              <TextInput
                label="Shareable URL"
                value={shareUrl}
                readOnly
                onFocus={(e) => e.currentTarget.select()}
                rightSection={
                  <CopyButton value={shareUrl} timeout={2000}>
                    {({ copied, copy }) => (
                      <Tooltip label={copied ? 'Copied' : 'Copy'} withArrow>
                        <Button size="xs" variant="subtle" onClick={copy} aria-label="Copy share URL">
                          {copied ? <IconCheck size={16} /> : <IconCopy size={16} />}
                        </Button>
                      </Tooltip>
                    )}
                  </CopyButton>
                }
                rightSectionWidth={48}
              />
              <Text size="xs" c="dimmed">
                Treat this URL as sensitive. Anyone with the link can view the dashboard until it expires.
                To revoke a leaked link before expiry, rotate <code>ANALYTICS_SHARE_SALT</code> in backend settings.
              </Text>
            </Stack>
          ) : (
            <NumberInput
              label="Expires after"
              description="Days from now (1–365)"
              value={ttlDays}
              onChange={setTtlDays}
              min={1}
              max={365}
              data-testid="share-ttl-input"
            />
          )}
          <Group justify="flex-end" mt="sm">
            <Button variant="subtle" onClick={onClose}>Close</Button>
            {!shareUrl && (
              <Button onClick={onMint} loading={submitting} data-testid="share-mint-button">
                Generate URL
              </Button>
            )}
          </Group>
        </Stack>
      </Modal>
    </>
  );
};
