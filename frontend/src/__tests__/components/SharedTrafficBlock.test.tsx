import { render, screen } from '@testing-library/react';
import SharedTrafficBlock from '../../components/screens/SharedTrafficBlock';

describe('SharedTrafficBlock', () => {
  it('renders an iframe pointed at the supplied URL', () => {
    render(<SharedTrafficBlock url="https://example.com/traffic" title="Traffic" />);
    const iframe = screen.getByTestId('shared-traffic-iframe') as HTMLIFrameElement;
    expect(iframe).toBeInTheDocument();
    expect(iframe.src).toBe('https://example.com/traffic');
    expect(screen.getByText('Traffic')).toBeInTheDocument();
  });

  it('omits the title row when none is provided', () => {
    render(<SharedTrafficBlock url="https://example.com/traffic" />);
    expect(screen.getByTestId('shared-traffic-iframe')).toBeInTheDocument();
    expect(screen.queryByText('Traffic')).not.toBeInTheDocument();
  });
});
