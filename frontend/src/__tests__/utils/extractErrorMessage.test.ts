import { extractErrorMessage } from '../../utils/extractErrorMessage';

describe('utils/extractErrorMessage', () => {
  const fallback = 'Something went wrong';

  it('returns envelope error.message when present', () => {
    expect(
      extractErrorMessage(
        {
          response: {
            data: {
              error: {
                code: 'validation_failed',
                message: 'Username is required',
              },
            },
          },
        },
        fallback,
      ),
    ).toBe('Username is required');
  });

  it('falls back to data.detail when it is a string', () => {
    expect(
      extractErrorMessage(
        {
          response: {
            data: {
              detail: 'Legacy detail message',
            },
          },
        },
        fallback,
      ),
    ).toBe('Legacy detail message');
  });

  it('falls back to the first detail entry when detail is a non-empty array', () => {
    expect(
      extractErrorMessage(
        {
          response: {
            data: {
              detail: ['First error', 'Second error'],
            },
          },
        },
        fallback,
      ),
    ).toBe('First error');
  });

  it('falls back to data.error when it is a bare string', () => {
    expect(
      extractErrorMessage(
        {
          response: {
            data: {
              error: 'Older endpoint message',
            },
          },
        },
        fallback,
      ),
    ).toBe('Older endpoint message');
  });

  it('returns the fallback when no known error message fields are present', () => {
    expect(
      extractErrorMessage(
        {
          response: {
            data: {
              foo: 'bar',
            },
          },
        },
        fallback,
      ),
    ).toBe(fallback);
  });

  it('handles a bare data payload without a response wrapper', () => {
    expect(
      extractErrorMessage(
        {
          data: {
            detail: 'Already destructured payload',
          },
        },
        fallback,
      ),
    ).toBe('Already destructured payload');
  });

  it('returns the fallback for null, undefined, and non-object input', () => {
    expect(extractErrorMessage(null, fallback)).toBe(fallback);
    expect(extractErrorMessage(undefined, fallback)).toBe(fallback);
    expect(extractErrorMessage('plain string', fallback)).toBe(fallback);
  });

  it('returns the fallback when envelope message is an empty string', () => {
    expect(
      extractErrorMessage(
        {
          response: {
            data: {
              error: {
                code: 'server_error',
                message: '   ',
              },
            },
          },
        },
        fallback,
      ),
    ).toBe(fallback);
  });

  it('prefers envelope.message over detail when both exist', () => {
    expect(
      extractErrorMessage(
        {
          response: {
            data: {
              error: {
                code: 'validation_failed',
                message: 'Preferred envelope message',
              },
              detail: 'Legacy detail message',
            },
          },
        },
        fallback,
      ),
    ).toBe('Preferred envelope message');
  });
});
