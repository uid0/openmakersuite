/**
 * Hook for managing command palette state
 * Note: Keyboard shortcuts (Cmd+K, Escape) are now handled by useKeyboardShortcuts
 */
import { useState } from 'react';

export function useCommandPalette() {
  const [isOpen, setIsOpen] = useState(false);

  return {
    isOpen,
    open: () => setIsOpen(true),
    close: () => setIsOpen(false),
    toggle: () => setIsOpen((prev) => !prev),
  };
}
