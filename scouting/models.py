from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.utils import timezone
from wagtail import blocks
from modelcluster.fields import ParentalKey
from wagtail.admin.panels import FieldPanel, InlinePanel
from wagtail.contrib.settings.models import BaseSiteSetting, register_setting
from wagtail.fields import RichTextField, StreamField
from wagtail.images.blocks import ImageChooserBlock
from wagtail.models import Orderable, Page
from wagtail.search import index
from .utils import display_user_name

DEPARTMENT_CHOICES = [
    ('85', 'Vendee (85)'),
    ('56', 'Morbihan (56)'),
    ('44', 'Loire-Atlantique (44)'),
    ('35', 'Ille-et-Vilaine (35)'),
]

CROP_CHOICES = [
    ('aubergine', 'Aubergine'),
    ('concombre', 'Concombre'),
]

AUXILIARY_MODE_CHOICES = [
    ('total', 'Abondance totale'),
    ('detailed', 'Detail par type'),
]

# Legacy list kept for backward compatibility with existing table AuxiliaryCount.
AUXILIARY_TYPE_CHOICES = [
    ('syrphe', 'Syrphe'),
    ('coccinelle', 'Coccinelle'),
    ('chrysope', 'Chrysope'),
    ('parasitoide', 'Parasitoide'),
    ('autre', 'Autre'),
]

AUXILIARY_SPECIES = [
    ('syrphes', 'Syrphes'),
    ('anthocorides', 'Punaises Anthocorides'),
    ('nabides', 'Punaises Nabides'),
    ('mirides', 'Punaises Mirides'),
    ('parasitoides', 'Hymenopteres parasitoides adultes'),
    ('coccinelles', 'Coccinelles'),
    ('chrysopes_hemerobes', 'Chrysopes et Hemerobes'),
    ('cecidiomyies', 'Cecidomyies predatrices'),
    ('araignees', 'Araignees adultes'),
]

LEGACY_LEAF_AUX_FIELDS = [
    'syrphes',
    'anthocorides',
    'nabides',
    'mirides',
    'parasitoides',
    'coccinelles',
    'chrysopes_hemerobes',
    'cecidiomyies',
    'araignees',
]

LEAF_POSITION_CHOICES = [
    ('low', 'Basse'),
    ('mid', 'Milieu'),
    ('high', 'Haute'),
]

ORGANIC_MODE_CHOICES = [
    ('bio', 'Bio (AB)'),
    ('non_bio', 'Non bio'),
]

ACTION_CATEGORY_CHOICES = [
    ('manual', 'Manuelle'),
    ('treatment', 'Traitement'),
    ('release', "Lacher d'auxiliaire"),
]

ACTION_ICON_CHOICES = [
    ('triangle', 'Triangle'),
    ('circle', 'Cercle'),
    ('rectRot', 'Losange'),
    ('rectRounded', 'Rectangle'),
    ('star', 'Etoile'),
    ('crossRot', 'Croix'),
]

ACTION_SCOPE_CHOICES = [
    ('localized', 'Localisee'),
    ('general', 'Generalisee'),
]

RECOMMENDATION_STATUS_CHOICES = [
    ('followed', 'Suivie'),
    ('dismissed', 'Non suivie'),
]

MOLECULE_ORGANIC_SCOPE_CHOICES = [
    ('bio', 'Bio (AB)'),
    ('non_bio', 'Non bio'),
    ('both', 'Bio et non bio'),
]

DECISION_AUX_METRIC_CHOICES = [
    ('per_plant', 'Auxiliaires / plant'),
    ('per_observed_leaf', 'Auxiliaires / feuille observee'),
    ('per_infested_leaf', 'Auxiliaires / feuille infestee'),
]

INFO_PAGE_KEY_CHOICES = [
    ('protocol', 'Protocole'),
    ('techniques', 'Techniques de lutte'),
    ('auxiliaries', 'Auxiliaires'),
]


class LogoItemBlock(blocks.StructBlock):
    image = ImageChooserBlock(required=True, label='Logo')
    alt = blocks.CharBlock(required=False, label='Texte alternatif')
    url = blocks.URLBlock(required=False, label='Lien')

    class Meta:
        icon = 'image'
        label = 'Logo'


def current_campaign_year():
    return timezone.localdate().year


