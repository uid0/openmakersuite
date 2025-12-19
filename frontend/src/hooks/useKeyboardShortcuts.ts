/**
 * Hook for managing global keyboard shortcuts
 * Handles Cmd+N (new item), Escape (close modals), and integrates with command palette
 */
import { useEffect } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useCloseTopModal } from './useModalManager';

/**
 * Get the appropriate "new item" route based on current pathname
 */
const getNewItemRoute = (pathname: string): string => {
  if (pathname.startsWith('/assets')) return '/assets/new';
  if (pathname.startsWith('/purchasing')) return '/purchasing/orders/new';
  if (pathname.startsWith('/inventory/suppliers')) return '/inventory/suppliers/new';
  if (pathname.startsWith('/inventory/locations')) return '/inventory/locations/new';
  if (pathname.startsWith('/inventory')) return '/inventory/items/new';
  return '/inventory/items/new'; // Default
};

/**
 * Hook to manage keyboard shortcuts globally
 * Should be used at the app level (WorkspaceLayout)
 */
export function useKeyboardShortcuts(commandPaletteOpen: boolean, onCommandPaletteToggle: () => void): void {
  const navigate = useNavigate();
  const location = useLocation();
  const closeTopModal = useCloseTopModal();

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      // Don't handle shortcuts when user is typing in an input, textarea, or contenteditable
      const target = event.target as HTMLElement;
      const isInput = target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable;
      
      // Allow Escape to work even in inputs (for closing modals)
      if (event.key === 'Escape') {
        // If command palette is open, let it handle its own Escape
        // CommandPalette has its own Escape handler, so we don't interfere
        if (commandPaletteOpen) {
          return; // CommandPalette will handle this
        }
        // Otherwise, try to close the topmost modal
        // Only prevent default if we actually close a modal
        const closed = closeTopModal();
        if (closed) {
          event.preventDefault();
        }
        return;
      }

      // Don't handle other shortcuts when typing
      if (isInput) {
        return;
      }

      // Cmd+N or Ctrl+N - New item (context-aware)
      if ((event.metaKey || event.ctrlKey) && event.key === 'n') {
        event.preventDefault();
        const newRoute = getNewItemRoute(location.pathname);
        navigate(newRoute);
      }

      // Cmd+K or Ctrl+K - Toggle command palette
      if ((event.metaKey || event.ctrlKey) && event.key === 'k') {
        event.preventDefault();
        onCommandPaletteToggle();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [navigate, location.pathname, commandPaletteOpen, onCommandPaletteToggle, closeTopModal]);
}
