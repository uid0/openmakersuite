import { render, screen } from '@testing-library/react';
import React from 'react';
import Footer from '../../components/Footer';

// Mock environment variables
const originalEnv = process.env;

describe('Footer Component', () => {
  beforeEach(() => {
    jest.resetModules();
    process.env = { ...originalEnv };
  });

  afterEach(() => {
    process.env = originalEnv;
  });

  it('renders footer with git hash and GitHub link', () => {
    process.env.REACT_APP_GIT_HASH = 'abc1234';
    process.env.REACT_APP_GITHUB_URL = 'https://github.com/test/repo';

    render(<Footer />);

    expect(screen.getByText(/Open Maker Suite/)).toBeInTheDocument();
    expect(screen.getByText('abc1234')).toBeInTheDocument();
    expect(screen.getByText('View on Github')).toBeInTheDocument();
    
    const githubLink = screen.getByText('View on Github').closest('a');
    expect(githubLink).toHaveAttribute('href', 'https://github.com/test/repo');
    expect(githubLink).toHaveAttribute('target', '_blank');
    expect(githubLink).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('uses default values when environment variables are not set', () => {
    delete process.env.REACT_APP_GIT_HASH;
    delete process.env.REACT_APP_GITHUB_URL;

    render(<Footer />);

    expect(screen.getByText('dev')).toBeInTheDocument();
    expect(screen.getByText('View on Github')).toBeInTheDocument();
    
    const githubLink = screen.getByText('View on Github').closest('a');
    expect(githubLink).toHaveAttribute('href', 'https://github.com/uid0/openmakersuite');
  });

  it('applies custom className when provided', () => {
    process.env.REACT_APP_GIT_HASH = 'test123';
    
    const { container } = render(<Footer className="custom-footer" />);
    
    const footer = container.querySelector('.app-footer.custom-footer');
    expect(footer).toBeInTheDocument();
  });
});

