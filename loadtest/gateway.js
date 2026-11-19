// Load test: how much time does the gateway add to each request?
//
// Three runs at the same fixed rate, one after the other:
//   direct            k6 -> mock backend                                  (baseline)
//   gateway_public    k6 -> gateway (route, log, proxy) -> mock backend
//   gateway_protected k6 -> gateway (+ API key, Redis rate limit) -> mock backend
// The differences between them are what the gateway costs.
//
// Run (inside the Compose network):  docker compose run --rm loadtest
// Settings (environment): RATE (requests/s, default 300), DURATION (seconds
// per run, default 30). See loadtest/README.md.

import http from 'k6/http';
import { check, sleep } from 'k6';

const CONTROL_PLANE = __ENV.CONTROL_PLANE_URL || 'http://control-plane:8000';
const GATEWAY = __ENV.GATEWAY_URL || 'http://gateway:8080';
const BACKEND = __ENV.MOCK_BACKEND_URL || 'http://mock-backend:9000';
const RATE = Number(__ENV.RATE || 300);
const DURATION = Number(__ENV.DURATION || 30);
const PAUSE = 5; // seconds between runs, so one run's tail doesn't overlap the next
// The consumer's limit: the control plane's maximum. Every request does the
// full Redis check, but the bucket never runs dry (no 429s).
const RATE_LIMIT_PER_MIN = 100000;
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
  const res = http.request(method, `${CONTROL_PLANE}/api/v1${path}`, body ? JSON.stringify(body) : null, params);
  if (res.status >= 300) {
    throw new Error(`${method} ${path}: ${res.status} ${res.body}`);
  }
  return res.status === 204 ? null : res.json();
}

export function setup() {
  const login = admin('POST', '/auth/login', {
    email: __ENV.ADMIN_EMAIL,
    password: __ENV.ADMIN_PASSWORD,
  });
  const token = login.access_token;

  const service = admin('POST', '/services', { name: `lt-${RUN_ID}-backend`, upstream_url: BACKEND }, token);
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
    token,
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
    `  gateway itself (route, log, proxy)   median ${ms(stat('gateway_public', 'med') - stat('direct', 'med'))}  p95 ${ms(stat('gateway_public', 'p(95)') - stat('direct', 'p(95)'))}`,
    `  + key check and Redis rate limit     median ${ms(stat('gateway_protected', 'med') - stat('gateway_public', 'med'))}  p95 ${ms(stat('gateway_protected', 'p(95)') - stat('gateway_public', 'p(95)'))}`,
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
