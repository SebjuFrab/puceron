from django.db import migrations


DEFAULT_SERVICE_PLANTS = [
    {
        'code': 'basilic',
        'name': 'Basilic',
        'latin_name': 'Ocimum basilicum',
        'description': 'Aromatique compagne souvent utilisee pour diversifier la serie et offrir un habitat complementaire.',
        'display_order': 10,
    },
    {
        'code': 'tagete',
        'name': 'Tagete',
        'latin_name': 'Tagetes patula',
        'description': 'Floraison compacte et longue, utile pour apporter de la ressource et de la diversite visuelle.',
        'display_order': 20,
    },
    {
        'code': 'capucine',
        'name': 'Capucine',
        'latin_name': 'Tropaeolum majus',
        'description': 'Plante compagne tres visible, souvent retenue pour son port couvrant et sa floraison marquee.',
        'display_order': 30,
    },
    {
        'code': 'coriandre',
        'name': 'Coriandre',
        'latin_name': 'Coriandrum sativum',
        'description': 'Ombellifere aromatique apportant une structure legere et une floraison interessante.',
        'display_order': 40,
    },
    {
        'code': 'aneth',
        'name': 'Aneth',
        'latin_name': 'Anethum graveolens',
        'description': 'Ombellifere fine et aerienne, frequemment associee aux dispositifs de plantes relais ou compagnes.',
        'display_order': 50,
    },
    {
        'code': 'phacelie',
        'name': 'Phacelie',
        'latin_name': 'Phacelia tanacetifolia',
        'description': 'Espece mellifere tres connue, utilisee pour structurer des bandes ou ilots de service.',
        'display_order': 60,
    },
    {
        'code': 'bourrache',
        'name': 'Bourrache',
        'latin_name': 'Borago officinalis',
        'description': 'Plante mellifere a floraison bleue, reconnaissable et facile a identifier visuellement.',
        'display_order': 70,
    },
    {
        'code': 'souci',
        'name': 'Souci',
        'latin_name': 'Calendula officinalis',
        'description': 'Floraison orange vive, souvent integree aux series pour renforcer la diversite florale.',
        'display_order': 80,
    },
]


def load_default_service_plants(apps, schema_editor):
    ServicePlant = apps.get_model('scouting', 'ServicePlant')
    for row in DEFAULT_SERVICE_PLANTS:
        ServicePlant.objects.update_or_create(
            code=row['code'],
            defaults={
                'name': row['name'],
                'latin_name': row['latin_name'],
                'description': row['description'],
                'display_order': row['display_order'],
                'is_active': True,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ('scouting', '0027_serviceplant_plantseries_has_service_plants_and_more'),
    ]

    operations = [
        migrations.RunPython(load_default_service_plants, migrations.RunPython.noop),
    ]