class UserProfile(models.Model):
    ROLE_PRODUCER = 'producer'
    ROLE_TECHNICIAN = 'technician'
    ROLE_CHOICES = [
        (ROLE_PRODUCER, 'Producteur'),
        (ROLE_TECHNICIAN, 'Technicien'),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
        verbose_name='Utilisateur',
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_PRODUCER, verbose_name='Role')
    assigned_technician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_producer_profiles',
        verbose_name='Technicien referent',
    )
    department = models.CharField(max_length=2, choices=DEPARTMENT_CHOICES, blank=True, verbose_name='Departement')
    farm_name = models.CharField(max_length=150, blank=True, verbose_name='Nom de ferme')
    phone = models.CharField(max_length=30, blank=True, verbose_name='Mobile')
    photo = models.ImageField(upload_to='profile_photos/', blank=True, verbose_name='Photo ou logo')
    farm_address = models.TextField(blank=True, verbose_name='Adresse complete')
    street_address = models.CharField(max_length=255, blank=True, verbose_name='Adresse')
    postal_code = models.CharField(max_length=10, blank=True, verbose_name='Code postal')
    city = models.CharField(max_length=120, blank=True, verbose_name='Commune')
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, verbose_name='Latitude')
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, verbose_name='Longitude')
    crops_grown = models.CharField(max_length=255, blank=True, verbose_name='Cultures suivies')
    tracked_plants = models.TextField(blank=True, verbose_name='Plants suivis')

    class Meta:
        verbose_name = 'Profil utilisateur'
        verbose_name_plural = 'Profils utilisateurs'

    def __str__(self):
        return f'{display_user_name(self.user)} ({self.get_role_display()})'

    @property
    def full_address(self):
        parts = [self.street_address, self.postal_code, self.city]
        return ', '.join([part for part in parts if part])

    def sync_profile_fields(self):
        if self.user_id and self.user.is_superuser:
            self.assigned_technician = None

        full_address = self.full_address
        if full_address:
            self.farm_address = full_address
        elif not self.street_address and not self.postal_code and not self.city:
            self.farm_address = ''

        if self.role == self.ROLE_PRODUCER and self.assigned_technician_id:
            technician_profile = UserProfile.objects.get_or_create(user=self.assigned_technician)[0]
            if technician_profile and technician_profile.department:
                self.department = technician_profile.department

    def save(self, *args, **kwargs):
        self.sync_profile_fields()
        super().save(*args, **kwargs)


class InfoPage(models.Model):
    page_key = models.CharField(max_length=30, choices=INFO_PAGE_KEY_CHOICES, unique=True, verbose_name='Page')
    title = models.CharField(max_length=160, verbose_name='Titre')
    intro = models.TextField(blank=True, verbose_name='Introduction')
    content = models.TextField(blank=True, verbose_name='Contenu')
    is_published = models.BooleanField(default=True, verbose_name='Publiee')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Mise a jour le')

    class Meta:
        ordering = ['page_key']
        verbose_name = "Page d'information"
        verbose_name_plural = "Pages d'information"

    def __str__(self):
        return self.title

    def get_page_key_display_label(self):
        return dict(INFO_PAGE_KEY_CHOICES).get(self.page_key, self.page_key)


class InfoResource(models.Model):
    page = models.ForeignKey(InfoPage, on_delete=models.CASCADE, related_name='resources', verbose_name='Page')
    title = models.CharField(max_length=160, verbose_name='Titre')
    description = models.TextField(blank=True, verbose_name='Description')
    file = models.FileField(upload_to='info_resources/', blank=True, verbose_name='Fichier')
    external_url = models.URLField(blank=True, verbose_name='Lien externe')
    display_order = models.PositiveSmallIntegerField(default=1, verbose_name="Ordre d'affichage")

    class Meta:
        ordering = ['display_order', 'title']
        verbose_name = 'Ressource'
        verbose_name_plural = 'Ressources'

    def __str__(self):
        return self.title

    def clean(self):
        has_file = bool(self.file)
        has_url = bool(self.external_url)
        if not has_file and not has_url:
            raise ValidationError({'__all__': 'Ajoutez un fichier PDF ou un lien externe.'})
        if has_file and has_url:
            raise ValidationError({'__all__': 'Choisissez soit un fichier, soit un lien externe, pas les deux.'})


class InfoIndexPage(Page):
    intro = RichTextField(blank=True, verbose_name='Introduction')

    max_count = 1
    subpage_types = ['scouting.InfoContentPage']
    parent_page_types = ['wagtailcore.Page']
    template = 'scouting/info_index.html'

    content_panels = Page.content_panels + [
        FieldPanel('intro'),
    ]

    class Meta:
        verbose_name = "Index des pages d'information"
        verbose_name_plural = "Index des pages d'information"

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        context['pages'] = self.get_children().live().specific().order_by('path')
        return context


class InfoContentPage(Page):
    page_key = models.CharField(
        max_length=30,
        choices=INFO_PAGE_KEY_CHOICES,
        unique=True,
        null=True,
        blank=True,
        verbose_name='Code de page',
    )
    intro = RichTextField(blank=True, verbose_name='Introduction')
    body = RichTextField(blank=True, verbose_name='Contenu')

    parent_page_types = ['scouting.InfoIndexPage']
    subpage_types = []
    template = 'scouting/info_page.html'

    search_fields = Page.search_fields + [
        index.SearchField('title'),
        index.SearchField('intro'),
        index.SearchField('body'),
    ]

    content_panels = Page.content_panels + [
        FieldPanel('page_key'),
        FieldPanel('intro'),
        FieldPanel('body'),
        InlinePanel('resources', label='Ressources'),
    ]

    class Meta:
        verbose_name = "Page d'information CMS"
        verbose_name_plural = "Pages d'information CMS"

    def __str__(self):
        return self.title

    def get_page_key_display_label(self):
        return dict(INFO_PAGE_KEY_CHOICES).get(self.page_key, self.page_key or self.slug)


