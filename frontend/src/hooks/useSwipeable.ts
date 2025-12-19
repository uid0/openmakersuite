/**
 * Custom hook for swipe gestures
 * Provides swipe detection with configurable thresholds and callbacks
 */
import { useSwipeable } from 'react-swipeable';

export interface SwipeableConfig {
  onSwipeLeft?: () => void;
  onSwipeRight?: () => void;
  threshold?: number; // Minimum distance in pixels to trigger swipe
  trackMouse?: boolean;
}

export const useSwipeableActions = (config: SwipeableConfig) => {
  const {
    onSwipeLeft,
    onSwipeRight,
    threshold = 50,
    preventDefaultTouchmoveEvent = false,
    trackMouse = false,
  } = config;

  const handlers = useSwipeable({
    onSwipedLeft: onSwipeLeft,
    onSwipedRight: onSwipeRight,
    delta: threshold,
    preventDefaultTouchmoveEvent,
    trackMouse,
  });

  return handlers;
};
