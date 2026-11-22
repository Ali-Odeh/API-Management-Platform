// Load test: how much time does the gateway add to each request?
//
// Three runs at the same fixed rate, one after the other:
//   direct            k6 -> mock backend                                  (baseline)
//   gateway_public    k6 -> gateway (route, log, workspace rate limit, proxy) -> mock backend
//   gateway_protected k6 -> gateway (+ API key; its consumer's bucket in the same
//                     Redis call) -> mock backend
// The differences between them are what the gateway costs. Since MT.8 both
// gateway runs make the one Redis call (the workspace's bucket).
//
// Run (inside the Compose network):  docker compose run --rm loadtest
// Settings (environment): RATE (requests/s, default 300), DURATION (seconds
// per run, default 30). See loadtest/README.md.

import http from 'k6/http';
import crypto from 'k6/crypto';
import encoding from 'k6/encoding';
import { check, sleep } from 'k6';

const CONTROL_PLANE = __ENV.CONTROL_PLANE_URL || 'http://control-plane:8000';
const GOTRUE = __ENV.GOTRUE_URL || 'http://auth:9999';
const GATEWAY = __ENV.GATEWAY_URL || 'http://gateway:8080';
const BACKEND = __ENV.MOCK_BACKEND_URL || 'http://mock-backend:9000';
const RATE = Number(__ENV.RATE || 300);
const DURATION = Number(__ENV.DURATION || 30);
const PAUSE = 5; // seconds between runs, so one run's tail doesn't overlap the next
// The consumer's limit: the control plane's maximum. Every request does the
// full Redis check, but the bucket never runs dry (no 429s).
const RATE_LIMIT_PER_MIN = 100000;
// The `default` workspace's limit during the test (the control plane's
// maximum). Its own limit (WORKSPACE_RATE_LIMIT_PER_MIN, 6000/min by default)
// would refuse most of 300 requests/s: every request also passes its bucket
// since MT.8. Put back as it was afterwards.
const WORKSPACE_RATE_LIMIT_PER_MIN = 1000000;
// The Platform Admin's MFA factor the test enrolls (limits need MFA) and removes.
const FACTOR_NAME = 'loadtest';
// Unique names, so the test never touches other data (like the e2e tests).
const RUN_ID = Math.random().toString(16).slice(2, 8);
const RUNS = ['direct', 'gateway_public', 'gateway_protected'];

function scenario(index, exec) {
  return {
    executor: 'constant-arrival-rate', // exactly RATE requests/s, however slow answers are
    rate: RATE,
    timeUnit: '1s',
    duration: `${DURATION}s`,
    startTime: `${index * (DURATION + PAUSE)}s`,
    preAllocatedVUs: 50,
    maxVUs: 300,
    exec,
    tags: { run: RUNS[index] },
  };
}

export const options = {
  scenarios: {
    direct: scenario(0, 'direct'),
    gateway_public: scenario(1, 'gatewayPublic'),
    gateway_protected: scenario(2, 'gatewayProtected'),
  },
  // Pass/fail rules. k6 exits with an error code if one is broken. Naming a
  // tag here also makes k6 report that run's numbers separately.
  thresholds: Object.fromEntries(
    RUNS.flatMap((run) => [
      [`http_req_failed{run:${run}}`, ['rate<0.01']], // under 1% errors
      // A safety net, not a target: we expect a few milliseconds.
      [`http_req_duration{run:${run}}`, ['p(95)<50']],
      [`http_reqs{run:${run}}`, ['count>0']],
      [`dropped_iterations{run:${run}}`, ['count<' + Math.ceil(RATE * DURATION * 0.01)]],
    ]),
  ),
  summaryTrendStats: ['avg', 'med', 'p(95)', 'p(99)', 'max'],
};

// ---- setup / teardown: create and delete our own objects ----

function admin(method, path, body, token) {
  const params = { headers: { 'Content-Type': 'application/json' }, tags: { run: 'setup' } };
  if (token) params.headers.Authorization = `Bearer ${token}`;
  // Everything the load test creates lives in the `default` workspace.
  const res = http.request(method, `${CONTROL_PLANE}/api/v1/workspaces/default${path}`, body ? JSON.stringify(body) : null, params);
  if (res.status >= 300) {
    throw new Error(`${method} ${path}: ${res.status} ${res.body}`);
  }
  return res.status === 204 ? null : res.json();
}

function gotrue(method, path, body, token) {
  const params = { headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, tags: { run: 'setup' } };
  const res = http.request(method, `${GOTRUE}${path}`, body ? JSON.stringify(body) : null, params);
  if (res.status >= 300) {
    throw new Error(`GoTrue ${method} ${path}: ${res.status} ${res.body}`);
  }
  return res;
}

