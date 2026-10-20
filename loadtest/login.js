// Sign-in throughput: password hashing is deliberately slow, so this finds how many logins per second the server takes
import http from 'k6/http';
import { check } from 'k6';
const BASE = 'http://127.0.0.1:8100/api';
const RATE = parseInt(__ENV.RATE || '20');
export const options = {
  scenarios: { logins: { executor: 'constant-arrival-rate', rate: RATE, timeUnit: '1s', duration: '45s', preAllocatedVUs: 200, maxVUs: 3000 } },
  summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};
export default function () {
  const i = Math.floor(Math.random() * 3000);
  const r = http.post(`${BASE}/auth/login/`, JSON.stringify({ email: `lt${i}@load.test`, password: 'LoadTest#2026' }),
    { headers: { 'Content-Type': 'application/json' }, timeout: '60s' });
  check(r, { 'login 200': (x) => x.status === 200 });
}
