/**
 * Hook for managing modal state and priority
 * Tracks open modals and provides functions to close them
 */
import { useCallback, useEffect } from 'react';

type ModalId = string;
type CloseHandler = () => void;

interface ModalRegistration {
  id: ModalId;
  close: CloseHandler;
  priority: number; // Higher priority modals are closed first
}

class ModalManager {
  private modals: Map<ModalId, ModalRegistration> = new Map();
  private listeners: Set<() => void> = new Set();

  register(id: ModalId, close: CloseHandler, priority: number = 0): void {
    this.modals.set(id, { id, close, priority });
    this.notifyListeners();
  }

  unregister(id: ModalId): void {
    this.modals.delete(id);
    this.notifyListeners();
  }

  closeTopModal(): boolean {
    if (this.modals.size === 0) return false;

    // Find modal with highest priority
    let topModal: ModalRegistration | null = null;
    const modalArray = Array.from(this.modals.values());
    for (let i = 0; i < modalArray.length; i++) {
      const modal = modalArray[i];
      if (!topModal || modal.priority > topModal.priority) {
        topModal = modal;
      }
    }

    if (topModal) {
      topModal.close();
      return true;
    }

    return false;
  }

  hasModals(): boolean {
    return this.modals.size > 0;
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private notifyListeners(): void {
    this.listeners.forEach((listener) => listener());
  }
}

// Singleton instance
const modalManager = new ModalManager();

/**
 * Hook to register a modal with the modal manager
 */
export function useModalManager(id: ModalId, isOpen: boolean, onClose: CloseHandler, priority: number = 0): void {
  useEffect(() => {
    if (isOpen) {
      modalManager.register(id, onClose, priority);
      return () => {
        modalManager.unregister(id);
      };
    }
  }, [id, isOpen, onClose, priority]);
}

/**
 * Hook to get the closeTopModal function
 */
export function useCloseTopModal(): () => boolean {
  return useCallback(() => {
    return modalManager.closeTopModal();
  }, []);
}

/**
 * Check if any modals are open
 */
export function hasOpenModals(): boolean {
  return modalManager.hasModals();
}
