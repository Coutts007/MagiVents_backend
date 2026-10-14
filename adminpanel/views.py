"""
Admin dashboard API (/api/admin/...). Every endpoint needs a signed-in admin (role 'admin', which is kept in
sync with is_staff); everyone else gets HTTP 403. The staff console on its secret path is separate.

Changes are written to the shared audit log (console.ConsoleAction) with who, what and why.
"""
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from accounts.permissions import IsPlatformAdmin
from console.views import log_action, paginate
from gatherings.models import Gathering
from listings.models import CategoryFee, ListingFee, PlatformPaymentAccount
from listings.services import amount_due, fee_table, mark_fee_paid, refresh_category, waive_fee
from payments.models import Payment
from payouts.ledger import earnings_subquery, outstanding_total, payouts_subquery, tickets_subquery
from payouts.models import PayoutAccount
from payouts.serializers import PayoutAccountSerializer, payout_account_payload, save_payout_account
from tickets.models import Booking
from .invites import send_organizer_invite

NAIROBI = ZoneInfo('Africa/Nairobi')


def money(value):
    return float(value or 0)


class AdminView(APIView):
    permission_classes = [IsPlatformAdmin]


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000, trim_whitespace=True)


# --- Overview ----------------------------------------------------------------------------------------------

def month_start():
    now = timezone.now().astimezone(NAIROBI)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def fees_collected(since=None):
    paid = Payment.objects.filter(purpose='listing_fee', status='succeeded', needs_refund=False)
    if since is not None:
        paid = paid.filter(completed_at__gte=since)
    return money(paid.aggregate(t=Sum('amount'))['t'])


def unpaid_total(fees):
    table = fee_table()
    return sum(amount_due(fee, fee_table=table) for fee in fees.select_related('gathering'))


@extend_schema(responses=OpenApiTypes.OBJECT)
class OverviewView(AdminView):
    def get(self, request):
        return Response({
            'organizers': User.objects.filter(role='curator').count(),
            'suspendedOrganizers': User.objects.filter(role='curator', is_suspended=True).count(),
            'eventsAwaitingPayment': ListingFee.objects.filter(status__in=['unpaid', 'pending']).count(),
            'feesCollectedThisMonth': fees_collected(month_start()),
            'feesCollectedAllTime': fees_collected(),
            'balancesOutstanding': outstanding_total(),
            'unpaidFeesTotal': unpaid_total(ListingFee.objects.filter(status__in=['unpaid', 'pending'])),
            'monthStart': month_start().isoformat(),
        })


# --- Organizers ----------------------------------------------------------------------------------------------

def _count(queryset, group_field):
    return Coalesce(Subquery(queryset.values(group_field).annotate(n=Count('pk')).values('n')[:1],
                             output_field=IntegerField()), Value(0))


def organizer_queryset():
    fee_sum = ListingFee.objects.filter(gathering__organizer=OuterRef('pk')).values('gathering__organizer') \
        .annotate(t=Sum('amount_paid')).values('t')[:1]
    return User.objects.annotate(
        events_count=_count(Gathering.objects.filter(organizer=OuterRef('pk')), 'organizer'),
        published_count=_count(Gathering.objects.filter(organizer=OuterRef('pk'), status__in=['published', 'sold_out']),
                               'organizer'),
        bookings_count=_count(Booking.objects.filter(gathering__organizer=OuterRef('pk')), 'gathering__organizer'),
        fees_paid=Coalesce(Subquery(fee_sum, output_field=IntegerField()), Value(0)),
        tickets_sold=tickets_subquery(),
        earnings=earnings_subquery(),
        payouts_total=payouts_subquery(),
    )


