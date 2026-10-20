// Raw throughput of one endpoint at a fixed arrival rate (no think time)
import http from 'k6/http';
import { check } from 'k6';
const URL = __ENV.URL, RATE = parseInt(__ENV.RATE || '100');
export const options = {
  scenarios: { r: { executor: 'constant-arrival-rate', rate: RATE, timeUnit: '1s', duration: '40s', preAllocatedVUs: 200, maxVUs: 3000 } },
  summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};
export default function () { check(http.get(URL, { timeout: '30s' }), { '200': (x) => x.status === 200 }); }
