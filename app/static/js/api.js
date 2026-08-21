/* Thin API client. Every backend call goes through here so error handling
   and the request envelope stay in one place. */

const BASE = '/api/v1';

export class ApiError extends Error {
  constructor(message, { code = 'ERROR', status = 0, details = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

async function parseError(response) {
  let payload = null;
  try { payload = await response.json(); } catch { /* non-JSON body */ }
  const err = payload?.error;
  if (err) {
    return new ApiError(err.message || 'Request failed.', {
      code: err.code, status: response.status, details: err.details ?? null,
    });
  }
  return new ApiError(
    `Request failed (${response.status} ${response.statusText}).`,
    { code: 'HTTP_ERROR', status: response.status },
  );
}

async function request(path, { method = 'GET', body, signal, raw = false } = {}) {
  let response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (cause) {
    if (cause?.name === 'AbortError') throw cause;
    throw new ApiError(
      'Cannot reach the API. Check that the server is running.',
      { code: 'NETWORK_ERROR' },
    );
  }

  if (!response.ok) throw await parseError(response);
  if (raw) return response;
  if (response.status === 204) return null;
  return response.json();
}

export const api = {
  decode: (payload, signal) => request('/decode', { method: 'POST', body: payload, signal }),
  decodeOne: (vin, opts = {}) => {
    const params = new URLSearchParams();
    if (opts.refresh) params.set('refresh', 'true');
    if (opts.verify) params.set('verify', 'true');
    const qs = params.toString();
    return request(`/decode/${encodeURIComponent(vin)}${qs ? `?${qs}` : ''}`);
  },
  validate: (vin) => request(`/validate/${encodeURIComponent(vin)}`),
  recent: (limit = 12) => request(`/vehicles/recent?limit=${limit}`),
  vehicle: (vin) => request(`/vehicles/${encodeURIComponent(vin)}`),
  compare: (vins) => request('/compare', { method: 'POST', body: { vins } }),
  providers: () => request('/providers'),
  usage: () => request('/usage'),
  health: () => request('/health'),

  async export(vins, format) {
    const response = await request('/export', {
      method: 'POST', body: { vins, format }, raw: true,
    });
    const disposition = response.headers.get('Content-Disposition') || '';
    const match = disposition.match(/filename="?([^"]+)"?/);
    return {
      blob: await response.blob(),
      filename: match ? match[1] : `vin-export.${format}`,
    };
  },
};
