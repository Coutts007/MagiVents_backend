// Realistic MagiVents traffic: each VU is one person using the app, with human think time between actions.
import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { SharedArray } from 'k6/data';
import { Trend, Rate, Counter } from 'k6/metrics';

const BASE = __ENV.BASE || 'http://127.0.0.1:8100/api';
const USERS = parseInt(__ENV.USERS || '500');
const HOLD = __ENV.HOLD || '2m';
const RAMP = __ENV.RAMP || '30s';
const tokens = new SharedArray('tokens', () => JSON.parse(open('./tokens.json')));
const EVENTS = ['karura-community-10k','nairobi-sounds-live','sme-growth-summit','nairobi-dev-meetup-payments','youth-career-fair-kicc','swahili-arts-food-festival','kisumu-lake-cleanup','nakuru-budget-forum','nairobi-pet-adoption-day','kisumu-climate-hackathon'];
const FREE = [['nairobi-dev-meetup-payments','7'],['youth-career-fair-kicc','8'],['kisumu-lake-cleanup','11'],['nakuru-budget-forum','12'],['kisumu-climate-hackathon','14']];
const PAID = [['karura-community-10k','1'],['nairobi-sounds-live','3'],['swahili-arts-food-festival','9'],['nairobi-pet-adoption-day','13']];
const SEARCH = ['music','Nairobi','run','tech','food','Kisumu'];

const listT = new Trend('t_event_list', true), detailT = new Trend('t_event_detail', true);
const bookT = new Trend('t_booking_create', true), authT = new Trend('t_signed_in_pages', true);
const paidOk = new Rate('paid_confirmed'), bookingsMade = new Counter('bookings_made');

export const options = {
  scenarios: { people: { executor: 'ramping-vus', startVUs: 0,
    stages: [{ duration: RAMP, target: USERS }, { duration: HOLD, target: USERS }, { duration: '10s', target: 0 }],
    gracefulRampDown: '20s' } },
  thresholds: { http_req_failed: ['rate<0.01'], http_req_duration: ['p(95)<1000'] },
  summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  discardResponseBodies: false,
};

const pick = (a) => a[Math.floor(Math.random() * a.length)];
const think = (lo, hi) => sleep(lo + Math.random() * (hi - lo));
const J = { 'Content-Type': 'application/json' };

function browse(h) {
  http.get(`${BASE}/config/`, { headers: h, tags: { name: 'config' } });
  let r = http.get(`${BASE}/gatherings/`, { headers: h, tags: { name: 'event_list' } });
  listT.add(r.timings.duration);
  check(r, { 'list 200': (x) => x.status === 200 });
  think(3, 8);
  if (Math.random() < 0.3) {
    r = http.get(`${BASE}/gatherings/?search=${pick(SEARCH)}`, { headers: h, tags: { name: 'event_search' } });
    check(r, { 'search 200': (x) => x.status === 200 });
    think(2, 5);
  }
  r = http.get(`${BASE}/gatherings/${pick(EVENTS)}/`, { headers: h, tags: { name: 'event_detail' } });
  detailT.add(r.timings.duration);
  check(r, { 'detail 200': (x) => x.status === 200 });
  think(5, 12);
}

function signedIn(h) {
  for (const [p, n] of [['/auth/profile/', 'profile'], ['/notifications/', 'notifications'], ['/bookmarks/', 'bookmarks'], ['/bookings/', 'my_bookings']]) {
    const r = http.get(`${BASE}${p}`, { headers: h, tags: { name: n } });
    authT.add(r.timings.duration);
    check(r, { [`${n} 200`]: (x) => x.status === 200 });
  }
  think(2, 5);
  if (Math.random() < 0.3) {
    const r = http.post(`${BASE}/bookmarks/toggle/${pick(EVENTS)}/`, null, { headers: h, tags: { name: 'bookmark_toggle' } });
    check(r, { 'bookmark ok': (x) => x.status === 200 || x.status === 201 });
  }
}

function buy(h, id) {
  const paid = Math.random() < 0.5;
  const [eventId, tierId] = paid ? pick(PAID) : pick(FREE);
  http.get(`${BASE}/gatherings/${eventId}/`, { headers: h, tags: { name: 'event_detail' } });
  think(5, 15); // filling the checkout form
  const body = { eventId, tierId, quantity: 1 + Math.floor(Math.random() * 3), attendeeName: `Load ${id}`, attendeeEmail: `lt${id}@load.test` };
  if (paid) Object.assign(body, { paymentMethod: 'mpesa', mpesaMode: 'stk', mpesaPhoneNumber: '0712345678' });
  const r = http.post(`${BASE}/bookings/`, JSON.stringify(body), { headers: Object.assign({}, h, J), tags: { name: 'booking_create' } });
  bookT.add(r.timings.duration);
  if (!check(r, { 'booking 201': (x) => x.status === 201 })) return;
  bookingsMade.add(1);
  if (!paid) return;
  // Checkout polls the booking while the (simulated) STK push completes
  const bid = r.json('id') || r.json('bookingId') || r.json('booking.id');
  let st = r.json('status') || r.json('booking.status');
  for (let i = 0; i < 8 && st !== 'confirmed' && st !== 'failed'; i++) {
    sleep(3);
    const s = http.get(`${BASE}/bookings/${bid}/status/`, { headers: h, tags: { name: 'booking_status' } });
    st = s.status === 200 ? (s.json('status') || s.json('booking.status')) : st;
  }
  paidOk.add(st === 'confirmed');
}

export default function () {
  const id = (__VU - 1) % tokens.length;
  const roll = Math.random();
  if (roll < 0.6) { group('visitor', () => browse({})); return; }
  const h = { Authorization: `Bearer ${tokens[id]}` };
  if (roll < 0.9) { group('member', () => { signedIn(h); browse(h); }); return; }
  group('buyer', () => { browse(h); buy(h, id); });
}
