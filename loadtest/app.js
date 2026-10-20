// What the real MagiVents frontend sends (src/App.tsx, services/api.ts): on each visit the app loads its settings
// and the whole event list once; search, filters and event pages then work in the browser from that list.
// Signed-in people also load their profile, saved events and tickets. Checkout polls the booking every 2.5 s.
import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { SharedArray } from 'k6/data';
import { Trend, Rate, Counter } from 'k6/metrics';

const BASE = __ENV.BASE || 'http://127.0.0.1:8100/api';
const USERS = parseInt(__ENV.USERS || '1000');
const tokens = new SharedArray('tokens', () => JSON.parse(open('./tokens.json')));
const FREE = [['nairobi-dev-meetup-payments', '7'], ['youth-career-fair-kicc', '8'], ['kisumu-lake-cleanup', '11'], ['nakuru-budget-forum', '12'], ['kisumu-climate-hackathon', '14']];
const PAID = [['karura-community-10k', '1'], ['nairobi-sounds-live', '3'], ['swahili-arts-food-festival', '9'], ['nairobi-pet-adoption-day', '13']];
const EVENTS = ['karura-community-10k', 'nairobi-sounds-live', 'sme-growth-summit', 'youth-career-fair-kicc', 'kisumu-lake-cleanup'];

const listT = new Trend('t_event_list', true), bookT = new Trend('t_booking_create', true);
const meT = new Trend('t_signed_in_pages', true), paidOk = new Rate('paid_confirmed'), booked = new Counter('bookings_made');

export const options = {
  scenarios: { people: { executor: 'ramping-vus', startVUs: 0,
    stages: [{ duration: __ENV.RAMP || '90s', target: USERS }, { duration: __ENV.HOLD || '3m', target: USERS }, { duration: '10s', target: 0 }],
    gracefulRampDown: '30s' } },
  thresholds: { http_req_failed: ['rate<0.01'], http_req_duration: ['p(95)<1000'] },
  summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  discardResponseBodies: true,
};

const pick = (a) => a[Math.floor(Math.random() * a.length)];
const think = (lo, hi) => sleep(lo + Math.random() * (hi - lo));
const GZ = { 'Accept-Encoding': 'gzip, deflate, br' };

function visit(h) {
  http.get(`${BASE}/config/`, { headers: h, tags: { name: 'config' } });
  // Attendees load the public list without their token (gatheringsApi.list), so it comes from the shared cache
  const r = http.get(`${BASE}/gatherings/`, { headers: GZ, tags: { name: 'event_list' } });
  listT.add(r.timings.duration);
  check(r, { 'list 200': (x) => x.status === 200 });
}

function signedInLoad(h) {
  for (const [p, n] of [['/auth/profile/', 'profile'], ['/bookmarks/', 'bookmarks'], ['/bookings/', 'my_bookings']]) {
    const r = http.get(`${BASE}${p}`, { headers: h, tags: { name: n } });
    meT.add(r.timings.duration);
    check(r, { [`${n} 200`]: (x) => x.status === 200 });
  }
}

function checkout(h, id) {
  const paid = Math.random() < 0.5;
  const [eventId, tierId] = paid ? pick(PAID) : pick(FREE);
  const body = { eventId, tierId, quantity: 1 + Math.floor(Math.random() * 3), attendeeName: `Load ${id}`, attendeeEmail: `lt${id}@load.test` };
  if (paid) Object.assign(body, { paymentMethod: 'mpesa', mpesaMode: 'stk', mpesaPhoneNumber: '0712345678' });
  const r = http.post(`${BASE}/bookings/`, JSON.stringify(body),
    { headers: Object.assign({ 'Content-Type': 'application/json' }, h), tags: { name: 'booking_create' }, responseType: 'text' });
  bookT.add(r.timings.duration);
  if (!check(r, { 'booking 201': (x) => x.status === 201 })) return;
  booked.add(1);
  if (paid) {
    const bid = r.json('id');
    let st = r.json('status');
    for (let i = 0; i < 10 && st !== 'confirmed' && st !== 'failed'; i++) {
      sleep(2.5);
      const s = http.get(`${BASE}/bookings/${bid}/status/`, { headers: h, tags: { name: 'booking_status' }, responseType: 'text' });
      if (s.status === 200) st = s.json('status');
    }
    paidOk.add(st === 'confirmed');
  }
  http.get(`${BASE}/bookings/`, { headers: h, tags: { name: 'my_bookings' } });  // "My tickets" refreshes
}

export default function () {
  const id = (__VU - 1) % tokens.length;
  const signedIn = Math.random() < 0.4;
  const h = signedIn ? Object.assign({ Authorization: `Bearer ${tokens[id]}` }, GZ) : GZ;
  group(signedIn ? 'member' : 'visitor', () => {
    visit(h);
    if (signedIn) signedInLoad(h);
    think(15, 45);                                  // browsing, searching, reading event pages (no requests)
    if (signedIn && Math.random() < 0.25) {
      http.post(`${BASE}/bookmarks/toggle/${pick(EVENTS)}/`, null, { headers: h, tags: { name: 'bookmark_toggle' } });
      think(5, 20);
    }
    if (signedIn && Math.random() < 0.25) {          // about 1 visit in 10 ends in a booking
      think(10, 30);                                // filling in checkout
      checkout(h, id);
    }
    think(10, 40);                                  // until the next reload or visit
  });
}
