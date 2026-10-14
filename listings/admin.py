from django.contrib import admin

from .models import CategoryFee, ListingFee, PlatformPaymentAccount


@admin.register(CategoryFee)
class CategoryFeeAdmin(admin.ModelAdmin):
    list_display = ['category', 'amount', 'updated_at', 'updated_by']


@admin.register(PlatformPaymentAccount)
class PlatformPaymentAccountAdmin(admin.ModelAdmin):
    list_display = ['method', 'business_number', 'account_name', 'updated_at']


@admin.register(ListingFee)
class ListingFeeAdmin(admin.ModelAdmin):
    list_display = ['reference', 'gathering', 'status', 'amount_paid', 'cleared_category', 'paid_at']
    list_filter = ['status', 'grandfathered']
    search_fields = ['reference', 'gathering__title', 'gathering__organizer__email', 'payment_reference']
    readonly_fields = [f.name for f in ListingFee._meta.fields]
