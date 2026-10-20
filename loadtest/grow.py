# Clone the demo events (with tiers and agenda) until the catalog holds TARGET published events
import copy, os
from gatherings.models import Gathering, TicketTier, AgendaItem
TARGET = int(os.environ.get('TARGET', '200'))
base = list(Gathering.objects.filter(organizer__email='curator@magivents.local').order_by('id'))
n = Gathering.objects.count(); i = 0
while n < TARGET:
    src = base[i % len(base)]; i += 1
    tiers, agenda = list(src.tiers.all()), list(src.agenda_items.all())
    g = copy.copy(src); g.pk = f'{src.id}-copy{i}'; g.title = f'{src.title} #{i}'; g.save(force_insert=True)
    for t in tiers: t.pk = None; t.gathering = g; t.save()
    for a in agenda: a.pk = None; a.gathering = g; a.save()
    n += 1
print(Gathering.objects.count())
