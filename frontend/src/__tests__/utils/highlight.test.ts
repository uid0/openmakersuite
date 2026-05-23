import {
  DEFAULT_HIGHLIGHT_BACKEND_URL,
  DEFAULT_HIGHLIGHT_OTLP_ENDPOINT,
  initHighlight,
  projectIdFingerprint,
  readHighlightEnv,
} from '../../utils/highlight';

describe('utils/highlight', () => {
  describe('readHighlightEnv', () => {
    it('reads REACT_APP_HIGHLIGHT_* and NODE_ENV from process.env', () => {
      const env = {
        REACT_APP_HIGHLIGHT_PROJECT_ID: 'abc12345',
        REACT_APP_HIGHLIGHT_BACKEND_URL: 'https://hl.example.org/public',
        REACT_APP_HIGHLIGHT_OTLP_ENDPOINT: 'https://otel.example.org:4317',
        REACT_APP_HIGHLIGHT_ENVIRONMENT: 'production',
        REACT_APP_GIT_HASH: 'deadbeef',
        NODE_ENV: 'production',
      } as unknown as NodeJS.ProcessEnv;

      expect(readHighlightEnv(env)).toEqual({
        projectId: 'abc12345',
        backendUrl: 'https://hl.example.org/public',
        otlpEndpoint: 'https://otel.example.org:4317',
        environment: 'production',
        release: 'deadbeef',
        nodeEnv: 'production',
      });
    });
  });

  describe('initHighlight', () => {
    it('skips init and warns when project id is missing', () => {
      const init = jest.fn();
      const consumeError = jest.fn();
      const logger = { info: jest.fn(), warn: jest.fn() };

      const result = initHighlight(
        { projectId: undefined, nodeEnv: 'production' },
        init,
        logger,
        consumeError,
      );

      expect(result).toBe(false);
      expect(init).not.toHaveBeenCalled();
      expect(consumeError).not.toHaveBeenCalled();
      expect(logger.warn).toHaveBeenCalledWith(
        '[Highlight] PROJECT_ID missing — session replay & error reporting disabled',
      );
      expect(logger.info).not.toHaveBeenCalled();
    });

    it('skips init and warns when project id is the empty string', () => {
      const init = jest.fn();
      const consumeError = jest.fn();
      const logger = { info: jest.fn(), warn: jest.fn() };

      const result = initHighlight(
        { projectId: '', nodeEnv: 'production' },
        init,
        logger,
        consumeError,
      );

      expect(result).toBe(false);
      expect(init).not.toHaveBeenCalled();
      expect(consumeError).not.toHaveBeenCalled();
      expect(logger.warn).toHaveBeenCalledTimes(1);
    });

    it('initializes SDK with env config and emits boot ping', () => {
      const init = jest.fn();
      const consumeError = jest.fn();
      const logger = { info: jest.fn(), warn: jest.fn() };

      const result = initHighlight(
        {
          projectId: 'abc12345xyz',
          backendUrl: 'https://hl.example.org/public',
          otlpEndpoint: 'https://otel.example.org:4317',
          environment: 'production',
          release: 'deadbeef',
          nodeEnv: 'production',
        },
        init,
        logger,
        consumeError,
      );

      expect(result).toBe(true);
      expect(init).toHaveBeenCalledTimes(1);
      const [projectIdArg, optsArg] = init.mock.calls[0];
      expect(projectIdArg).toBe('abc12345xyz');
      expect(optsArg.environment).toBe('production');
      expect(optsArg.version).toBe('deadbeef');
      expect(optsArg.serviceName).toBe('oms-frontend');
      expect(optsArg.tracingOrigins).toBe(true);
      expect(optsArg.backendUrl).toBe('https://hl.example.org/public');
      expect(optsArg.otlpEndpoint).toBe('https://otel.example.org:4317');

      expect(logger.warn).not.toHaveBeenCalled();
      expect(logger.info).toHaveBeenCalledWith(
        '[Highlight] initialized (env=production, release=deadbeef, project=abc12345, backend=https://hl.example.org/public, otlp=https://otel.example.org:4317)',
      );
      expect(consumeError).toHaveBeenCalledTimes(1);
      expect(consumeError.mock.calls[0][0]).toBeInstanceOf(Error);
      expect(consumeError.mock.calls[0][0].message).toBe('OMS frontend boot ping');
      expect(consumeError.mock.calls[0][1]).toBe('boot');
      expect(consumeError.mock.calls[0][2]).toEqual({
        release: 'deadbeef',
        environment: 'production',
      });
    });

    it('falls back to the OMS self-host defaults when env values are unset', () => {
      const init = jest.fn();
      const consumeError = jest.fn();
      const logger = { info: jest.fn(), warn: jest.fn() };

      initHighlight(
        { projectId: 'abc12345', nodeEnv: 'production' },
        init,
        logger,
        consumeError,
      );

      const optsArg = init.mock.calls[0][1];
      expect(optsArg.backendUrl).toBe(DEFAULT_HIGHLIGHT_BACKEND_URL);
      expect(optsArg.otlpEndpoint).toBe(DEFAULT_HIGHLIGHT_OTLP_ENDPOINT);
    });

    it('falls back to NODE_ENV when REACT_APP_HIGHLIGHT_ENVIRONMENT is unset', () => {
      const init = jest.fn();
      const consumeError = jest.fn();
      const logger = { info: jest.fn(), warn: jest.fn() };

      initHighlight(
        { projectId: 'p1234567', release: undefined, nodeEnv: 'development' },
        init,
        logger,
        consumeError,
      );

      expect(init.mock.calls[0][1].environment).toBe('development');
      expect(logger.info).toHaveBeenCalledWith(
        expect.stringContaining('release=unknown'),
      );
    });

    it('uses "development" when neither environment nor NODE_ENV is set', () => {
      const init = jest.fn();
      const consumeError = jest.fn();
      const logger = { info: jest.fn(), warn: jest.fn() };

      initHighlight({ projectId: 'p1234567' }, init, logger, consumeError);

      expect(init.mock.calls[0][1].environment).toBe('development');
    });

    it('still returns true and warns when boot-ping consumeError throws', () => {
      const init = jest.fn();
      const consumeError = jest.fn(() => {
        throw new Error('not ready');
      });
      const logger = { info: jest.fn(), warn: jest.fn() };

      const result = initHighlight(
        { projectId: 'p1234567', nodeEnv: 'production' },
        init,
        logger,
        consumeError,
      );

      expect(result).toBe(true);
      expect(init).toHaveBeenCalledTimes(1);
      expect(logger.warn).toHaveBeenCalledWith(
        '[Highlight] boot ping failed: not ready',
      );
    });
  });

  describe('projectIdFingerprint', () => {
    it('returns first 8 chars of the project id', () => {
      expect(projectIdFingerprint('abcdef1234567890')).toBe('abcdef12');
    });

    it('returns the full string when the id is shorter than 8 chars', () => {
      expect(projectIdFingerprint('short')).toBe('short');
    });
  });
});
