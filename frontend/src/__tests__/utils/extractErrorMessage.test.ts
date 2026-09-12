import {
  extractErrorCode,
  extractErrorDetails,
  extractErrorMessage,
} from '../../utils/extractErrorMessage';

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

describe('utils/extractErrorCode', () => {
  const envelope = (error: unknown) => ({ response: { data: { error } } });

  it('reads the code out of the standardized envelope', () => {
    expect(extractErrorCode(envelope({ code: 'line_voided', message: 'x' }))).toBe('line_voided');
  });

  it('still reads the flat code an unconverted endpoint writes beside a string error', () => {
    expect(
      extractErrorCode({ response: { data: { error: 'Only sent orders can be confirmed', code: 'not_sent' } } }),
    ).toBe('not_sent');
  });

  it('reports no code rather than inventing one', () => {
    // A bare-prose refusal, a DRF field map, a network failure: none of these
    // is a coded refusal, and a caller branching on the code must not be told
    // one arrived.
    expect(extractErrorCode({ response: { data: { error: 'no code here' } } })).toBeUndefined();
    expect(extractErrorCode({ response: { data: { detail: 'Not found.' } } })).toBeUndefined();
    expect(extractErrorCode(envelope({ code: '   ', message: 'x' }))).toBeUndefined();
    expect(extractErrorCode(new Error('network fail'))).toBeUndefined();
    expect(extractErrorCode(undefined)).toBeUndefined();
  });
});

describe('utils/extractErrorDetails', () => {
  it('returns the envelope hints a coded refusal carries', () => {
    const candidates = [{ item_supplier: 7 }];
    expect(
      extractErrorDetails({
        response: { data: { error: { code: 'ambiguous', message: 'x', details: { candidates } } } },
      }),
    ).toEqual({ candidates });
  });

  it('reports nothing for a refusal that carries no hints', () => {
    expect(
      extractErrorDetails({ response: { data: { error: { code: 'not_draft', message: 'x' } } } }),
    ).toBeUndefined();
    // A field-error LIST is not a hint map; callers key into it by name.
    expect(
      extractErrorDetails({ response: { data: { error: { code: 'x', message: 'y', details: ['a'] } } } }),
    ).toBeUndefined();
    expect(extractErrorDetails({ response: { data: { error: 'flat prose' } } })).toBeUndefined();
    expect(extractErrorDetails(new Error('network fail'))).toBeUndefined();
  });
});
