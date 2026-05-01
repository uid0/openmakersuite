import { dsnFingerprint, initSentry, readSentryEnv } from '../../utils/sentry';

describe('utils/sentry', () => {
  describe('readSentryEnv', () => {
    it('reads REACT_APP_* and NODE_ENV from process.env', () => {
      const env = {
        REACT_APP_SENTRY_DSN: 'https://abcd1234@sentry.example/42',
        REACT_APP_SENTRY_ENVIRONMENT: 'production',
        REACT_APP_GIT_HASH: 'deadbeef',
        NODE_ENV: 'production',
      } as unknown as NodeJS.ProcessEnv;

      expect(readSentryEnv(env)).toEqual({
        dsn: 'https://abcd1234@sentry.example/42',
        environment: 'production',
        release: 'deadbeef',
        nodeEnv: 'production',
      });
    });
  });

  describe('initSentry', () => {
    it('skips init and warns when DSN is missing', () => {
      const init = jest.fn();
      const logger = { info: jest.fn(), warn: jest.fn() };

      const result = initSentry({ dsn: undefined, nodeEnv: 'production' }, init, logger);

      expect(result).toBe(false);
      expect(init).not.toHaveBeenCalled();
      expect(logger.warn).toHaveBeenCalledWith(
        '[Sentry] DSN missing — exception reporting disabled',
      );
      expect(logger.info).not.toHaveBeenCalled();
    });

    it('skips init and warns when DSN is the empty string', () => {
      const init = jest.fn();
      const logger = { info: jest.fn(), warn: jest.fn() };

      const result = initSentry({ dsn: '', nodeEnv: 'production' }, init, logger);

      expect(result).toBe(false);
      expect(init).not.toHaveBeenCalled();
      expect(logger.warn).toHaveBeenCalledTimes(1);
    });

    it('initializes SDK with env config and logs INFO with release + dsn fingerprint', () => {
      const init = jest.fn();
      const logger = { info: jest.fn(), warn: jest.fn() };

      const result = initSentry(
        {
          dsn: 'https://abcd1234efgh@sentry.example/42',
          environment: 'production',
          release: 'deadbeef',
          nodeEnv: 'production',
        },
        init,
        logger,
      );

      expect(result).toBe(true);
      expect(init).toHaveBeenCalledTimes(1);
      const callArg = init.mock.calls[0][0];
      expect(callArg.dsn).toBe('https://abcd1234efgh@sentry.example/42');
      expect(callArg.environment).toBe('production');
      expect(callArg.release).toBe('deadbeef');
      expect(callArg.tracesSampleRate).toBe(0.1);
      expect(callArg.replaysOnErrorSampleRate).toBe(1.0);
      expect(Array.isArray(callArg.integrations)).toBe(true);

      expect(logger.warn).not.toHaveBeenCalled();
      expect(logger.info).toHaveBeenCalledWith(
        '[Sentry] initialized (env=production, release=deadbeef, dsn-fingerprint=abcd1234)',
      );
    });

    it('falls back to NODE_ENV when REACT_APP_SENTRY_ENVIRONMENT is unset', () => {
      const init = jest.fn();
      const logger = { info: jest.fn(), warn: jest.fn() };

      initSentry(
        { dsn: 'https://x@y/1', release: undefined, nodeEnv: 'development' },
        init,
        logger,
      );

      expect(init.mock.calls[0][0].environment).toBe('development');
      expect(init.mock.calls[0][0].tracesSampleRate).toBe(1.0);
      expect(logger.info).toHaveBeenCalledWith(
        expect.stringContaining('release=unknown'),
      );
    });

    it('uses "development" when neither environment nor NODE_ENV is set', () => {
      const init = jest.fn();
      const logger = { info: jest.fn(), warn: jest.fn() };

      initSentry({ dsn: 'https://x@y/1' }, init, logger);

      expect(init.mock.calls[0][0].environment).toBe('development');
    });
  });

  describe('dsnFingerprint', () => {
    it('returns first 8 chars of the DSN public key', () => {
      expect(dsnFingerprint('https://abcd1234efgh@sentry.example/42')).toBe('abcd1234');
    });

    it('falls back to the first 8 chars of the raw string for malformed DSNs', () => {
      expect(dsnFingerprint('not-a-dsn-string')).toBe('not-a-ds');
    });
  });
});
