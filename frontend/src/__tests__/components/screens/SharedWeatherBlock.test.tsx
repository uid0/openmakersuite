import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';

import SharedWeatherBlock from '../../../components/screens/SharedWeatherBlock';

jest.mock('../../../services/api', () => ({
  __esModule: true,
  default: { get: jest.fn() },
}));

// eslint-disable-next-line @typescript-eslint/no-var-requires
const api = require('../../../services/api').default;

describe('SharedWeatherBlock', () => {
  beforeEach(() => {
    api.get.mockReset();
  });

  it('renders the native weather panel when the backend returns data', async () => {
    api.get.mockResolvedValueOnce({
      data: {
        location_name: 'Carrollton',
        country: 'US',
        condition: 'Clouds',
        description: 'scattered clouds',
        icon: '03d',
        temperature: 78.4,
        feels_like: 79.1,
        humidity: 55,
        wind_speed: 7.2,
        units: 'imperial',
        observed_at: 1747523400,
        cache_hit: false,
      },
    });

    render(<SharedWeatherBlock />);

    await waitFor(() => {
      expect(screen.getByTestId('shared-weather-native')).toBeInTheDocument();
    });
    expect(screen.getByText('78°F')).toBeInTheDocument();
    expect(screen.getByText('scattered clouds')).toBeInTheDocument();
    expect(screen.getByText(/Carrollton, US/)).toBeInTheDocument();
  });

  it('shows "not configured" message when the backend reports it', async () => {
    api.get.mockRejectedValueOnce({
      isAxiosError: true,
      response: { data: { code: 'weather_not_configured' } },
    });

    render(<SharedWeatherBlock />);

    await waitFor(() => {
      expect(screen.getByTestId('shared-weather-not-configured')).toBeInTheDocument();
    });
  });

  it('falls back to the legacy iframe URL when the native fetch fails', async () => {
    api.get.mockRejectedValueOnce(new Error('network fail'));

    render(<SharedWeatherBlock weatherUrl="https://example.com/weather" />);

    await waitFor(() => {
      const iframe = screen.getByTestId('shared-weather-iframe-fallback') as HTMLIFrameElement;
      expect(iframe.getAttribute('src')).toBe('https://example.com/weather');
    });
  });

  it('shows "Weather unavailable" when the fetch fails and no fallback URL is supplied', async () => {
    api.get.mockRejectedValueOnce(new Error('network fail'));

    render(<SharedWeatherBlock />);

    await waitFor(() => {
      expect(screen.getByTestId('shared-weather-error')).toBeInTheDocument();
    });
  });
});
