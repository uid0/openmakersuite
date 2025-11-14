/**
 * Footer Component
 * Displays git hash and GitHub link
 */
import React from 'react';
import './Footer.css';

interface FooterProps {
  className?: string;
}

const Footer: React.FC<FooterProps> = ({ className = '' }) => {
  // Get git hash from environment variable (set at build time)
  const gitHash = process.env.REACT_APP_GIT_HASH || 'dev';
  const githubUrl = process.env.REACT_APP_GITHUB_URL || 'https://github.com/uid0/openmakersuite';

  return (
    <footer className={`app-footer ${className}`}>
      <p>
        Open Maker Suite, rev. <span className="git-hash">{gitHash}</span>,{' '}
        <a
          href={githubUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="github-link"
        >
          View on Github
        </a>
        .
      </p>
    </footer>
  );
};

export default Footer;

