import { render, screen } from '@testing-library/react';
import React from 'react';
import SharedWeatherBlock from '../../../components/screens/SharedWeatherBlock';

describe('SharedWeatherBlock', () => {
  it('renders an iframe pointing at the supplied weather URL', () => {
    render(<SharedWeatherBlock weatherUrl="https://www.wunderground.com/weather/us/tx/carrollton/" />);
    const iframe = screen.getByTestId('shared-weather-iframe') as HTMLIFrameElement;
    expect(iframe).toBeInTheDocument();
    expect(iframe.tagName).toBe('IFRAME');
    expect(iframe.getAttribute('src')).toBe(
      'https://www.wunderground.com/weather/us/tx/carrollton/',
    );
  });

  it('shows a fallback message when no URL is configured', () => {
    render(<SharedWeatherBlock weatherUrl="" />);
    expect(screen.getByTestId('shared-weather-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('shared-weather-iframe')).toBeNull();
  });
});