class InfoContentPageResource(Orderable):
    page = ParentalKey(
        'scouting.InfoContentPage',
        on_delete=models.CASCADE,
        related_name='resources',
        verbose_name='Page',
    )
    title = models.CharField(max_length=160, verbose_name='Titre')
    description = models.TextField(blank=True, verbose_name='Description')
    document = models.ForeignKey(
        'wagtaildocs.Document',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        verbose_name='Document',
    )
    external_url = models.URLField(blank=True, verbose_name='Lien externe')

    panels = [
        FieldPanel('title'),
        FieldPanel('description'),
        FieldPanel('document'),
        FieldPanel('external_url'),
    ]

    class Meta:
        ordering = ['sort_order', 'title']
        verbose_name = 'Ressource CMS'
        verbose_name_plural = 'Ressources CMS'

    def __str__(self):
        return self.title

    def clean(self):
        has_document = bool(self.document_id)
        has_url = bool(self.external_url)
        if not has_document and not has_url:
            raise ValidationError({'__all__': 'Ajoutez un document ou un lien externe.'})
        if has_document and has_url:
            raise ValidationError({'__all__': 'Choisissez soit un document, soit un lien externe, pas les deux.'})


@register_setting(icon='cogs')
class SiteContentSettings(BaseSiteSetting):
    favicon = models.ForeignKey(
        'wagtailimages.Image',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        verbose_name='Favicon',
        help_text='Icône du site affichée dans l’onglet du navigateur et sur l’écran d’accueil.',
    )
    funders_title = models.CharField(max_length=160, default='Financeurs', verbose_name='Titre financeurs')
    funders_text = RichTextField(blank=True, verbose_name='Texte financeurs')
    funders_logos = StreamField(
        [('logo', LogoItemBlock())],
        blank=True,
        use_json_field=True,
        verbose_name='Logos financeurs',
    )
    footer_title = models.CharField(max_length=160, default='Bas de page', verbose_name='Titre footer')
    footer_text = RichTextField(blank=True, verbose_name='Texte footer')
    footer_logos = StreamField(
        [('logo', LogoItemBlock())],
        blank=True,
        use_json_field=True,
        verbose_name='Logos footer',
    )

    panels = [
        FieldPanel('favicon'),
        FieldPanel('funders_title'),
        FieldPanel('funders_text'),
        FieldPanel('funders_logos'),
        FieldPanel('footer_title'),
        FieldPanel('footer_text'),
        FieldPanel('footer_logos'),
    ]

    class Meta:
        verbose_name = 'Contenus globaux du site'
        verbose_name_plural = 'Contenus globaux du site'


class Crop(models.Model):
    name = models.CharField(max_length=100, unique=True, verbose_name='Nom')
    decision_aux_metric = models.CharField(
        max_length=30,
        choices=DECISION_AUX_METRIC_CHOICES,
        default='per_plant',
        verbose_name='Indicateur auxiliaires pour la decision',
    )
    is_active = models.BooleanField(default=True, verbose_name='Actif')

    class Meta:
        ordering = ['name']
        verbose_name = 'Culture'
        verbose_name_plural = 'Cultures'

    def __str__(self):
        return self.name


class ConductType(models.Model):
    name = models.CharField(max_length=100, unique=True, verbose_name='Nom')
    is_active = models.BooleanField(default=True, verbose_name='Actif')

    class Meta:
        ordering = ['name']
        verbose_name = 'Type de conduite'
        verbose_name_plural = 'Types de conduite'

    def __str__(self):
        return self.name


class Variety(models.Model):
    crop = models.ForeignKey(Crop, on_delete=models.CASCADE, related_name='varieties', verbose_name='Culture')
    name = models.CharField(max_length=120, verbose_name='Nom')
    is_active = models.BooleanField(default=True, verbose_name='Active')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_varieties',
        verbose_name='Creee par',
    )

    class Meta:
        ordering = ['name']
        verbose_name = 'Variete'
        verbose_name_plural = 'Varietes'
        constraints = [
            models.UniqueConstraint(fields=['crop', 'name'], name='unique_variety_per_crop')
        ]

    def __str__(self):
        return f'{self.crop.name} - {self.name}'


class PlantSeries(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='plant_series',
        verbose_name='Producteur',
    )
    name = models.CharField(max_length=120, verbose_name='Nom de la serie')
    photo = models.ImageField(upload_to='plant_series/', blank=True, verbose_name='Photo')
    crop = models.ForeignKey(Crop, on_delete=models.PROTECT, related_name='plant_series', verbose_name='Culture')
    conduct_type = models.ForeignKey(
        ConductType,
        on_delete=models.PROTECT,
        related_name='plant_series',
        verbose_name='Conduite',
    )
    organic_mode = models.CharField(max_length=10, choices=ORGANIC_MODE_CHOICES, default='bio', verbose_name='Mode de conduite')
    variety = models.ForeignKey(Variety, on_delete=models.PROTECT, related_name='plant_series', verbose_name='Variete')
    greenhouse = models.CharField(max_length=150, blank=True, verbose_name='Serre')
    year = models.PositiveSmallIntegerField(default=current_campaign_year, verbose_name='Annee')
    planting_week = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(53)],
        verbose_name='Numero de la semaine de plantation',
    )
    plants_count = models.PositiveSmallIntegerField(default=10, verbose_name='Nombre de plants')
    leaves_per_plant = models.PositiveSmallIntegerField(default=3, verbose_name='Nombre de feuilles par plant')
    is_active = models.BooleanField(default=True, verbose_name='Active')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creee le')

    class Meta:
        ordering = ['name']
        verbose_name = 'Serie de plants'
        verbose_name_plural = 'Series de plants'
        constraints = [
            models.UniqueConstraint(fields=['user', 'year', 'name'], name='unique_series_name_per_user_year')
        ]

    def __str__(self):
        return f'{self.name} ({display_user_name(self.user)})'