// The Platform Admin logs in with GoTrue: password, then a TOTP code (the
// workspace's limits are platform settings, which need MFA). The factor is
// enrolled here and removed in teardown, like the e2e tests do.
function login() {
  // Compose passes these through without requiring them, so check them here.
  const missing = ['PLATFORM_ADMIN_EMAIL', 'PLATFORM_ADMIN_PASSWORD', 'GOTRUE_SERVICE_ROLE_KEY'].filter((name) => !__ENV[name]);
  if (missing.length > 0) {
    throw new Error(`Set ${missing.join(', ')} in .env: the load test logs in as the Platform Admin with a password and MFA.`);
  }
  const res = http.post(
    `${GOTRUE}/token?grant_type=password`,
    JSON.stringify({ email: __ENV.PLATFORM_ADMIN_EMAIL, password: __ENV.PLATFORM_ADMIN_PASSWORD }),
    { headers: { 'Content-Type': 'application/json' }, tags: { run: 'setup' } },
  );
  if (res.status !== 200) {
    throw new Error(`GoTrue login: ${res.status} ${res.body}`);
  }
  const password = res.json().access_token;
  const userId = JSON.parse(encoding.b64decode(password.split('.')[1], 'rawurl', 's')).sub;

  removeFactors(userId); // left over by a run that was killed
  const factor = gotrue('POST', '/factors', { factor_type: 'totp', friendly_name: FACTOR_NAME }, password).json();
  const challenge = gotrue('POST', `/factors/${factor.id}/challenge`, {}, password).json();
  const verified = gotrue('POST', `/factors/${factor.id}/verify`,
    { challenge_id: challenge.id, code: totpCode(factor.totp.secret) }, password).json();
  return { token: verified.access_token, userId };
}

// Removes the test's factor through GoTrue's admin API (the service-role key);
// a factor the admin enrolled themself is left alone.
function removeFactors(userId) {
  const key = __ENV.GOTRUE_SERVICE_ROLE_KEY;
  for (const factor of gotrue('GET', `/admin/users/${userId}/factors`, null, key).json()) {
    if (factor.friendly_name === FACTOR_NAME) {
      gotrue('DELETE', `/admin/users/${userId}/factors/${factor.id}`, null, key);
    }
  }
}

// The 6-digit code an authenticator app shows (RFC 6238: HMAC-SHA1 over the
// 30-second step, with the base32 secret from enrollment).
function totpCode(secret) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  const key = [];
  let bits = 0;
  let value = 0;
  for (const c of secret.toUpperCase().replace(/=+$/, '')) {
    value = (value << 5) | alphabet.indexOf(c);
    bits += 5;
    if (bits >= 8) {
      bits -= 8;
      key.push((value >>> bits) & 0xff);
      value &= (1 << bits) - 1;
    }
  }
  const step = new Uint8Array(8);
  let counter = Math.floor(Date.now() / 30000);
  for (let i = 7; i >= 0; i--) {
    step[i] = counter % 256;
    counter = Math.floor(counter / 256);
  }
  const digest = crypto.hmac('sha1', new Uint8Array(key).buffer, step.buffer, 'hex').match(/../g).map((h) => parseInt(h, 16));
  const offset = digest[19] & 0x0f;
  const number = ((digest[offset] & 0x7f) << 24) | (digest[offset + 1] << 16) | (digest[offset + 2] << 8) | digest[offset + 3];
  return String(number % 1000000).padStart(6, '0');
}

export function setup() {
  const { token, userId } = login();

  // Raise `default`'s own rate limit for the test; teardown puts back what
  // was there (null = the platform default). k6 runs no teardown when setup
  // fails, so a failure here puts it back at once.
  const before = admin('GET', '', null, token).limit_overrides.rate_limit_per_min;
  admin('PATCH', '/limits', { rate_limit_per_min: WORKSPACE_RATE_LIMIT_PER_MIN }, token);
  try {
    return { token, userId, workspaceRateLimitBefore: before, ...createTestObjects(token) };
  } catch (e) {
    admin('PATCH', '/limits', { rate_limit_per_min: before }, token);
    removeFactors(userId);
    throw e;
  }
}

