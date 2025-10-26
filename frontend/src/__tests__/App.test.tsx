/**
 * Tests for App component
 */
import { render, screen, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import App from '../App';

// Mock child components to simplify testing
jest.mock('../pages/HomePage', () => () => <div>Home Page</div>);
jest.mock('../pages/ScanPage', () => () => <div>Scan Page</div>);
jest.mock('../pages/AdminDashboard', () => () => <div>Admin Dashboard</div>);

describe('App Component', () => {
  test('renders without crashing', async () => {
    await act(async () => {
      render(
        <MemoryRouter>
          <App />
        </MemoryRouter>
      );
    });
    expect(screen.getByText('Home Page')).toBeInTheDocument();
  });
});
