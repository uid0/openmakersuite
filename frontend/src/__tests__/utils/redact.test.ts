import { redactError, redactString } from '../../utils/redact';

describe('redactString', () => {
  it('returns short non-sensitive strings unchanged', () => {
    expect(redactString('hello world')).toBe('hello world');
    expect(redactString('short')).toBe('short');
  });

  it('scrubs Bearer tokens', () => {
    const out = redactString('Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig');
    expect(out).not.toContain('eyJhbGciOiJIUzI1NiJ9.payload.sig');
    expect(out).toContain('[REDACTED]');
  });

  it('scrubs raw JWTs', () => {
    const out = redactString('token = eyJhbGciOiJIUzI1NiJ9.payload.signature123');
    expect(out).not.toContain('eyJhbGciOiJIUzI1NiJ9.payload.signature123');
    expect(out).toContain('[REDACTED]');
  });

  it('scrubs PEM private key blocks', () => {
    const pem =
      '-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAxxxx\n-----END RSA PRIVATE KEY-----';
    const out = redactString(`Bad key: ${pem}`);
    expect(out).not.toContain('BEGIN RSA PRIVATE KEY');
    expect(out).toContain('[REDACTED]');
    expect(out).toContain('Bad key:');
  });

  it('scrubs high-entropy 32+ char tokens', () => {
    const secret = 'abcdef0123456789ABCDEFabcdef0123456789ABCDEF';
    const out = redactString(`apiKey=${secret}`);
    expect(out).not.toContain(secret);
    expect(out).toContain('apiKey=');
  });

  it('does not mangle ordinary URLs even when long', () => {
    const url = 'https://example.com/api/v1/items/123/details';
    expect(redactString(url)).toBe(url);
  });
});

describe('redactError', () => {
  it('preserves the error name', () => {
    const err = new TypeError('boom: Bearer eyJabc.payload.sig');
    const scrubbed = redactError(err);
    expect(scrubbed.name).toBe('TypeError');
  });

  it('scrubs the message', () => {
    const err = new Error('401 for url: https://x.com/?token=Bearer eyJabc.payload.sig');
    const scrubbed = redactError(err);
    expect(scrubbed.message).not.toContain('eyJabc.payload.sig');
    expect(scrubbed.message).toContain('[REDACTED]');
    expect(scrubbed.message).toContain('401 for url');
  });

  it('scrubs the stack', () => {
    const err = new Error('boom');
    err.stack =
      'Error: boom\n    at deliver (tasks.ts:42:10)\n    Bearer eyJabc.payload.sig';
    const scrubbed = redactError(err);
    expect(scrubbed.stack).toBeDefined();
    expect(scrubbed.stack).not.toContain('eyJabc.payload.sig');
    expect(scrubbed.stack).toContain('deliver (tasks.ts:42:10)');
  });

  it('handles errors with an empty message', () => {
    const err = new Error();
    const scrubbed = redactError(err);
    expect(scrubbed.message).toBe('');
    expect(scrubbed.name).toBe(err.name);
  });
});
