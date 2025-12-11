/**
 * Dark Mode Toggle Component
 * Provides a button to toggle between light and dark themes
 */
import { ActionIcon, Tooltip } from '@mantine/core';
import { IconMoon, IconSun } from '@tabler/icons-react';
import React from 'react';
import { useTheme } from '../contexts/ThemeContext';

interface DarkModeToggleProps {
  /** Optional size for the icon */
  size?: number | string;
  /** Optional variant for the ActionIcon */
  variant?: string;
}

export const DarkModeToggle: React.FC<DarkModeToggleProps> = ({ 
  size = 20,
  variant = 'subtle'
}) => {
  const { effectiveColorScheme, toggleColorScheme } = useTheme();
  const isDark = effectiveColorScheme === 'dark';

  return (
    <Tooltip label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}>
      <ActionIcon
        variant={variant}
        onClick={toggleColorScheme}
        aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
        size="lg"
      >
        {isDark ? <IconSun size={size} /> : <IconMoon size={size} />}
      </ActionIcon>
    </Tooltip>
  );
};
