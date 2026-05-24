import { render, screen } from '@testing-library/react';
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Footer from '../../components/Footer';

describe('Footer Component', () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('renders footer with git hash and GitHub link', () => {
    vi.stubEnv('VITE_GIT_HASH', 'abc1234');
    vi.stubEnv('VITE_GITHUB_URL', 'https://github.com/test/repo');

    render(<Footer />);

    expect(screen.getByText(/Open Maker Suite/)).toBeInTheDocument();
    expect(screen.getByText('abc1234')).toBeInTheDocument();
    expect(screen.getByText('View on Github')).toBeInTheDocument();

    const githubLink = screen.getByRole('link', { name: /view on github/i });
    expect(githubLink).toHaveAttribute('href', 'https://github.com/test/repo');
    expect(githubLink).toHaveAttribute('target', '_blank');
    expect(githubLink).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('uses default values when environment variables are not set', () => {
    render(<Footer />);

    expect(screen.getByText('dev')).toBeInTheDocument();
    expect(screen.getByText('View on Github')).toBeInTheDocument();

    const githubLink = screen.getByRole('link', { name: /view on github/i });
    expect(githubLink).toHaveAttribute('href', 'https://github.com/uid0/openmakersuite');
  });

  it('applies custom className when provided', () => {
    vi.stubEnv('VITE_GIT_HASH', 'test123');

    render(<Footer className="custom-footer" />);

    const footer = screen.getByRole('contentinfo');
    expect(footer).toHaveClass('app-footer', 'custom-footer');
  });
});

