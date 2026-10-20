// Maximum throughput of one endpoint for signed-in users (no think time)
import http from 'k6/http';
import { check } from 'k6';
import { SharedArray } from 'k6/data';
const tokens = new SharedArray('tokens', () => JSON.parse(open('./tokens.json')));
export const options = { scenarios: { b: { executor: 'constant-vus', vus: 64, duration: '15s' } }, discardResponseBodies: true,
  summaryTrendStats: ['med', 'p(95)'] };
export default function () {
  const h = { 'Accept-Encoding': 'gzip' };
  if (__ENV.AUTH === '1') h.Authorization = `Bearer ${tokens[Math.floor(Math.random() * tokens.length)]}`;
  check(http.get(`http://127.0.0.1:8100/api/${__ENV.P}`, { headers: h }), { ok: (r) => r.status === 200 });
}
