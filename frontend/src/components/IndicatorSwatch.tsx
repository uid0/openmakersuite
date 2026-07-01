/**
 * A small colored dot that mirrors what a bound indicator light shows for a
 * given status or presentation. Color, dimness (brightness) and a pulsing
 * animation (blink patterns) follow the shared ga-72l presentation policy.
 */
import './IndicatorSwatch.css';
import { ForgeKeyIndicatorPresentation, IndicatorStatusValue } from '../services/api';
import {
  IndicatorPresentationSpec,
  swatchVisualForPresentation,
  swatchVisualForStatus,
} from '../utils/indicatorPresentation';

interface Props {
  /** Render the canonical presentation for this status… */
  status?: IndicatorStatusValue;
  /** …or an explicit presentation (derived/last-pushed or a manual preview). */
  presentation?: ForgeKeyIndicatorPresentation | IndicatorPresentationSpec | null;
  /** Diameter in px (default 16). */
  size?: number;
  title?: string;
  testId?: string;
}

export default function IndicatorSwatch({
  status,
  presentation,
  size = 16,
  title,
  testId,
}: Props) {
  const visual = status
    ? swatchVisualForStatus(status)
    : swatchVisualForPresentation(presentation);

  return (
    <span
      className={`indicator-swatch${visual.blink ? ' indicator-swatch--blink' : ''}`}
      data-testid={testId}
      data-off={visual.isOff ? 'true' : 'false'}
      data-blink={visual.blink ? 'true' : 'false'}
      title={title}
      aria-hidden="true"
      style={{
        width: size,
        height: size,
        background: visual.background,
        opacity: visual.blink ? undefined : visual.opacity,
      }}
    />
  );
}
