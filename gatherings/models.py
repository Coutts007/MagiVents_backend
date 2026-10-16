import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone

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
    # The organizer's own website, shown as "Visit website" on the event page
    website_url = models.URLField(max_length=500, blank=True, default='')

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='published')
    is_featured = models.BooleanField(default=False)
    tags = models.JSONField(default=list, blank=True)
    curator_note = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._loaded_status = self._loaded_category = None

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        # Deferred fields (e.g. rows the delete collector loads) are not read, which would query again
        instance._loaded_status = instance.__dict__.get('status')
        instance._loaded_category = instance.__dict__.get('category')
        return instance

    @property
    def is_public(self):
        return self.status in ('published', 'sold_out')

    def save(self, *args, **kwargs):
        from . import publishing
        # Backstop: a draft (or a new event) never becomes public without passing the publish rules
        # (organizer not suspended, payment details, listing fee), whichever code path saves it
        publishing.before_save(self)
        created = self._state.adding
        previous_status, previous_category = self._loaded_status, self._loaded_category
        super().save(*args, **kwargs)
        self._loaded_status, self._loaded_category = self.status, self.category
        publishing.after_save(self, previous_status, previous_category, created)

    def __str__(self):
        return self.title

    def get_image_url(self):
        if self.artwork_image:
            return self.artwork_image.url
        return self.artwork_url or ''


class TicketTier(models.Model):
    """A ticket type the organizer sets up: VVIP, VIP, Regular, Couple, Group, Early Bird or any custom name.

    One ticket is sold at one price and admits `admits` people (2 for a couple, 5 or 10 for a group), so it
    uses that many places of the event's capacity. A flash sale lowers the price between two times, optionally
    for a limited number of tickets; `on_sale_from` / `on_sale_until` open and close the tier by itself.
    All times are stored in UTC.
    """
    MAX_ADMITS = 100

    gathering = models.ForeignKey(Gathering, on_delete=models.CASCADE, related_name='tiers')
    name = models.CharField(max_length=120)  # e.g. "VIP"
    price = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.TextField(blank=True, default='')
    # Total tickets of this type; `available` is what is left after bookings
    quantity = models.PositiveIntegerField(default=50)
    available = models.PositiveIntegerField(default=50)
    perks = models.JSONField(default=list, blank=True)
    order = models.PositiveIntegerField(default=0)
    # People one ticket admits
    admits = models.PositiveSmallIntegerField(default=1)

    # Flash sale: sale_price applies from sale_starts_at (inclusive) to sale_ends_at (exclusive),
    # for at most sale_quantity tickets when a limit is set. sale_sold counts tickets held or sold at that price.
    sale_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    sale_starts_at = models.DateTimeField(null=True, blank=True)
    sale_ends_at = models.DateTimeField(null=True, blank=True)
    sale_quantity = models.PositiveIntegerField(null=True, blank=True)
    sale_sold = models.PositiveIntegerField(default=0)

    # Optional sales window for the tier itself (e.g. Early Bird closes on its own)
    on_sale_from = models.DateTimeField(null=True, blank=True)
    on_sale_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['order', 'id']

    @property
    def sold(self):
        return max(0, self.quantity - self.available)

    @property
    def sale_remaining(self):
        """Tickets still available at the sale price, or None when the sale has no limit."""
        if self.sale_quantity is None:
            return None
        return max(0, min(self.sale_quantity - self.sale_sold, self.available))

    def sale_active(self, now=None):
        now = now or timezone.now()
        return (
            self.sale_price is not None and self.sale_starts_at is not None and self.sale_ends_at is not None
            and self.sale_starts_at <= now < self.sale_ends_at
            and (self.sale_quantity is None or self.sale_sold < self.sale_quantity)
        )

    def price_at(self, now=None):
        """(unit price in effect, whether it is the flash-sale price). The server alone decides this."""
        if self.sale_active(now):
            return self.sale_price, True
        return self.price, False

    def sales_state(self, now=None):
        """'on_sale', 'not_started' or 'ended' for the tier's own sales window."""
        now = now or timezone.now()
        if self.on_sale_from and now < self.on_sale_from:
            return 'not_started'
        if self.on_sale_until and now >= self.on_sale_until:
            return 'ended'
        return 'on_sale'

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