class ScoutingRecord(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='records',
        verbose_name='Producteur',
    )
    plant_series = models.ForeignKey(
        PlantSeries,
        on_delete=models.PROTECT,
        related_name='records',
        null=True,
        blank=True,
        verbose_name='Serie de plants',
    )
    crop_ref = models.ForeignKey(
        Crop,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='records',
        verbose_name='Culture',
    )
    conduct_type_ref = models.ForeignKey(
        ConductType,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='records',
        verbose_name='Conduite',
    )
    variety_ref = models.ForeignKey(
        Variety,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='records',
        verbose_name='Variete',
    )
    department = models.CharField(max_length=2, choices=DEPARTMENT_CHOICES, verbose_name='Departement')
    crop = models.CharField(max_length=100, verbose_name='Culture')
    scouting_date = models.DateField(default=timezone.localdate, verbose_name='Date observation')
    year = models.PositiveSmallIntegerField(verbose_name='Annee')
    week = models.PositiveSmallIntegerField(verbose_name='Semaine')
    aphid_infested_percent = models.DecimalField(max_digits=5, decimal_places=2, verbose_name='% feuilles infestees')
    primary_aphid_species = models.ForeignKey(
        'AphidSpecies',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='primary_records',
        verbose_name='Espece principale de puceron',
    )
    auxiliary_mode = models.CharField(max_length=10, choices=AUXILIARY_MODE_CHOICES, default='total', verbose_name='Mode auxiliaires')
    auxiliary_total = models.PositiveIntegerField(default=0, verbose_name='Total auxiliaires')
    comment = models.TextField(blank=True, verbose_name='Commentaire')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Cree le')

    class Meta:
        ordering = ['-year', '-week', '-created_at']
        verbose_name = 'Comptage'
        verbose_name_plural = 'Comptages'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'plant_series', 'year', 'week'],
                name='unique_record_per_user_crop_week',
            )
        ]

    def __str__(self):
        return f'{display_user_name(self.user)} - {self.crop} S{self.week}/{self.year}'

    @property
    def auxiliaries_per_plant(self):
        plants_count = self.plant_series.plants_count if self.plant_series_id and self.plant_series else 10
        plants_count = plants_count or 10
        return self.auxiliary_total / plants_count

    @property
    def risk_level(self):
        if self.aphid_infested_percent > 10 and self.auxiliaries_per_plant <= 1:
            return 'Eleve'
        if self.aphid_infested_percent > 10:
            return 'Modere'
        return 'Faible'

    def recompute_from_leaf_observations(self):
        leaves = list(self.leaf_observations.all())
        if not leaves:
            self.aphid_infested_percent = 0
            self.auxiliary_total = 0
            self.auxiliary_mode = 'detailed'
            self.primary_aphid_species = None
            self.save(update_fields=['aphid_infested_percent', 'auxiliary_total', 'auxiliary_mode', 'primary_aphid_species'])
            return

        infested_count = sum(1 for leaf in leaves if leaf.aphid_present)
        total_aux = (
            LeafAuxiliaryObservation.objects.filter(leaf_observation__record=self).aggregate(total=Sum('count'))[
                'total'
            ]
            or 0
        )
        if total_aux == 0:
            total_aux = sum(leaf.total_auxiliaries for leaf in leaves)
        self.aphid_infested_percent = round((infested_count / len(leaves)) * 100, 2)
        self.auxiliary_total = total_aux
        self.auxiliary_mode = 'detailed'
        observed_species_ids = self.observed_aphid_species_ids()
        if len(observed_species_ids) == 1:
            self.primary_aphid_species_id = observed_species_ids[0]
        elif observed_species_ids and self.primary_aphid_species_id not in observed_species_ids:
            self.primary_aphid_species = None
        elif not observed_species_ids:
            self.primary_aphid_species = None
        self.save(
            update_fields=[
                'aphid_infested_percent',
                'auxiliary_total',
                'auxiliary_mode',
                'primary_aphid_species',
            ]
        )

    def observed_aphid_species_ids(self):
        return list(
            self.leaf_observations.filter(aphid_present=True, aphid_species__isnull=False)
            .values_list('aphid_species_id', flat=True)
            .distinct()
        )

    def species_means_per_plant(self):
        taxa = list(AuxiliaryTaxon.objects.order_by('display_order', 'name'))
        totals = {taxon.id: 0 for taxon in taxa}
        rows = (
            LeafAuxiliaryObservation.objects.filter(leaf_observation__record=self)
            .values('taxon_id')
            .annotate(total=Sum('count'))
        )
        for row in rows:
            totals[row['taxon_id']] = row['total'] or 0
        if not rows:
            for key, _ in AUXILIARY_SPECIES:
                legacy_total = sum(getattr(leaf, key) for leaf in self.leaf_observations.all())
                taxon = next((item for item in taxa if item.code == key), None)
                if taxon:
                    totals[taxon.id] = legacy_total
        return {taxon.id: round(totals[taxon.id] / 10.0, 2) for taxon in taxa}


