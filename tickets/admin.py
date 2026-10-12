from django.contrib import admin

from .models import Booking, Bookmark, PromoCode


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ['ticket_code', 'status', 'gathering', 'attendee_email', 'quantity', 'total_price',
                    'payment_method', 'booking_date', 'checked_in_at']
    list_filter = ['status', 'payment_method']
    search_fields = ['ticket_code', 'attendee_email', 'attendee_name', 'gathering__title']
    raw_id_fields = ['user', 'gathering', 'tier', 'promo_code', 'checked_in_by']


@admin.register(PromoCode)
class PromoCodeAdmin(admin.ModelAdmin):
    """Site-wide codes (no gathering) are created here; organizers manage their own in the app."""
    list_display = ['code', 'percent_off', 'gathering', 'use_count', 'max_uses', 'valid_until', 'is_active']
    list_filter = ['is_active']
    search_fields = ['code', 'gathering__title']
    raw_id_fields = ['gathering', 'created_by']


admin.site.register(Bookmark)
