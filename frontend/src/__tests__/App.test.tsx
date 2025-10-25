/**
 * Tests for App component
 */
import { render, screen } from '@testing-library/react';
import App from '../App';

// Mock child components to simplify testing
jest.mock('../pages/HomePage', () => () => <div>Home Page</div>);
jest.mock('../pages/ScanPage', () => () => <div>Scan Page</div>);
jest.mock('../pages/AdminDashboard', () => () => <div>Admin Dashboard</div>);

describe('App Component', () => {
  test('renders without crashing', () => {
    render(<App />);
    expect(screen.getByText('Home Page')).toBeInTheDocument();
  });
});
