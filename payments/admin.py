from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['reference', 'provider', 'amount', 'status', 'needs_refund', 'receipt_number', 'created_at']
    list_filter = ['provider', 'status', 'needs_refund']
    search_fields = ['reference', 'provider_reference', 'receipt_number', 'booking__attendee_email']
    readonly_fields = [f.name for f in Payment._meta.fields] + ['raw_payloads']
