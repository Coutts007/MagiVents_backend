from django.contrib import admin
from .models import Gathering, TicketTier, AgendaItem

@admin.register(Gathering)
class GatheringAdmin(admin.ModelAdmin):
    list_display = ['title', 'venue_city', 'iso_date', 'organizer', 'starting_price', 'status', 'is_featured']
    search_fields = ['title', 'venue_city', 'venue_name']
    list_filter = ['category', 'status', 'is_featured', 'iso_date']

admin.site.register(TicketTier)
admin.site.register(AgendaItem)