class AuxiliaryTaxon(models.Model):
    code = models.SlugField(max_length=40, unique=True, verbose_name='Code')
    name = models.CharField(max_length=120, verbose_name='Nom')
    photo = models.ImageField(upload_to='auxiliary_taxa/', blank=True, verbose_name='Photo')
    display_order = models.PositiveSmallIntegerField(default=1, verbose_name="Ordre d'affichage")
    is_releasable = models.BooleanField(default=False, verbose_name='Lachable')
    is_active = models.BooleanField(default=True, verbose_name='Actif')

    class Meta:
        ordering = ['display_order', 'name']
        verbose_name = 'Auxiliaire'
        verbose_name_plural = 'Auxiliaires'

    def __str__(self):
        return self.name


class AphidSpecies(models.Model):
    code = models.SlugField(max_length=50, unique=True, verbose_name='Code')
    vernacular_name = models.CharField(max_length=140, verbose_name='Nom vernaculaire')
    latin_name = models.CharField(max_length=140, blank=True, verbose_name='Nom latin')
    photo = models.ImageField(upload_to='aphid_species/', blank=True, verbose_name='Photo')
    molecules = models.ManyToManyField(
        'Molecule',
        related_name='aphid_species',
        blank=True,
        verbose_name='Molecules de lutte',
    )
    auxiliary_taxa = models.ManyToManyField(
        AuxiliaryTaxon,
        related_name='target_aphid_species',
        blank=True,
        verbose_name='Auxiliaires de lutte',
    )
    description = models.TextField(blank=True, verbose_name='Description')
    display_order = models.PositiveSmallIntegerField(default=1, verbose_name="Ordre d'affichage")
    is_active = models.BooleanField(default=True, verbose_name='Actif')

    class Meta:
        ordering = ['display_order', 'vernacular_name', 'latin_name']
        verbose_name = 'Espece de puceron'
        verbose_name_plural = 'Especes de pucerons'

    def __str__(self):
        if self.latin_name and self.latin_name != self.vernacular_name:
            return f'{self.vernacular_name} ({self.latin_name})'
        return self.vernacular_name


class ActionType(models.Model):
    name = models.CharField(max_length=120, unique=True, verbose_name='Nom')
    category = models.CharField(max_length=20, choices=ACTION_CATEGORY_CHOICES, default='manual', verbose_name='Categorie')
    chart_icon = models.CharField(
        max_length=20,
        choices=ACTION_ICON_CHOICES,
        blank=True,
        verbose_name='Icone graphique',
    )
    display_order = models.PositiveSmallIntegerField(default=1, verbose_name="Ordre d'affichage")
    is_active = models.BooleanField(default=True, verbose_name='Actif')

    class Meta:
        ordering = ['display_order', 'name']
        verbose_name = "Type d'action"
        verbose_name_plural = "Types d'action"

    def __str__(self):
        return self.name

    @property
    def resolved_chart_icon(self):
        if self.chart_icon:
            return self.chart_icon
        return {
            'manual': 'triangle',
            'treatment': 'rectRot',
            'release': 'star',
        }.get(self.category, 'circle')

    @property
    def chart_icon_symbol(self):
        return {
            'triangle': '▲',
            'circle': '●',
            'rectRot': '◆',
            'rectRounded': '■',
            'star': '★',
            'crossRot': '✚',
        }.get(self.resolved_chart_icon, '●')


class Molecule(models.Model):
    name = models.CharField(max_length=140, verbose_name='Nom')
    crops = models.ManyToManyField(Crop, related_name='molecules', blank=True, verbose_name='Cultures')
    organic_scope = models.CharField(max_length=10, choices=MOLECULE_ORGANIC_SCOPE_CHOICES, default='both', verbose_name='Portee bio/non bio')
    is_active = models.BooleanField(default=True, verbose_name='Active')

    class Meta:
        ordering = ['name']
        verbose_name = 'Molecule'
        verbose_name_plural = 'Molecules'
        constraints = [
            models.UniqueConstraint(fields=['name', 'organic_scope'], name='unique_molecule_per_scope')
        ]

    def __str__(self):
        return self.name


