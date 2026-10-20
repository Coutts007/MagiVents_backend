// Ticket-sale rush: everyone books the same free event within seconds (worst case for the row lock on the event)
import http from 'k6/http';
import { check, sleep } from 'k6';
import { SharedArray } from 'k6/data';
const BASE = 'http://127.0.0.1:8100/api';
const tokens = new SharedArray('tokens', () => JSON.parse(open('./tokens.json')));
const PEAK = parseInt(__ENV.PEAK || '2000');
export const options = {
  scenarios: { rush: { executor: 'ramping-vus', startVUs: 0,
    stages: [{ duration: '10s', target: PEAK }, { duration: '60s', target: PEAK }, { duration: '5s', target: 0 }] } },
  summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};
export default function () {
  const id = (__VU - 1) % tokens.length;
  const h = { Authorization: `Bearer ${tokens[id]}`, 'Content-Type': 'application/json' };
  http.get(`${BASE}/gatherings/youth-career-fair-kicc/`, { headers: h, tags: { name: 'event_detail' } });
  sleep(1 + Math.random() * 2);
  const r = http.post(`${BASE}/bookings/`, JSON.stringify({ eventId: 'youth-career-fair-kicc', tierId: '8', quantity: 1,
    attendeeName: `Load ${id}`, attendeeEmail: `lt${id}@load.test` }), { headers: h, tags: { name: 'booking_create' }, timeout: '60s' });
  check(r, { 'booking 201': (x) => x.status === 201 });
  sleep(2 + Math.random() * 3);
}
