from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .authentication import user_cache_key
from .models import User


@receiver(post_save, sender=User)
@receiver(post_delete, sender=User)
def forget_cached_user(sender, instance, **kwargs):
    key = user_cache_key(instance.pk)
    cache.delete(key)
    # Again after commit, in case a request cached the old record in between
    transaction.on_commit(lambda: cache.delete(key))