class PlantAction(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='plant_actions',
        verbose_name='Producteur',
    )
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='entered_plant_actions',
        verbose_name='Saisie par',
    )
    plant_series = models.ForeignKey(PlantSeries, on_delete=models.PROTECT, related_name='actions', verbose_name='Serie de plants')
    department = models.CharField(max_length=2, choices=DEPARTMENT_CHOICES, verbose_name='Departement')
    crop_ref = models.ForeignKey(Crop, on_delete=models.PROTECT, related_name='actions', verbose_name='Culture')
    conduct_type_ref = models.ForeignKey(
        ConductType,
        on_delete=models.PROTECT,
        related_name='actions',
        null=True,
        blank=True,
        verbose_name='Conduite',
    )
    variety_ref = models.ForeignKey(
        Variety,
        on_delete=models.PROTECT,
        related_name='actions',
        null=True,
        blank=True,
        verbose_name='Variete',
    )
    action_date = models.DateField(default=timezone.localdate, verbose_name="Date d'action")
    action_type = models.ForeignKey(ActionType, on_delete=models.PROTECT, related_name='actions', verbose_name="Type d'action")
    scope = models.CharField(max_length=12, choices=ACTION_SCOPE_CHOICES, default='general', verbose_name='Portee')
    molecule = models.ForeignKey(
        Molecule,
        on_delete=models.PROTECT,
        related_name='actions',
        null=True,
        blank=True,
        verbose_name='Molecule',
    )
    auxiliary_taxon = models.ForeignKey(
        AuxiliaryTaxon,
        on_delete=models.PROTECT,
        related_name='release_actions',
        null=True,
        blank=True,
        limit_choices_to={'is_releasable': True},
        verbose_name='Auxiliaire lache',
    )
    decision_lever = models.ForeignKey(
        'DecisionLever',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='actions',
        verbose_name='Levier active',
    )
    notes = models.TextField(blank=True, verbose_name='Details')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creee le')

    class Meta:
        ordering = ['-action_date', '-created_at']
        verbose_name = 'Action preventive ou curative'
        verbose_name_plural = 'Actions preventives ou curatives'

    def clean(self):
        errors = {}
        category = self.action_type.category if self.action_type_id else None
        if category == 'treatment':
            if not self.molecule_id:
                errors['molecule'] = 'Choisissez une molecule pour un traitement.'
        elif self.molecule_id:
            errors['molecule'] = 'La molecule est reservee aux actions de type traitement.'
        if category == 'release':
            if not self.auxiliary_taxon_id:
                errors['auxiliary_taxon'] = "Choisissez un auxiliaire pour un lacher."
            elif not self.auxiliary_taxon.is_releasable:
                errors['auxiliary_taxon'] = "Cet auxiliaire n'est pas marque comme lachable."
        elif self.auxiliary_taxon_id:
            errors['auxiliary_taxon'] = "L'auxiliaire est reserve au type lacher."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.plant_series.name} - {self.action_type.name} ({self.action_date.isoformat()})'


class DecisionRule(models.Model):
    crop = models.ForeignKey(Crop, on_delete=models.CASCADE, related_name='decision_rules', verbose_name='Culture')
    title = models.CharField(max_length=160, verbose_name='Titre')
    description = models.TextField(verbose_name='Description')
    week_min = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='Semaine min incluse')
    week_max = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='Semaine max incluse')
    infestation_min = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Infestation min incluse (%)",
    )
    infestation_max = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Infestation max exclue (%)",
    )
    auxiliary_min = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Auxiliaires min inclus',
    )
    auxiliary_max = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Auxiliaires max exclu',
    )
    priority = models.PositiveSmallIntegerField(default=100, verbose_name='Priorite')
    is_active = models.BooleanField(default=True, verbose_name='Active')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creee le')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Mise a jour le')

    class Meta:
        ordering = ['crop__name', 'priority', 'title']
        verbose_name = 'Regle de decision'
        verbose_name_plural = 'Regles de decision'

    def __str__(self):
        return f'{self.crop.name} - {self.title}'

    @property
    def crop_aux_metric(self):
        return self.crop.decision_aux_metric

    @property
    def week_min_effective(self):
        return self.week_min if self.week_min is not None else 1

    @property
    def week_max_effective(self):
        return self.week_max if self.week_max is not None else 53

    @property
    def infestation_min_effective(self):
        return self.infestation_min if self.infestation_min is not None else Decimal('0')

    @property
    def infestation_max_effective(self):
        return self.infestation_max

    @property
    def auxiliary_min_effective(self):
        return self.auxiliary_min if self.auxiliary_min is not None else Decimal('0')

    @property
    def auxiliary_max_effective(self):
        return self.auxiliary_max

    def _overlaps_inclusive(self, min_a, max_a, min_b, max_b):
        return min_a <= max_b and min_b <= max_a

    def _overlaps_left_closed_right_open(self, min_a, max_a, min_b, max_b):
        if max_a is None and max_b is None:
            return True
        if max_a is None:
            return min_a < max_b
        if max_b is None:
            return min_b < max_a
        return min_a < max_b and min_b < max_a

    def conflicting_active_rules(self):
        if not self.is_active or not self.crop_id:
            return self.__class__.objects.none()

        qs = self.__class__.objects.filter(crop=self.crop, is_active=True).exclude(pk=self.pk)
        conflicts = []
        for other in qs:
            same_weeks = self._overlaps_inclusive(
                self.week_min_effective,
                self.week_max_effective,
                other.week_min_effective,
                other.week_max_effective,
            )
            same_infestation = self._overlaps_left_closed_right_open(
                self.infestation_min_effective,
                self.infestation_max_effective,
                other.infestation_min_effective,
                other.infestation_max_effective,
            )
            same_aux = self._overlaps_left_closed_right_open(
                self.auxiliary_min_effective,
                self.auxiliary_max_effective,
                other.auxiliary_min_effective,
                other.auxiliary_max_effective,
            )
            if same_weeks and same_infestation and same_aux:
                conflicts.append(other.pk)
        return self.__class__.objects.filter(pk__in=conflicts)

    def clean(self):
        errors = {}
        if self.week_min is not None and self.week_min < 1:
            errors['week_min'] = 'La semaine min doit etre comprise entre 1 et 53.'
        if self.week_max is not None and self.week_max > 53:
            errors['week_max'] = 'La semaine max doit etre comprise entre 1 et 53.'
        if self.week_min is not None and self.week_max is not None and self.week_min > self.week_max:
            errors['week_max'] = 'La semaine max doit etre superieure ou egale a la semaine min.'
        if self.infestation_min is not None and self.infestation_min < 0:
            errors['infestation_min'] = "La borne min d'infestation doit etre positive."
        if self.infestation_max is not None and self.infestation_max > 100:
            errors['infestation_max'] = "La borne max d'infestation ne peut pas depasser 100 %."
        if (
            self.infestation_min is not None
            and self.infestation_max is not None
            and self.infestation_min >= self.infestation_max
        ):
            errors['infestation_max'] = "La borne max d'infestation doit etre strictement superieure a la borne min."
        if self.auxiliary_min is not None and self.auxiliary_max is not None and self.auxiliary_min >= self.auxiliary_max:
            errors['auxiliary_max'] = "La borne max d'auxiliaires doit etre strictement superieure a la borne min."
        if errors:
            raise ValidationError(errors)

        conflicts = list(self.conflicting_active_rules())
        if conflicts:
            conflict_names = ', '.join(f'{rule.title} (#{rule.pk})' for rule in conflicts)
            raise ValidationError(
                {
                    '__all__': (
                        'Chevauchement avec des regles actives existantes: '
                        f'{conflict_names}. Desactivez-les ou ajustez les plages.'
                    )
                }
            )


