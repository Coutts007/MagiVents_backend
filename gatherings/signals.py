from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from . import catalog
from .models import AgendaItem, Gathering, TicketTier

# Saves that only move ticket counts happen on every booking. They are left to the cache's short lifetime instead
# of starting a new version, or the cache would be emptied by each sale. A status-only save (publish, unpublish)
# is an edit and does start one.
COUNTER_FIELDS = {'attendee_count', 'status', 'available', 'sale_sold'}


@receiver(post_save, sender=Gathering)
@receiver(post_save, sender=TicketTier)
@receiver(post_save, sender=AgendaItem)
def catalog_changed(sender, update_fields=None, **kwargs):
    if update_fields and set(update_fields) <= COUNTER_FIELDS and {'attendee_count', 'available'} & set(update_fields):
        return
    # After commit, so a request running meanwhile cannot cache the old version under the new one
    transaction.on_commit(catalog.invalidate)


@receiver(post_delete, sender=Gathering)
@receiver(post_delete, sender=TicketTier)
@receiver(post_delete, sender=AgendaItem)
def catalog_deleted(sender, **kwargs):
    transaction.on_commit(catalog.invalidate)