// A service, a public and a protected route, and a consumer with a key, all
// named after this run; returned once the gateway serves them.
function createTestObjects(token) {
  const service = admin('POST', '/services', { name: `lt-${RUN_ID}-backend`, targets: [{ url: BACKEND }] }, token);
  const route = (label, authRequired) =>
    admin('POST', '/routes', {
      service_id: service.id,
      path_prefix: `/lt-${RUN_ID}/${label}`,
      methods: ['GET'],
      auth_required: authRequired,
    }, token);
  const publicRoute = route('public', false);
  const protectedRoute = route('protected', true);
  const consumer = admin('POST', '/consumers', {
    name: `lt-${RUN_ID}-client`,
    email: 'loadtest@example.com',
    rate_limit_per_min: RATE_LIMIT_PER_MIN,
  }, token);
  const key = admin('POST', `/consumers/${consumer.id}/keys`, { name: 'loadtest' }, token);

  // Wait until the gateway has the new config (usually ~70 ms; up to 5 s).
  const probe = `${GATEWAY}${protectedRoute.path_prefix}/ready`;
  for (let i = 0; ; i++) {
    const res = http.get(probe, { headers: { 'X-API-Key': key.key }, tags: { run: 'setup' } });
    if (res.status === 200) break;
    if (i === 100) throw new Error(`gateway never served the test route: ${res.status}`);
    sleep(0.05);
  }

  console.log(`load test ${RUN_ID}: ${RATE} requests/s, ${DURATION} s per run`);
  return {
    runId: RUN_ID,
    publicPath: publicRoute.path_prefix,
    protectedPath: protectedRoute.path_prefix,
    apiKey: key.key,
    cleanup: { routes: [publicRoute.id, protectedRoute.id], service: service.id, consumer: consumer.id },
  };
}

export function teardown(data) {
  // Routes first: a service with routes can't be deleted. Deleting the
  // consumer deletes its key.
  data.cleanup.routes.forEach((id) => admin('DELETE', `/routes/${id}`, null, data.token));
  admin('DELETE', `/services/${data.cleanup.service}`, null, data.token);
  admin('DELETE', `/consumers/${data.cleanup.consumer}`, null, data.token);
  admin('PATCH', '/limits', { rate_limit_per_min: data.workspaceRateLimitBefore }, data.token);
  removeFactors(data.userId);
}

// ---- the three runs ----

function ok(res) {
  check(res, { 'status 200': (r) => r.status === 200 });
}

export function direct(data) {
  ok(http.get(`${BACKEND}/lt-${data.runId}/direct/item`));
}

export function gatewayPublic(data) {
  ok(http.get(`${GATEWAY}${data.publicPath}/item`));
}

export function gatewayProtected(data) {
  ok(http.get(`${GATEWAY}${data.protectedPath}/item`, { headers: { 'X-API-Key': data.apiKey } }));
}

// ---- the report: one table, and the differences between the runs ----

function brokenThresholds(data) {
  const broken = [];
  for (const [name, metric] of Object.entries(data.metrics)) {
    for (const [rule, result] of Object.entries(metric.thresholds || {})) {
      if (!result.ok) broken.push(`  ${name}: ${rule}`);
    }
  }
  return broken.length ? ['Thresholds FAILED:', ...broken].join('\n') : 'Thresholds: all passed';
}

export function handleSummary(data) {
  const stat = (run, name) => {
    const m = data.metrics[`http_req_duration{run:${run}}`];
    return m ? m.values[name] : NaN;
  };
  const count = (run) => {
    const m = data.metrics[`http_reqs{run:${run}}`];
    return m ? m.values.count : 0;
  };
  const failed = (run) => {
    const m = data.metrics[`http_req_failed{run:${run}}`];
    return m ? m.values.rate * 100 : NaN;
  };
  const ms = (v) => (Number.isNaN(v) ? '   n/a' : v.toFixed(2).padStart(6));
  const pad = (s, n) => String(s).padEnd(n);

  const lines = [
    '',
    `Load test: ${RATE} requests/s, ${DURATION} s per run`,
    '',
    `${pad('run', 19)}${pad('requests', 10)}${pad('errors', 9)}median    p95     p99   (ms)`,
    ...RUNS.map((run) =>
      `${pad(run, 19)}${pad(count(run), 10)}${pad(failed(run).toFixed(2) + '%', 9)}${ms(stat(run, 'med'))}  ${ms(stat(run, 'p(95)'))}  ${ms(stat(run, 'p(99)'))}`),
    '',
    'Time the gateway adds (difference of medians and of p95s):',
    `  gateway (route, log, Redis, proxy)   median ${ms(stat('gateway_public', 'med') - stat('direct', 'med'))}  p95 ${ms(stat('gateway_public', 'p(95)') - stat('direct', 'p(95)'))}`,
    `  + key check and consumer bucket      median ${ms(stat('gateway_protected', 'med') - stat('gateway_public', 'med'))}  p95 ${ms(stat('gateway_protected', 'p(95)') - stat('gateway_public', 'p(95)'))}`,
    `  total                                median ${ms(stat('gateway_protected', 'med') - stat('direct', 'med'))}  p95 ${ms(stat('gateway_protected', 'p(95)') - stat('direct', 'p(95)'))}`,
    '',
    `Run id ${data.setup_data ? data.setup_data.runId : '?'} (for loadtest/gateway_ms.py)`,
    brokenThresholds(data),
    '',
  ];
  return {
    stdout: lines.join('\n'),
    'results/summary.json': JSON.stringify(data, null, 2),
  };
}