class DecisionLever(models.Model):
    rule = models.ForeignKey(DecisionRule, on_delete=models.CASCADE, related_name='levers', verbose_name='Regle')
    title = models.CharField(max_length=160, verbose_name='Titre')
    description = models.TextField(verbose_name='Description du levier')
    action_type = models.ForeignKey(
        ActionType,
        on_delete=models.PROTECT,
        related_name='decision_levers',
        verbose_name="Type d'action",
    )
    scope = models.CharField(max_length=12, choices=ACTION_SCOPE_CHOICES, default='general', verbose_name='Portee')
    molecule = models.ForeignKey(
        Molecule,
        on_delete=models.PROTECT,
        related_name='decision_levers',
        null=True,
        blank=True,
        verbose_name='Molecule preselectionnee',
    )
    auxiliary_taxon = models.ForeignKey(
        AuxiliaryTaxon,
        on_delete=models.PROTECT,
        related_name='decision_levers',
        null=True,
        blank=True,
        limit_choices_to={'is_releasable': True},
        verbose_name='Auxiliaire preselectionne',
    )
    notes_template = models.TextField(blank=True, verbose_name='Details preraplis')
    display_order = models.PositiveSmallIntegerField(default=1, verbose_name="Ordre d'affichage")
    is_active = models.BooleanField(default=True, verbose_name='Actif')

    class Meta:
        ordering = ['display_order', 'title']
        verbose_name = 'Levier de decision'
        verbose_name_plural = 'Leviers de decision'

    def __str__(self):
        return f'{self.rule.title} - {self.title}'

    def clean(self):
        errors = {}
        category = self.action_type.category if self.action_type_id else None
        if self.molecule_id and category != 'treatment':
            errors['molecule'] = 'La molecule ne peut etre preselectionnee que pour un type traitement.'
        if self.auxiliary_taxon_id:
            if category != 'release':
                errors['auxiliary_taxon'] = "L'auxiliaire ne peut etre preselectionne que pour un type lacher."
            elif not self.auxiliary_taxon.is_releasable:
                errors['auxiliary_taxon'] = "Cet auxiliaire n'est pas marque comme lachable."
        if self.molecule_id and self.rule_id and not self.molecule.crops.filter(id=self.rule.crop_id).exists():
            errors['molecule'] = "La molecule preselectionnee n'est pas autorisee pour la culture de cette regle."
        if errors:
            raise ValidationError(errors)


class RecommendationDismissReason(models.Model):
    label = models.CharField(max_length=160, unique=True, verbose_name='Libelle')
    requires_comment = models.BooleanField(default=False, verbose_name='Texte libre propose')
    display_order = models.PositiveSmallIntegerField(default=1, verbose_name="Ordre d'affichage")
    is_active = models.BooleanField(default=True, verbose_name='Actif')

    class Meta:
        ordering = ['display_order', 'label']
        verbose_name = 'Motif de non-suivi'
        verbose_name_plural = 'Motifs de non-suivi'

    def __str__(self):
        return self.label


