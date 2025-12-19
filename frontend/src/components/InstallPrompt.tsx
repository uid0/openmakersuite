/**
 * Install Prompt Component
 * Prompts users to install the PWA on their device
 */
import { ActionIcon, Button, Card, Group, Stack, Text } from '@mantine/core';
import { IconDownload, IconX } from '@tabler/icons-react';
import React, { useState } from 'react';
import { usePWAInstall } from '../hooks/usePWAInstall';
import '../styles/InstallPrompt.css';

const InstallPrompt: React.FC = () => {
  const { isInstallable, showInstallPrompt, dismiss } = usePWAInstall();
  const [isVisible, setIsVisible] = useState(true);

  if (!isInstallable || !isVisible) {
    return null;
  }

  const handleInstall = async () => {
    const installed = await showInstallPrompt();
    if (installed) {
      setIsVisible(false);
    }
  };

  const handleDismiss = () => {
    setIsVisible(false);
    dismiss();
  };

  return (
    <Card className="install-prompt" shadow="md" padding="md" radius="md" withBorder>
      <Group justify="space-between" align="flex-start" gap="md">
        <Stack gap="xs" style={{ flex: 1 }}>
          <Text fw={600} size="sm">
            Install App
          </Text>
          <Text size="xs" c="dimmed">
            Install Dallas Makerspace Inventory to your home screen for quick access and offline
            use.
          </Text>
        </Stack>
        <Group gap="xs">
          <Button
            size="xs"
            leftSection={<IconDownload size={16} />}
            onClick={handleInstall}
            variant="filled"
          >
            Install
          </Button>
          <ActionIcon variant="subtle" color="gray" onClick={handleDismiss} aria-label="Dismiss">
            <IconX size={16} />
          </ActionIcon>
        </Group>
      </Group>
    </Card>
  );
};

export default InstallPrompt;
