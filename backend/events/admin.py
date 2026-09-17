from django.contrib import admin

# Register your models here.
# backend/events/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, Event

# Register the custom User model using Django's built-in UserAdmin
@admin.register(User)
class CustomUserAdmin(UserAdmin):
    # Display the custom 'role' field alongside standard user fields
    fieldsets = UserAdmin.fieldsets + (
        ('Platform Role', {'fields': ('role',)}),
    )
    list_display = ('email', 'first_name', 'last_name', 'role', 'is_staff')

# Register the Event model
@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'organizer', 'status', 'start_time', 'location')
    list_filter = ('status', 'start_time')
    search_fields = ('title', 'location')


# Registering Ticketing and Order Models in the Admin Interface
from .models import TicketTier, Order, OrderItem, Ticket

@admin.register(TicketTier)
class TicketTierAdmin(admin.ModelAdmin):
    list_display = ('name', 'event', 'price', 'available_quantity', 'total_quantity')
    list_filter = ('event',)
    search_fields = ('name', 'event__title')

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'attendee', 'status', 'total_amount', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('id', 'attendee__email', 'payment_intent_id')

@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'ticket_tier', 'quantity', 'unit_price')

@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ('id', 'status', 'order_item', 'checked_in_at')
    list_filter = ('status',)
    search_fields = ('qr_code_hash', 'id')