def organizer_row(user):
    earnings, payouts = money(getattr(user, 'earnings', 0)), money(getattr(user, 'payouts_total', 0))
    default_details = PayoutAccount.objects.filter(organizer=user, gathering__isnull=True).first()
    return {
        'id': str(user.id),
        'name': user.first_name or user.email.split('@')[0],
        'email': user.email,
        'city': user.city,
        'bio': user.bio,
        'role': user.role,
        'isSuspended': user.is_suspended,
        'suspendedAt': user.suspended_at.isoformat() if user.suspended_at else None,
        'suspensionReason': user.suspension_reason,
        'isActive': user.is_active,
        # False until an invited organizer sets their password
        'hasPassword': user.has_usable_password() or user.auth_provider == 'google',
        'joinedDate': user.date_joined.isoformat(),
        'lastLogin': user.last_login.isoformat() if user.last_login else None,
        'eventsCount': getattr(user, 'events_count', 0),
        'publishedCount': getattr(user, 'published_count', 0),
        'bookingsCount': getattr(user, 'bookings_count', 0),
        'listingFeesPaid': getattr(user, 'fees_paid', 0),
        'ticketsSold': getattr(user, 'tickets_sold', 0),
        'ticketSales': earnings,
        'earnings': earnings,
        'payoutsTotal': payouts,
        'balance': earnings - payouts,
        'paymentDetails': payout_account_payload(default_details),
    }


def get_organizer(user_id):
    user = organizer_queryset().filter(id=user_id).first()
    if user is None:
        raise NotFound('No such organizer.')
    return user


class CreateOrganizerSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    city = serializers.CharField(max_length=120, required=False, allow_blank=True)
    bio = serializers.CharField(required=False, allow_blank=True)

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('An account with this email already exists.')
        return value


@extend_schema(request=CreateOrganizerSerializer, responses=OpenApiTypes.OBJECT)
class OrganizerListView(AdminView):
    def get(self, request):
        params = request.query_params
        qs = organizer_queryset()
        role = params.get('role', '')
        if role in ('curator', 'patron'):
            qs = qs.filter(role=role)
        else:
            # Organizers, plus anyone who still owns events after being demoted
            qs = qs.filter(Q(role='curator') | Q(events_count__gt=0))
        state = params.get('status', '')
        if state == 'active':
            qs = qs.filter(is_suspended=False)
        elif state == 'suspended':
            qs = qs.filter(is_suspended=True)
        elif state == 'invited':
            qs = qs.filter(password__startswith='!', auth_provider='email')
        search = params.get('search', '').strip()
        if search:
            qs = qs.filter(Q(email__icontains=search) | Q(first_name__icontains=search) | Q(city__icontains=search))
        sort = {'name': 'first_name', 'joined': '-date_joined', 'events': '-events_count', 'sales': '-earnings',
                'fees': '-fees_paid'}
        qs = qs.order_by(sort.get(params.get('sort'), 'first_name'), 'email')
        return Response(paginate(request, qs, organizer_row))

    def post(self, request):
        data = CreateOrganizerSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        user = User(username=values['email'], email=values['email'], first_name=values['name'].strip(),
                    city=values.get('city', ''), bio=values.get('bio', ''), role='curator', auth_provider='email')
        user.set_unusable_password()
        user.save()
        invited = send_organizer_invite(request, user)
        log_action(request, 'create_organizer', 'user', user.id, user.email)
        return Response({**organizer_row(get_organizer(user.id)), 'inviteSent': invited},
                        status=status.HTTP_201_CREATED)


class UpdateOrganizerSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150, required=False)
    email = serializers.EmailField(required=False)
    city = serializers.CharField(max_length=120, required=False, allow_blank=True)
    bio = serializers.CharField(required=False, allow_blank=True)
    # Promote an attendee to organizer, or demote an organizer. Admins are managed in the Django admin.
    role = serializers.ChoiceField(choices=['patron', 'curator'], required=False)


class DeleteOrganizerSerializer(serializers.Serializer):
    confirmEmail = serializers.EmailField()


def organizer_events(user):
    table = fee_table()
    rows = []
    for g in Gathering.objects.filter(organizer=user).select_related('listing_fee').annotate(
        sold=Coalesce(Sum('bookings__quantity', filter=Q(bookings__status='confirmed')), Value(0)),
        revenue=Sum('bookings__total_price', filter=Q(bookings__status='confirmed')),
    ).order_by('-iso_date'):
        fee = getattr(g, 'listing_fee', None)
        rows.append({
            'id': str(g.id), 'title': g.title, 'date': g.date_display, 'category': g.category, 'status': g.status,
            'ticketsSold': g.sold, 'revenue': money(g.revenue),
            'feeStatus': fee.status if fee else None,
            'feeDue': amount_due(fee, fee_table=table) if fee else 0,
            'paymentDetails': payout_account_payload(PayoutAccount.objects.filter(gathering=g).first()),
        })
    return rows


