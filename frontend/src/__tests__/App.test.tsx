/**
 * Tests for App component
 */
import { render, screen } from '@testing-library/react';
import App from '../App';

// Mock child components to simplify testing
vi.mock('../pages/HomePage', () => ({ default: () => <div>Home Page</div> }));
vi.mock('../pages/ScanPage', () => ({ default: () => <div>Scan Page</div> }));
vi.mock('../pages/AdminDashboard', () => ({ default: () => <div>Admin Dashboard</div> }));

describe('App Component', () => {
  test('renders without crashing', () => {
    render(<App />);
    expect(screen.getByText('Home Page')).toBeInTheDocument();
  });
});