class RecommendationResponse(models.Model):
    record = models.ForeignKey(
        ScoutingRecord,
        on_delete=models.CASCADE,
        related_name='recommendation_responses',
        verbose_name='Comptage',
    )
    rule = models.ForeignKey(
        DecisionRule,
        on_delete=models.CASCADE,
        related_name='responses',
        verbose_name='Regle de decision',
    )
    status = models.CharField(max_length=12, choices=RECOMMENDATION_STATUS_CHOICES, verbose_name='Statut')
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='handled_recommendation_responses',
        verbose_name='Traitee par',
    )
    dismiss_reason = models.ForeignKey(
        RecommendationDismissReason,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='responses',
        verbose_name='Motif de non-suivi',
    )
    dismiss_note = models.TextField(blank=True, verbose_name='Precision libre')
    lever = models.ForeignKey(
        DecisionLever,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='responses',
        verbose_name='Levier suivi',
    )
    action = models.ForeignKey(
        PlantAction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='recommendation_responses',
        verbose_name='Action creee',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creee le')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Mise a jour le')

    class Meta:
        ordering = ['-updated_at']
        verbose_name = 'Traitement de recommandation'
        verbose_name_plural = 'Traitements de recommandation'
        constraints = [
            models.UniqueConstraint(fields=['record', 'rule'], name='unique_recommendation_response_per_record_rule')
        ]

    def __str__(self):
        return f'{self.record} - {self.rule.title} - {self.get_status_display()}'

    def clean(self):
        errors = {}
        if self.rule_id and self.record_id:
            crop = self.record.crop_ref or (self.record.plant_series.crop if self.record.plant_series_id else None)
            if crop and self.rule.crop_id != crop.id:
                errors['rule'] = 'La regle ne correspond pas a la culture du comptage.'
        if self.status == 'dismissed':
            if self.lever_id:
                errors['lever'] = 'Un levier suivi ne peut pas etre renseigne pour une recommandation non suivie.'
            if self.action_id:
                errors['action'] = 'Une action ne peut pas etre liee a une recommandation non suivie.'
        if self.status == 'followed' and self.dismiss_reason_id:
            errors['dismiss_reason'] = 'Le motif de non-suivi est reserve aux recommandations non suivies.'
        if self.dismiss_reason_id and not self.dismiss_reason.is_active:
            errors['dismiss_reason'] = 'Le motif selectionne est inactif.'
        if self.lever_id and self.rule_id and self.lever.rule_id != self.rule_id:
            errors['lever'] = 'Le levier doit appartenir a la meme regle que la recommandation.'
        if errors:
            raise ValidationError(errors)


class AuxiliaryCount(models.Model):
    record = models.ForeignKey(ScoutingRecord, on_delete=models.CASCADE, related_name='auxiliary_counts', verbose_name='Comptage')
    auxiliary_type = models.CharField(max_length=20, choices=AUXILIARY_TYPE_CHOICES, verbose_name="Type d'auxiliaire")
    count = models.PositiveIntegerField(default=0, verbose_name='Nombre')

    class Meta:
        unique_together = ('record', 'auxiliary_type')
        verbose_name = "Compte d'auxiliaire"
        verbose_name_plural = "Comptes d'auxiliaires"

    def __str__(self):
        return f'{self.get_auxiliary_type_display()}: {self.count}'


class LeafObservation(models.Model):
    record = models.ForeignKey(
        ScoutingRecord,
        on_delete=models.CASCADE,
        related_name='leaf_observations',
        verbose_name='Comptage',
    )
    plant_number = models.PositiveSmallIntegerField(verbose_name='Numero du plant')
    leaf_position = models.CharField(max_length=30, verbose_name='Position de la feuille')
    leaf_index = models.PositiveSmallIntegerField(default=1, verbose_name='Index de feuille')
    aphid_present = models.BooleanField(default=False, verbose_name='Puceron present')
    aphid_species = models.ForeignKey(
        AphidSpecies,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='leaf_observations',
        verbose_name='Espece de puceron',
    )
    syrphes = models.PositiveSmallIntegerField(default=0, verbose_name='Syrphes')
    anthocorides = models.PositiveSmallIntegerField(default=0, verbose_name='Punaises Anthocorides')
    nabides = models.PositiveSmallIntegerField(default=0, verbose_name='Punaises Nabides')
    mirides = models.PositiveSmallIntegerField(default=0, verbose_name='Punaises Mirides')
    parasitoides = models.PositiveSmallIntegerField(default=0, verbose_name='Hymenopteres parasitoides adultes')
    coccinelles = models.PositiveSmallIntegerField(default=0, verbose_name='Coccinelles')
    chrysopes_hemerobes = models.PositiveSmallIntegerField(default=0, verbose_name='Chrysopes et Hemerobes')
    cecidiomyies = models.PositiveSmallIntegerField(default=0, verbose_name='Cecidomyies predatrices')
    araignees = models.PositiveSmallIntegerField(default=0, verbose_name='Araignees adultes')

    class Meta:
        verbose_name = 'Observation de feuille'
        verbose_name_plural = 'Observations de feuilles'
        constraints = [
            models.UniqueConstraint(
                fields=['record', 'plant_number', 'leaf_index'],
                name='unique_leaf_observation_per_record',
            )
        ]
        ordering = ['plant_number', 'leaf_index']

    @property
    def total_auxiliaries(self):
        related_total = self.auxiliary_observations.aggregate(total=Sum('count'))['total']
        if related_total is not None:
            return related_total
        return sum(getattr(self, key) for key in LEGACY_LEAF_AUX_FIELDS)

    total_auxiliaries.fget.short_description = 'Total auxiliaires'


class LeafAuxiliaryObservation(models.Model):
    leaf_observation = models.ForeignKey(
        LeafObservation,
        on_delete=models.CASCADE,
        related_name='auxiliary_observations',
        verbose_name='Observation de feuille',
    )
    taxon = models.ForeignKey(
        AuxiliaryTaxon,
        on_delete=models.CASCADE,
        related_name='leaf_counts',
        verbose_name='Auxiliaire',
    )
    count = models.PositiveSmallIntegerField(default=0, verbose_name='Nombre')

    class Meta:
        verbose_name = "Observation d'auxiliaire"
        verbose_name_plural = "Observations d'auxiliaires"
        constraints = [
            models.UniqueConstraint(
                fields=['leaf_observation', 'taxon'],
                name='unique_aux_count_per_leaf_and_taxon',
            )
        ]

    def __str__(self):
        return f'{self.taxon.name}: {self.count}'