@extend_schema(request=UpdateOrganizerSerializer, responses=OpenApiTypes.OBJECT)
class OrganizerDetailView(AdminView):
    def get(self, request, user_id):
        user = get_organizer(user_id)
        from payouts.ledger import event_earnings
        from payouts.models import Payout
        return Response({
            **organizer_row(user),
            'events': organizer_events(user),
            'eventEarnings': event_earnings(user),
            'payouts': [payout_row(p) for p in Payout.objects.filter(organizer=user)],
        })

    def patch(self, request, user_id):
        user = get_organizer(user_id)
        data = UpdateOrganizerSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        if 'email' in values:
            email = values['email'].strip().lower()
            if User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
                raise ValidationError({'email': 'Another account already uses this email.'})
            if user.username == user.email:
                user.username = email
            user.email = email
        if 'role' in values and values['role'] != user.role:
            if user.role == 'admin':
                raise ValidationError({'role': 'Administrators are managed in the Django admin.'})
            log_action(request, 'promote_organizer' if values['role'] == 'curator' else 'demote_organizer',
                       'user', user.id, user.email)
            user.role = values['role']
        if 'name' in values:
            user.first_name = values['name'].strip()
        for field in ('city', 'bio'):
            if field in values:
                setattr(user, field, values[field])
        user.save()
        log_action(request, 'edit_organizer', 'user', user.id, user.email, ', '.join(sorted(values)))
        return Response(organizer_row(get_organizer(user.id)))

    def delete(self, request, user_id):
        """Permanent deletion, only when none of the organizer's events has a booking or a paid listing fee."""
        user = get_organizer(user_id)
        data = DeleteOrganizerSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        if data.validated_data['confirmEmail'].strip().lower() != user.email.lower():
            raise ValidationError({'confirmEmail': 'Type the organizer\'s email exactly to confirm.'})
        if user.role == 'admin' or user.pk == request.user.pk:
            raise ValidationError({'detail': 'Administrators cannot be deleted here.'})
        if Booking.objects.filter(gathering__organizer=user).exists():
            return Response({
                'error': 'This organizer\'s events have bookings, so the account cannot be deleted: ticket holders '
                         'and sales records must be kept. Suspend the organizer instead.',
                'code': 'has_bookings', 'canSuspend': True,
            }, status=status.HTTP_409_CONFLICT)
        if Payment.objects.filter(listing_fee__gathering__organizer=user, status='succeeded').exists():
            return Response({
                'error': 'This organizer has paid listing fees, which are financial records that must be kept. '
                         'Suspend the organizer instead.',
                'code': 'has_payments', 'canSuspend': True,
            }, status=status.HTTP_409_CONFLICT)
        email = user.email
        with transaction.atomic():
            Payment.objects.filter(listing_fee__gathering__organizer=user).delete()
            User.objects.filter(pk=user.pk).delete()
            log_action(request, 'delete_organizer', 'user', user_id, email)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class ResendInviteView(AdminView):
    def post(self, request, user_id):
        user = get_organizer(user_id)
        if user.has_usable_password():
            raise ValidationError({'detail': 'This organizer has already set a password.'})
        if not send_organizer_invite(request, user):
            return Response({'error': 'The email could not be sent right now.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        log_action(request, 'resend_invite', 'user', user.id, user.email)
        return Response({'message': f'Invitation sent to {user.email}.'})


@extend_schema(request=PayoutAccountSerializer, responses=OpenApiTypes.OBJECT)
class OrganizerPaymentDetailsView(AdminView):
    """The organizer's default payment details, as an admin edits them."""

    def put(self, request, user_id):
        user = get_organizer(user_id)
        data = PayoutAccountSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        account = save_payout_account(user, data.validated_data, by=request.user)
        log_action(request, 'edit_payment_details', 'user', user.id, user.email, account.summary())
        return Response({'details': payout_account_payload(account)})

    def delete(self, request, user_id):
        user = get_organizer(user_id)
        PayoutAccount.objects.filter(organizer=user, gathering__isnull=True).delete()
        log_action(request, 'remove_payment_details', 'user', user.id, user.email)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SuspendSerializer(ReasonSerializer):
    takeDownEvents = serializers.BooleanField(default=False)


@extend_schema(request=SuspendSerializer, responses=OpenApiTypes.OBJECT)
class SuspendOrganizerView(AdminView):
    def post(self, request, user_id):
        user = get_organizer(user_id)
        data = SuspendSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        if user.role == 'admin':
            raise ValidationError({'detail': 'Administrators cannot be suspended here.'})
        with transaction.atomic():
            user.is_suspended = True
            user.suspended_at = timezone.now()
            user.suspension_reason = data.validated_data['reason']
            user.save(update_fields=['is_suspended', 'suspended_at', 'suspension_reason'])
            taken_down = 0
            if data.validated_data['takeDownEvents']:
                # Back to draft: hidden from the site; the listing fee stays paid for when they return
                taken_down = Gathering.objects.filter(organizer=user, status__in=['published', 'sold_out']) \
                    .update(status='draft')
            note = data.validated_data['reason'] + (f' ({taken_down} published events taken down)' if taken_down else '')
            log_action(request, 'suspend_organizer', 'user', user.id, user.email, note)
        return Response({**organizer_row(get_organizer(user.id)), 'eventsTakenDown': taken_down})


@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class ReactivateOrganizerView(AdminView):
    def post(self, request, user_id):
        user = get_organizer(user_id)
        user.is_suspended = False
        user.suspended_at = None
        user.suspension_reason = ''
        user.save(update_fields=['is_suspended', 'suspended_at', 'suspension_reason'])
        log_action(request, 'reactivate_organizer', 'user', user.id, user.email)
        return Response(organizer_row(get_organizer(user.id)))


# --- Category fees and the platform's payment details ----------------------------------------------------------

class CategoryFeeItemSerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=Gathering.CATEGORY_CHOICES)
    amount = serializers.IntegerField(min_value=0, max_value=1_000_000)


class CategoryFeesSerializer(serializers.Serializer):
    fees = CategoryFeeItemSerializer(many=True)


def category_fee_rows():
    stored = {f.category: f for f in CategoryFee.objects.select_related('updated_by')}
    awaiting = dict(ListingFee.objects.filter(status__in=['unpaid', 'pending']).values('gathering__category')
                    .annotate(n=Count('id')).values_list('gathering__category', 'n'))
    rows = []
    for category in Gathering.CATEGORIES:
        fee = stored.get(category)
        rows.append({
            'category': category,
            'amount': fee.amount if fee else 0,
            'updatedAt': fee.updated_at.isoformat() if fee else None,
            'updatedBy': fee.updated_by.email if fee and fee.updated_by else None,
            'eventsAwaitingPayment': awaiting.get(category, 0),
        })
    return rows


@extend_schema(request=CategoryFeesSerializer, responses=OpenApiTypes.OBJECT)
class CategoryFeesView(AdminView):
    def get(self, request):
        return Response({'fees': category_fee_rows()})

    def put(self, request):
        data = CategoryFeesSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        changes = []
        with transaction.atomic():
            for item in data.validated_data['fees']:
                current = CategoryFee.fee_for(item['category'])
                if current == item['amount']:
                    continue
                CategoryFee.objects.update_or_create(category=item['category'],
                                                     defaults={'amount': item['amount'], 'updated_by': request.user})
                # Events not yet listed now owe the new amount; events already paid are not affected
                refresh_category(item['category'])
                changes.append(f"{item['category']}: KSh {current:,} -> KSh {item['amount']:,}")
            if changes:
                log_action(request, 'set_category_fees', 'settings', 'category_fees', 'Category fees', '; '.join(changes))
        return Response({'fees': category_fee_rows()})


class PlatformPaymentSerializer(serializers.Serializer):
    method = serializers.ChoiceField(choices=PlatformPaymentAccount.METHOD_CHOICES)
    businessNumber = serializers.RegexField(r'^\d{5,7}$', error_messages={
        'invalid': 'A Paybill or Till number has 5 to 7 digits.'})
    accountNumber = serializers.RegexField(r'^[A-Za-z0-9 \-]{0,20}$', required=False, allow_blank=True,
                                           error_messages={'invalid': 'Up to 20 letters, digits or spaces.'})
    accountName = serializers.CharField(max_length=100)
    instructions = serializers.CharField(max_length=1000, required=False, allow_blank=True)


def platform_row(account):
    return {
        'method': account.method,
        'businessNumber': account.business_number,
        'accountNumber': account.account_number,
        'accountName': account.account_name,
        'instructions': account.instructions,
        'updatedAt': account.updated_at.isoformat() if account.updated_at else None,
        'updatedBy': account.updated_by.email if account.updated_by else None,
    }


@extend_schema(request=PlatformPaymentSerializer, responses=OpenApiTypes.OBJECT)
class PlatformPaymentView(AdminView):
    """Where organizers pay listing fees. Display details only: provider credentials stay in the environment."""

    def get(self, request):
        return Response(platform_row(PlatformPaymentAccount.load()))

    def put(self, request):
        data = PlatformPaymentSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        account = PlatformPaymentAccount.load()
        account.method = values['method']
        account.business_number = values['businessNumber']
        account.account_number = values.get('accountNumber', '').strip().upper() if values['method'] == 'paybill' else ''
        account.account_name = values['accountName'].strip()
        account.instructions = values.get('instructions', '').strip()
        account.updated_by = request.user
        account.save()
        log_action(request, 'set_platform_payment', 'settings', 'platform_payment', 'Platform payment details',
                   f'{account.get_method_display()} {account.business_number}')
        return Response(platform_row(account))


# --- Listing-fee payments -----------------------------------------------------------------------------------

def listing_fee_row(fee, table):
    g, organizer = fee.gathering, fee.gathering.organizer
    latest = fee.payments.order_by('-created_at').first()
    return {
        'eventId': str(g.id),
        'eventTitle': g.title,
        'eventStatus': g.status,
        'category': g.category,
        'organizer': {'id': str(organizer.id), 'name': organizer.first_name or organizer.email, 'email': organizer.email},
        'status': fee.status,
        'amountDue': amount_due(fee, fee_table=table),
        'categoryFee': table.get(g.category, 0),
        'amountPaid': fee.amount_paid,
        'amountWaived': fee.amount_waived,
        'reference': fee.reference,
        'paymentReference': fee.payment_reference or None,
        'paidAt': fee.paid_at.isoformat() if fee.paid_at else None,
        'createdAt': fee.created_at.isoformat(),
        'grandfathered': fee.grandfathered,
        'waivedBy': fee.waived_by.email if fee.waived_by else None,
        'waivedAt': fee.waived_at.isoformat() if fee.waived_at else None,
        'waiveReason': fee.waive_reason or None,
        'markedPaidBy': fee.marked_paid_by.email if fee.marked_paid_by else None,
        'markedPaidAt': fee.marked_paid_at.isoformat() if fee.marked_paid_at else None,
        'markPaidReason': fee.mark_paid_reason or None,
        'latestPayment': {
            'status': latest.status, 'provider': latest.provider, 'amount': money(latest.amount),
            'failureReason': latest.failure_reason or None, 'createdAt': latest.created_at.isoformat(),
        } if latest else None,
    }


@extend_schema(responses=OpenApiTypes.OBJECT)
class ListingFeeListView(AdminView):
    def get(self, request):
        params = request.query_params
        qs = ListingFee.objects.select_related('gathering__organizer', 'waived_by', 'marked_paid_by')
        if params.get('status') in dict(ListingFee.STATUS_CHOICES):
            qs = qs.filter(status=params['status'])
        if params.get('category') in Gathering.CATEGORIES:
            qs = qs.filter(gathering__category=params['category'])
        if params.get('organizerId', '').isdigit():
            qs = qs.filter(gathering__organizer_id=params['organizerId'])
        for name, lookup in (('dateFrom', 'gte'), ('dateTo', 'lte')):
            if params.get(name):
                try:
                    day = timezone.datetime.fromisoformat(params[name]).date()
                except ValueError:
                    raise ValidationError({name: 'Use a date like 2026-10-01.'})
                qs = qs.filter(**{f'created_at__date__{lookup}': day})
        search = params.get('search', '').strip()
        if search:
            qs = qs.filter(Q(gathering__title__icontains=search) | Q(gathering__organizer__email__icontains=search)
                           | Q(reference__iexact=search) | Q(payment_reference__icontains=search))
        table = fee_table()
        totals = {
            'count': qs.count(),
            'byStatus': dict(qs.values('status').annotate(n=Count('id')).values_list('status', 'n')),
            'amountPaid': qs.aggregate(t=Sum('amount_paid'))['t'] or 0,
            'amountWaived': qs.aggregate(t=Sum('amount_waived'))['t'] or 0,
            'amountUnpaid': unpaid_total(qs.filter(status__in=['unpaid', 'pending'])),
        }
        page = paginate(request, qs.order_by('-created_at'), lambda f: listing_fee_row(f, table))
        return Response({**page, 'totals': totals})


class MarkPaidSerializer(ReasonSerializer):
    reference = serializers.CharField(max_length=120, trim_whitespace=True)


def fee_response(gathering_id):
    fee = ListingFee.objects.select_related('gathering__organizer', 'waived_by', 'marked_paid_by') \
        .get(gathering_id=gathering_id)
    return Response(listing_fee_row(fee, fee_table()))


def existing_event(event_id):
    if not Gathering.objects.filter(id=event_id).exists():
        raise NotFound('No such event.')


@extend_schema(request=ReasonSerializer, responses=OpenApiTypes.OBJECT)
class WaiveListingFeeView(AdminView):
    def post(self, request, event_id):
        existing_event(event_id)
        data = ReasonSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        fee = waive_fee(event_id, request.user, data.validated_data['reason'])
        log_action(request, 'waive_listing_fee', 'event', event_id, fee.gathering.title, data.validated_data['reason'])
        return fee_response(event_id)


@extend_schema(request=MarkPaidSerializer, responses=OpenApiTypes.OBJECT)
class MarkListingFeePaidView(AdminView):
    def post(self, request, event_id):
        existing_event(event_id)
        data = MarkPaidSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        fee = mark_fee_paid(event_id, request.user, data.validated_data['reason'], data.validated_data['reference'])
        log_action(request, 'mark_listing_fee_paid', 'event', event_id, fee.gathering.title,
                   f"{data.validated_data['reason']} (ref {data.validated_data['reference']})")
        return fee_response(event_id)


# --- Payouts (Part 6) ----------------------------------------------------------------------------------------

def payout_row(p):
    return {
        'id': p.id, 'amount': money(p.amount), 'reference': p.reference, 'paidOn': p.paid_on.isoformat(),
        'note': p.note, 'sentTo': p.sent_to, 'recordedBy': p.recorded_by.email if p.recorded_by else None,
        'createdAt': p.created_at.isoformat(),
    }



class PayoutSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=1)
    reference = serializers.CharField(max_length=120, trim_whitespace=True)
    paidOn = serializers.DateField()
    note = serializers.CharField(max_length=1000, required=False, allow_blank=True)


@extend_schema(request=PayoutSerializer, responses=OpenApiTypes.OBJECT)
class OrganizerPayoutsView(AdminView):
    """Payouts made to an organizer. Record one once the money has been sent (it cannot exceed the balance)."""

    def get(self, request, user_id):
        from payouts.ledger import organizer_totals
        from payouts.models import Payout
        user = get_organizer(user_id)
        return Response({**organizer_totals(user), 'payouts': [payout_row(p) for p in Payout.objects.filter(organizer=user)]})

    def post(self, request, user_id):
        from payouts.ledger import organizer_totals
        from payouts.services import record_payout
        user = get_organizer(user_id)
        data = PayoutSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        payout = record_payout(user.id, values['amount'], values['reference'], values['paidOn'],
                               values.get('note', ''), request.user)
        log_action(request, 'record_payout', 'user', user.id, user.email,
                   f"KSh {payout.amount:,.0f}, ref {payout.reference}" + (f": {payout.note}" if payout.note else ''))
        return Response({'payout': payout_row(payout), **organizer_totals(user)}, status=status.HTTP_201_CREATED)
