import uuid
from django.conf import settings
from django.db import models

class Gathering(models.Model):
    CATEGORIES = [
        'Sports & Outdoors',
        'Entertainment & Music',
        'Business & Entrepreneurship',
        'Tech & Innovation',
        'Education & Career',
        'Arts & Culture',
        'Social Impact & Community',
        'Political',
        'Others',
    ]
    CATEGORY_CHOICES = tuple((c, c) for c in CATEGORIES)
    STATUS_CHOICES = (
        ('published', 'Published'),
        ('draft', 'Draft'),
        ('sold_out', 'Sold Out'),
    )

    id = models.CharField(primary_key=True, max_length=100, default=uuid.uuid4, editable=False)
    organizer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='organized_gatherings')
    title = models.CharField(max_length=255)
    subtitle = models.CharField(max_length=300, blank=True, default='')
    category = models.CharField(max_length=60, choices=CATEGORY_CHOICES)
    description = models.TextField()
    full_content = models.TextField(blank=True, default='')
    
    date_display = models.CharField(max_length=120)  # e.g., "Saturday, 28 Nov 2026"
    iso_date = models.DateField()
    time_display = models.CharField(max_length=120)  # e.g., "19:00 — 22:30"
    
    # Venue
    venue_name = models.CharField(max_length=255)
    venue_address = models.CharField(max_length=255, blank=True, default='')
    venue_city = models.CharField(max_length=120, blank=True, default='')
    venue_neighborhood = models.CharField(max_length=120, blank=True, default='')
    venue_map_note = models.TextField(blank=True, default='')
    venue_lat = models.FloatField(null=True, blank=True)
    venue_lng = models.FloatField(null=True, blank=True)

    # Capacity & Pricing
    # All prices are in Kenyan shillings
    is_free = models.BooleanField(default=False)
    starting_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    currency = models.CharField(max_length=10, default='KES')
    capacity = models.PositiveIntegerField(default=100)
    attendee_count = models.PositiveIntegerField(default=0)

    # Editorial Artwork
    artwork_image = models.ImageField(upload_to='gatherings/artwork/', null=True, blank=True)
    artwork_url = models.CharField(max_length=500, blank=True, default='')

    # Host Identity
    host_name = models.CharField(max_length=120, blank=True, default='')
    host_role = models.CharField(max_length=120, blank=True, default='')
    host_avatar_url = models.CharField(max_length=500, blank=True, default='')
    host_bio = models.TextField(blank=True, default='')

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='published')
    is_featured = models.BooleanField(default=False)
    tags = models.JSONField(default=list, blank=True)
    curator_note = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

    def get_image_url(self):
        if self.artwork_image:
            return self.artwork_image.url
        return self.artwork_url or ''


class TicketTier(models.Model):
    gathering = models.ForeignKey(Gathering, on_delete=models.CASCADE, related_name='tiers')
    name = models.CharField(max_length=120)  # e.g. "VIP"
    price = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.TextField(blank=True, default='')
    available = models.PositiveIntegerField(default=50)
    perks = models.JSONField(default=list, blank=True)

    def __str__(self):
        return f"{self.name} - {self.gathering.title}"


class AgendaItem(models.Model):
    gathering = models.ForeignKey(Gathering, on_delete=models.CASCADE, related_name='agenda_items')
    time = models.CharField(max_length=50)   # e.g. "19:00"
    title = models.CharField(max_length=200) # e.g. "Keynote address"
    detail = models.TextField(blank=True, default='')
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.time}: {self.title}"