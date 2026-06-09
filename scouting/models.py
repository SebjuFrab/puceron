from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q, Sum
from django.templatetags.static import static
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
    ('detailed', 'Détail par type'),
    ('quick', 'Saisie rapide'),
]

ENTRY_MODE_CHOICES = [
    ('detailed', 'Comptage détaillé'),
    ('quick', 'Comptage rapide'),
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
    ('release', "Lâcher d'auxiliaire"),
]

ACTION_ICON_CHOICES = [
    ('triangle', 'Triangle'),
    ('circle', 'Cercle'),
    ('rectRot', 'Losange'),
    ('rectRounded', 'Rectangle'),
    ('star', 'Étoile'),
    ('crossRot', 'Croix'),
]

ACTION_SCOPE_CHOICES = [
    ('localized', 'Localisée'),
    ('general', 'Généralisée'),
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

DEFAULT_SERVICE_PLANT_ICON_CODES = {
    'basilic',
    'tagete',
    'capucine',
    'coriandre',
    'aneth',
    'phacelie',
    'bourrache',
    'souci',
}


class LogoItemBlock(blocks.StructBlock):
    image = ImageChooserBlock(required=True, label='Logo')
    alt = blocks.CharBlock(required=False, label='Texte alternatif')
    url = blocks.URLBlock(required=False, label='Lien')

    class Meta:
        icon = 'image'
        label = 'Logo'


def current_campaign_year():
    return timezone.localdate().year


class TechnicianStructure(models.Model):
    name = models.CharField(max_length=160, unique=True, verbose_name='Structure')
    address = models.TextField(blank=True, verbose_name='Adresse')
    logo = models.ImageField(upload_to='technician_structures/', blank=True, verbose_name='Logo')
    generic_contact = models.CharField(max_length=160, blank=True, verbose_name='Contact générique')
    website = models.URLField(blank=True, verbose_name='Site web')

    class Meta:
        ordering = ['name']
        verbose_name = 'Structure technicien'
        verbose_name_plural = 'Structures technicien'

    def __str__(self):
        return self.name


class AccessControlSettings(models.Model):
    default_producer_readonly_message = models.TextField(
        blank=True,
        default=(
            "Votre compte est actuellement en lecture seule car aucun technicien actif n'est rattaché à votre profil."
        ),
        verbose_name='Message global lecture seule producteur',
    )
    default_technician_denied_message = models.TextField(
        blank=True,
        default='Votre licence technicien est inactive. Contactez le super-admin.',
        verbose_name='Message global accès refusé technicien',
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Mis à jour le')

    class Meta:
        verbose_name = "Paramètres d'accès"
        verbose_name_plural = "Paramètres d'accès"

    def __str__(self):
        return "Paramètres d'accès"

    @classmethod
    def get_solo(cls):
        return cls.objects.order_by('pk').first() or cls.objects.create()


class UserProfile(models.Model):
    ROLE_PRODUCER = 'producer'
    ROLE_TECHNICIAN = 'technician'
    ROLE_CHOICES = [
        (ROLE_PRODUCER, 'Producteur'),
        (ROLE_TECHNICIAN, 'Technicien'),
    ]
    LICENSE_STATUS_ACTIVE = 'active'
    LICENSE_STATUS_INACTIVE = 'inactive'
    LICENSE_STATUS_SUSPENDED = 'suspended'
    LICENSE_STATUS_CHOICES = [
        (LICENSE_STATUS_ACTIVE, 'Active'),
        (LICENSE_STATUS_INACTIVE, 'Inactive'),
        (LICENSE_STATUS_SUSPENDED, 'Suspendue'),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
        verbose_name='Utilisateur',
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_PRODUCER, verbose_name='Rôle')
    assigned_technician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_producer_profiles',
        verbose_name='Technicien référent',
    )
    department = models.CharField(max_length=10, blank=True, verbose_name='Département')
    structure = models.ForeignKey(
        'TechnicianStructure',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='technician_profiles',
        verbose_name='Structure',
    )
    license_status = models.CharField(
        max_length=20,
        choices=LICENSE_STATUS_CHOICES,
        default=LICENSE_STATUS_ACTIVE,
        verbose_name='Statut licence',
    )
    deactivation_message = models.TextField(
        blank=True,
        verbose_name='Message désactivation / arrêt de suivi',
    )
    farm_name = models.CharField(max_length=150, blank=True, verbose_name='Nom de ferme')
    phone = models.CharField(max_length=30, blank=True, verbose_name='Mobile')
    photo = models.ImageField(upload_to='profile_photos/', blank=True, verbose_name='Photo ou logo')
    farm_address = models.TextField(blank=True, verbose_name='Adresse complète')
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

    @property
    def has_active_license(self):
        return self.license_status == self.LICENSE_STATUS_ACTIVE

    def active_technician_assignments(self):
        return self.technician_assignments.filter(
            is_active=True,
            technician__profile__role=self.ROLE_TECHNICIAN,
            technician__profile__license_status=self.LICENSE_STATUS_ACTIVE,
        ).select_related('technician', 'technician__profile', 'technician__profile__structure')

    def has_active_technician(self):
        if self.role != self.ROLE_PRODUCER:
            return True
        return self.active_technician_assignments().exists()

    def producer_readonly_message(self):
        if self.role != self.ROLE_PRODUCER:
            return ''
        if self.has_active_technician():
            return ''

        latest_closed_assignment = (
            self.technician_assignments.exclude(message='')
            .exclude(is_active=True)
            .order_by('-ended_at', '-updated_at')
            .first()
        )
        if latest_closed_assignment:
            return latest_closed_assignment.message

        previous_assignment = (
            self.technician_assignments.exclude(message='')
            .order_by('-updated_at', '-created_at')
            .first()
        )
        if previous_assignment:
            return previous_assignment.message

        inactive_assignment_message = (
            self.technician_assignments.filter(
                is_active=True,
                technician__profile__role=self.ROLE_TECHNICIAN,
            )
            .exclude(technician__profile__deactivation_message='')
            .order_by('-updated_at')
            .values_list('technician__profile__deactivation_message', flat=True)
            .first()
        )
        if inactive_assignment_message:
            return inactive_assignment_message

        settings = AccessControlSettings.get_solo()
        return settings.default_producer_readonly_message

    def save(self, *args, **kwargs):
        self.sync_profile_fields()
        super().save(*args, **kwargs)


class ProducerTechnicianAssignment(models.Model):
    END_REASON_ADMIN_REMOVED = 'admin_removed'
    END_REASON_TECHNICIAN_STOP = 'technician_stop'
    END_REASON_TECHNICIAN_DISABLED = 'technician_disabled'
    END_REASON_REASSIGNED = 'reassigned'
    END_REASON_CHOICES = [
        (END_REASON_ADMIN_REMOVED, 'Retrait admin'),
        (END_REASON_TECHNICIAN_STOP, 'Arrêt suivi technicien'),
        (END_REASON_TECHNICIAN_DISABLED, 'Technicien désactivé'),
        (END_REASON_REASSIGNED, 'Réaffectation'),
    ]

    producer_profile = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name='technician_assignments',
        verbose_name='Producteur',
    )
    technician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='producer_assignments',
        verbose_name='Technicien',
    )
    is_active = models.BooleanField(default=True, verbose_name='Affectation active')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Affecte le')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_producer_assignments',
        verbose_name='Affecte par',
    )
    ended_at = models.DateTimeField(null=True, blank=True, verbose_name='Fin affectation')
    ended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ended_producer_assignments',
        verbose_name='Termine par',
    )
    end_reason = models.CharField(
        max_length=30,
        choices=END_REASON_CHOICES,
        blank=True,
        verbose_name='Motif de fin',
    )
    message = models.TextField(
        blank=True,
        verbose_name='Message producteur',
        help_text='Message affiché au producteur si son accès passe en lecture seule.',
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Mis à jour le')

    class Meta:
        ordering = ['-is_active', '-updated_at', '-created_at']
        verbose_name = 'Affectation technicien/producteur'
        verbose_name_plural = 'Affectations technicien/producteur'
        constraints = [
            models.UniqueConstraint(
                fields=['producer_profile', 'technician'],
                condition=Q(is_active=True),
                name='unique_active_technician_assignment_per_producer',
            )
        ]

    def __str__(self):
        return f'{self.producer_profile} -> {display_user_name(self.technician)}'

    def clean(self):
        errors = {}
        if self.producer_profile_id and self.producer_profile.role != UserProfile.ROLE_PRODUCER:
            errors['producer_profile'] = "L'affectation doit pointer vers un profil producteur."
        if self.technician_id:
            technician_profile = UserProfile.objects.get_or_create(user=self.technician)[0]
            if technician_profile.role != UserProfile.ROLE_TECHNICIAN:
                errors['technician'] = "L'affectation doit pointer vers un technicien."
        if errors:
            raise ValidationError(errors)

    def close(self, *, ended_by=None, reason='', message=''):
        self.is_active = False
        self.ended_at = timezone.now()
        self.ended_by = ended_by
        self.end_reason = reason or self.end_reason
        if message:
            self.message = message
        self.save(update_fields=['is_active', 'ended_at', 'ended_by', 'end_reason', 'message', 'updated_at'])


class TechnicianCoFollowRequest(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_ACCEPTED = 'accepted'
    STATUS_REJECTED = 'rejected'
    STATUS_PARTIAL = 'partial'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'En attente'),
        (STATUS_ACCEPTED, 'Acceptée'),
        (STATUS_REJECTED, 'Refusée'),
        (STATUS_PARTIAL, 'Partielle'),
    ]

    source_technician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='outgoing_cofollow_requests',
        verbose_name='Technicien source',
    )
    target_technician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='incoming_cofollow_requests',
        verbose_name='Technicien cible',
    )
    message = models.TextField(blank=True, verbose_name='Message')
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        verbose_name='Statut',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Créé le')
    responded_at = models.DateTimeField(null=True, blank=True, verbose_name='Traite le')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Demande co-suivi technicien'
        verbose_name_plural = 'Demandes co-suivi technicien'

    def __str__(self):
        return f'{display_user_name(self.source_technician)} -> {display_user_name(self.target_technician)}'


class TechnicianCoFollowRequestItem(models.Model):
    DECISION_PENDING = 'pending'
    DECISION_ACCEPTED = 'accepted'
    DECISION_REJECTED = 'rejected'
    DECISION_CHOICES = [
        (DECISION_PENDING, 'En attente'),
        (DECISION_ACCEPTED, 'Accepte'),
        (DECISION_REJECTED, 'Refuse'),
    ]

    request = models.ForeignKey(
        TechnicianCoFollowRequest,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name='Demande',
    )
    producer_profile = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name='cofollow_request_items',
        verbose_name='Producteur',
    )
    decision = models.CharField(
        max_length=20,
        choices=DECISION_CHOICES,
        default=DECISION_PENDING,
        verbose_name='Décision',
    )
    decided_at = models.DateTimeField(null=True, blank=True, verbose_name='Decide le')

    class Meta:
        ordering = ['id']
        verbose_name = 'Producteur demande co-suivi'
        verbose_name_plural = 'Producteurs demande co-suivi'
        constraints = [
            models.UniqueConstraint(
                fields=['request', 'producer_profile'],
                name='unique_cofollow_request_item_per_producer',
            )
        ]

    def __str__(self):
        return f'{self.request_id} - {self.producer_profile}'


class NotificationPreference(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notification_preference',
        verbose_name='Utilisateur',
    )
    bulletin_email_enabled = models.BooleanField(
        default=True,
        verbose_name='Recevoir les bulletins par email',
    )
    bulletin_email_urgent_only = models.BooleanField(
        default=False,
        verbose_name='Limiter aux bulletins urgents',
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Mis à jour le')

    class Meta:
        verbose_name = 'Préférence de notification'
        verbose_name_plural = 'Préférences de notification'

    def __str__(self):
        return f'Notifications - {display_user_name(self.user)}'

    def wants_bulletin_email(self, bulletin):
        if not self.bulletin_email_enabled:
            return False
        if self.bulletin_email_urgent_only:
            return bool(bulletin.priority_id and bulletin.priority.code == 'urgent')
        return True


class BulletinMessageType(models.Model):
    CODE_BSV = 'bsv'
    CODE_ALERT = 'alert'
    CODE_ADVICE = 'advice'
    CODE_REMINDER = 'reminder'

    code = models.SlugField(max_length=50, unique=True, verbose_name='Code')
    label = models.CharField(max_length=120, unique=True, verbose_name='Libellé')
    display_order = models.PositiveSmallIntegerField(default=1, verbose_name="Ordre d'affichage")
    is_active = models.BooleanField(default=True, verbose_name='Actif')

    class Meta:
        ordering = ['display_order', 'label']
        verbose_name = 'Type de bulletin'
        verbose_name_plural = 'Types de bulletin'

    def __str__(self):
        return self.label


class BulletinPriority(models.Model):
    CODE_INFO = 'info'
    CODE_WATCH = 'watch'
    CODE_URGENT = 'urgent'

    code = models.SlugField(max_length=50, unique=True, verbose_name='Code')
    label = models.CharField(max_length=120, unique=True, verbose_name='Libellé')
    display_order = models.PositiveSmallIntegerField(default=1, verbose_name="Ordre d'affichage")
    is_active = models.BooleanField(default=True, verbose_name='Actif')

    class Meta:
        ordering = ['display_order', 'label']
        verbose_name = 'Priorité de bulletin'
        verbose_name_plural = 'Priorités de bulletin'

    def __str__(self):
        return self.label


class BulletinMessage(models.Model):
    STATUS_DRAFT = 'draft'
    STATUS_SENT = 'sent'
    STATUS_ARCHIVED = 'archived'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Brouillon'),
        (STATUS_SENT, 'Envoyé'),
        (STATUS_ARCHIVED, 'Archive'),
    ]

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='authored_bulletins',
        verbose_name='Technicien auteur',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_bulletins',
        verbose_name='Créé par',
    )
    title = models.CharField(max_length=180, verbose_name='Titre')
    body = models.TextField(verbose_name='Message')
    types = models.ManyToManyField(
        BulletinMessageType,
        related_name='bulletins',
        verbose_name='Types',
    )
    priority = models.ForeignKey(
        BulletinPriority,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='bulletins',
        verbose_name='Priorité',
    )
    crops = models.ManyToManyField(
        'Crop',
        blank=True,
        related_name='bulletin_messages',
        verbose_name='Cultures',
    )
    departments = models.ManyToManyField(
        'Department',
        blank=True,
        related_name='bulletin_messages',
        verbose_name='Départements',
    )
    valid_until = models.DateField(null=True, blank=True, verbose_name="Valable jusqu'au")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        verbose_name='Statut',
    )
    sent_at = models.DateTimeField(null=True, blank=True, verbose_name='Envoyé le')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Créé le')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Mis à jour le')

    class Meta:
        ordering = ['-sent_at', '-created_at']
        verbose_name = 'Bulletin technicien'
        verbose_name_plural = 'Bulletins techniciens'

    def __str__(self):
        return self.title

    @property
    def type_labels(self):
        return ', '.join(str(message_type) for message_type in self.types.all())

    @property
    def priority_label(self):
        return str(self.priority) if self.priority_id else ''

    @property
    def crop_labels(self):
        return ', '.join(str(crop) for crop in self.crops.all())

    @property
    def department_labels(self):
        return ', '.join(str(department) for department in self.departments.all())


class BulletinAttachment(models.Model):
    TYPE_PHOTO = 'photo'
    TYPE_FILE = 'file'
    TYPE_CHOICES = [
        (TYPE_PHOTO, 'Photo'),
        (TYPE_FILE, 'Pièce jointe'),
    ]

    bulletin = models.ForeignKey(
        BulletinMessage,
        on_delete=models.CASCADE,
        related_name='attachments',
        verbose_name='Bulletin',
    )
    file = models.FileField(upload_to='bulletins/attachments/', verbose_name='Fichier')
    original_name = models.CharField(max_length=255, blank=True, verbose_name='Nom original')
    attachment_type = models.CharField(max_length=20, choices=TYPE_CHOICES, verbose_name='Type')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Ajouté le')

    class Meta:
        ordering = ['attachment_type', 'original_name', 'id']
        verbose_name = 'Fichier de bulletin'
        verbose_name_plural = 'Fichiers de bulletin'

    def __str__(self):
        return self.original_name or self.file.name

    @property
    def is_photo(self):
        return self.attachment_type == self.TYPE_PHOTO


class BulletinRecipient(models.Model):
    bulletin = models.ForeignKey(
        BulletinMessage,
        on_delete=models.CASCADE,
        related_name='recipients',
        verbose_name='Bulletin',
    )
    producer_profile = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name='bulletin_recipients',
        verbose_name='Producteur',
    )
    first_opened_at = models.DateTimeField(null=True, blank=True, verbose_name='Première ouverture')
    last_opened_at = models.DateTimeField(null=True, blank=True, verbose_name='Dernière ouverture')
    open_count = models.PositiveIntegerField(default=0, verbose_name="Nombre d'ouvertures")
    acknowledged_at = models.DateTimeField(null=True, blank=True, verbose_name='Pris connaissance le')
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='acknowledged_bulletins',
        verbose_name='Pris connaissance par',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Ajouté le')

    class Meta:
        ordering = ['producer_profile__farm_name', 'producer_profile__user__username']
        verbose_name = 'Destinataire de bulletin'
        verbose_name_plural = 'Destinataires de bulletin'
        constraints = [
            models.UniqueConstraint(
                fields=['bulletin', 'producer_profile'],
                name='unique_bulletin_recipient_per_producer',
            )
        ]

    def __str__(self):
        return f'{self.bulletin} - {self.producer_profile}'

    def mark_opened(self, *, at=None):
        opened_at = at or timezone.now()
        updates = {
            'last_opened_at': opened_at,
            'open_count': F('open_count') + 1,
        }
        if self.first_opened_at is None:
            updates['first_opened_at'] = opened_at
        BulletinRecipient.objects.filter(pk=self.pk).update(**updates)
        self.refresh_from_db(fields=['first_opened_at', 'last_opened_at', 'open_count'])

    def mark_acknowledged(self, user, *, at=None):
        if self.acknowledged_at:
            return
        self.acknowledged_at = at or timezone.now()
        self.acknowledged_by = user
        self.save(update_fields=['acknowledged_at', 'acknowledged_by'])


class NotificationDelivery(models.Model):
    CHANNEL_IN_APP = 'in_app'
    CHANNEL_EMAIL = 'email'
    CHANNEL_PUSH = 'push'
    CHANNEL_SMS = 'sms'
    CHANNEL_CHOICES = [
        (CHANNEL_IN_APP, 'Application'),
        (CHANNEL_EMAIL, 'Email'),
        (CHANNEL_PUSH, 'Push'),
        (CHANNEL_SMS, 'SMS'),
    ]

    STATUS_PENDING = 'pending'
    STATUS_SENT = 'sent'
    STATUS_FAILED = 'failed'
    STATUS_SKIPPED = 'skipped'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'En attente'),
        (STATUS_SENT, 'Envoyee'),
        (STATUS_FAILED, 'Échec'),
        (STATUS_SKIPPED, 'Ignoree'),
    ]

    recipient = models.ForeignKey(
        BulletinRecipient,
        on_delete=models.CASCADE,
        related_name='notification_deliveries',
        verbose_name='Destinataire',
    )
    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES, verbose_name='Canal')
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        verbose_name='Statut',
    )
    error = models.TextField(blank=True, verbose_name='Erreur')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Créée le')
    sent_at = models.DateTimeField(null=True, blank=True, verbose_name='Envoyee le')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Livraison de notification'
        verbose_name_plural = 'Livraisons de notification'

    def __str__(self):
        return f'{self.recipient_id} - {self.channel} - {self.status}'


class InfoPage(models.Model):
    page_key = models.CharField(max_length=30, choices=INFO_PAGE_KEY_CHOICES, unique=True, verbose_name='Page')
    title = models.CharField(max_length=160, verbose_name='Titre')
    intro = models.TextField(blank=True, verbose_name='Introduction')
    content = models.TextField(blank=True, verbose_name='Contenu')
    is_published = models.BooleanField(default=True, verbose_name='Publiee')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Mise à jour le')

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


class Department(models.Model):
    code = models.CharField(max_length=10, unique=True, verbose_name='Code')
    name = models.CharField(max_length=120, verbose_name='Nom')
    is_active = models.BooleanField(default=True, verbose_name='Actif')

    class Meta:
        ordering = ['code']
        verbose_name = 'Département'
        verbose_name_plural = 'Départements'

    def __str__(self):
        return self.label

    @property
    def label(self):
        if self.name and self.code:
            return f'{self.name} ({self.code})'
        return self.code


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
    footer_title = models.CharField(max_length=160, blank=True, default='', verbose_name='Titre footer')
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
        verbose_name='Indicateur auxiliaires pour la décision',
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
        verbose_name='Créée par',
    )

    class Meta:
        ordering = ['name']
        verbose_name = 'Variete'
        verbose_name_plural = 'Variétés'
        constraints = [
            models.UniqueConstraint(fields=['crop', 'name'], name='unique_variety_per_crop')
        ]

    def __str__(self):
        return f'{self.crop.name} - {self.name}'


class ServicePlant(models.Model):
    code = models.SlugField(max_length=50, unique=True, verbose_name='Code')
    name = models.CharField(max_length=140, verbose_name='Nom')
    latin_name = models.CharField(max_length=140, blank=True, verbose_name='Nom latin')
    photo = models.ImageField(upload_to='service_plants/', blank=True, verbose_name='Photo')
    description = models.TextField(blank=True, verbose_name='Description')
    display_order = models.PositiveSmallIntegerField(default=1, verbose_name="Ordre d'affichage")
    is_active = models.BooleanField(default=True, verbose_name='Actif')

    class Meta:
        ordering = ['display_order', 'name', 'latin_name']
        verbose_name = 'Plante de service'
        verbose_name_plural = 'Plantes de service'

    def __str__(self):
        if self.latin_name and self.latin_name != self.name:
            return f'{self.name} ({self.latin_name})'
        return self.name

    @property
    def image_url(self):
        if self.photo:
            return self.photo.url
        if self.code in DEFAULT_SERVICE_PLANT_ICON_CODES:
            return static(f'service_plants/icons/{self.code}.png')
        return ''


class PlantSeries(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='plant_series',
        verbose_name='Producteur',
    )
    name = models.CharField(max_length=120, verbose_name='Nom de la série')
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
    has_service_plants = models.BooleanField(default=False, verbose_name='Presence de plantes de service')
    service_plants = models.ManyToManyField(
        ServicePlant,
        related_name='plant_series',
        blank=True,
        verbose_name='Plantes de service',
    )
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
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Créée le')

    class Meta:
        ordering = ['name']
        verbose_name = 'Série de plants'
        verbose_name_plural = 'Séries de plants'
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
        verbose_name='Série de plants',
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
    department = models.CharField(max_length=10, verbose_name='Département')
    crop = models.CharField(max_length=100, verbose_name='Culture')
    scouting_date = models.DateField(default=timezone.localdate, verbose_name='Date observation')
    year = models.PositiveSmallIntegerField(verbose_name='Annee')
    week = models.PositiveSmallIntegerField(verbose_name='Semaine')
    entry_mode = models.CharField(
        max_length=10,
        choices=ENTRY_MODE_CHOICES,
        default='detailed',
        verbose_name='Mode de saisie',
    )
    observed_plants_count = models.PositiveSmallIntegerField(default=10, verbose_name='Plants observes')
    observed_leaves_count = models.PositiveSmallIntegerField(default=30, verbose_name='Feuilles observees')
    aphid_infested_leaves_count = models.PositiveSmallIntegerField(default=0, verbose_name='Feuilles infestees de pucerons')
    aphid_infested_percent = models.DecimalField(max_digits=5, decimal_places=2, verbose_name='% feuilles infestees')
    primary_aphid_species = models.ForeignKey(
        'AphidSpecies',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='primary_records',
        verbose_name='Espèce principale de puceron',
    )
    auxiliary_mode = models.CharField(max_length=10, choices=AUXILIARY_MODE_CHOICES, default='total', verbose_name='Mode auxiliaires')
    auxiliary_total = models.PositiveIntegerField(default=0, verbose_name='Total auxiliaires')
    comment = models.TextField(blank=True, verbose_name='Commentaire')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Créé le')

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
        if self.entry_mode == 'quick' and self.observed_plants_count:
            plants_count = self.observed_plants_count
        else:
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
            self.entry_mode = 'detailed'
            self.observed_plants_count = self.plant_series.plants_count if self.plant_series_id and self.plant_series else 10
            self.observed_leaves_count = 0
            self.aphid_infested_leaves_count = 0
            self.primary_aphid_species = None
            self.save(
                update_fields=[
                    'aphid_infested_percent',
                    'auxiliary_total',
                    'auxiliary_mode',
                    'entry_mode',
                    'observed_plants_count',
                    'observed_leaves_count',
                    'aphid_infested_leaves_count',
                    'primary_aphid_species',
                ]
            )
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
        observed_plants = max((leaf.plant_number for leaf in leaves), default=0)
        self.aphid_infested_percent = round((infested_count / len(leaves)) * 100, 2)
        self.auxiliary_total = total_aux
        self.auxiliary_mode = 'detailed'
        self.entry_mode = 'detailed'
        self.observed_plants_count = observed_plants or (self.plant_series.plants_count if self.plant_series_id and self.plant_series else 10)
        self.observed_leaves_count = len(leaves)
        self.aphid_infested_leaves_count = infested_count
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
                'entry_mode',
                'observed_plants_count',
                'observed_leaves_count',
                'aphid_infested_leaves_count',
                'primary_aphid_species',
            ]
        )

    def observed_aphid_species_ids(self):
        if self.entry_mode == 'quick':
            return list(self.quick_aphid_species.values_list('species_id', flat=True).distinct())
        return list(
            self.leaf_observations.filter(aphid_present=True, aphid_species__isnull=False)
            .values_list('aphid_species_id', flat=True)
            .distinct()
        )

    def species_means_per_plant(self):
        taxa = list(AuxiliaryTaxon.objects.order_by('display_order', 'name'))
        totals = {taxon.id: 0 for taxon in taxa}
        if self.entry_mode == 'quick':
            rows = self.quick_auxiliary_counts.values('taxon_id').annotate(total=Sum('count'))
            divisor = self.observed_plants_count or 10
        else:
            rows = (
                LeafAuxiliaryObservation.objects.filter(leaf_observation__record=self)
                .values('taxon_id')
                .annotate(total=Sum('count'))
            )
            if not rows:
                for key, _ in AUXILIARY_SPECIES:
                    legacy_total = sum(getattr(leaf, key) for leaf in self.leaf_observations.all())
                    taxon = next((item for item in taxa if item.code == key), None)
                    if taxon:
                        totals[taxon.id] = legacy_total
            divisor = self.plant_series.plants_count if self.plant_series_id and self.plant_series else 10
        for row in rows:
            totals[row['taxon_id']] = row['total'] or 0
        divisor = divisor or 10
        return {taxon.id: round(totals[taxon.id] / float(divisor), 2) for taxon in taxa}


class QuickRecordAphidSpecies(models.Model):
    record = models.ForeignKey(
        ScoutingRecord,
        on_delete=models.CASCADE,
        related_name='quick_aphid_species',
        verbose_name='Comptage',
    )
    species = models.ForeignKey(
        'AphidSpecies',
        on_delete=models.CASCADE,
        related_name='quick_records',
        verbose_name='Espèce de puceron',
    )

    class Meta:
        verbose_name = 'Espèce de puceron observée (rapide)'
        verbose_name_plural = 'Especes de pucerons observees (rapide)'
        constraints = [
            models.UniqueConstraint(fields=['record', 'species'], name='unique_quick_aphid_species_per_record')
        ]

    def __str__(self):
        return f'{self.record_id} - {self.species}'


class QuickRecordAuxiliaryCount(models.Model):
    record = models.ForeignKey(
        ScoutingRecord,
        on_delete=models.CASCADE,
        related_name='quick_auxiliary_counts',
        verbose_name='Comptage',
    )
    taxon = models.ForeignKey(
        'AuxiliaryTaxon',
        on_delete=models.CASCADE,
        related_name='quick_record_counts',
        verbose_name='Auxiliaire',
    )
    count = models.PositiveSmallIntegerField(default=0, verbose_name='Nombre')

    class Meta:
        verbose_name = "Compte d'auxiliaire (rapide)"
        verbose_name_plural = "Comptes d'auxiliaires (rapide)"
        constraints = [
            models.UniqueConstraint(fields=['record', 'taxon'], name='unique_quick_aux_count_per_record_and_taxon')
        ]

    def __str__(self):
        return f'{self.taxon.name}: {self.count}'


class QuickRecordOtherPestCount(models.Model):
    record = models.ForeignKey(
        ScoutingRecord,
        on_delete=models.CASCADE,
        related_name='quick_other_pest_counts',
        verbose_name='Comptage',
    )
    taxon = models.ForeignKey(
        'OtherPestTaxon',
        on_delete=models.CASCADE,
        related_name='quick_record_counts',
        verbose_name='Autre ravageur',
    )
    infested_leaves_count = models.PositiveSmallIntegerField(default=0, verbose_name='Feuilles infestees')

    class Meta:
        verbose_name = "Observation d'autre ravageur (rapide)"
        verbose_name_plural = "Observations d'autres ravageurs (rapide)"
        constraints = [
            models.UniqueConstraint(fields=['record', 'taxon'], name='unique_quick_pest_count_per_record_and_taxon')
        ]

    def __str__(self):
        return f'{self.taxon.name}: {self.infested_leaves_count}'


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
        verbose_name='Molécules de lutte',
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
        verbose_name = 'Espèce de puceron'
        verbose_name_plural = 'Especes de pucerons'

    def __str__(self):
        if self.latin_name and self.latin_name != self.vernacular_name:
            return f'{self.vernacular_name} ({self.latin_name})'
        return self.vernacular_name


class OtherPestTaxon(models.Model):
    code = models.SlugField(max_length=50, unique=True, verbose_name='Code')
    name = models.CharField(max_length=140, verbose_name='Nom')
    photo = models.ImageField(upload_to='other_pest_taxa/', blank=True, verbose_name='Photo')
    display_order = models.PositiveSmallIntegerField(default=1, verbose_name="Ordre d'affichage")
    is_active = models.BooleanField(default=True, verbose_name='Actif')

    class Meta:
        ordering = ['display_order', 'name']
        verbose_name = 'Autre ravageur'
        verbose_name_plural = 'Autres ravageurs'

    def __str__(self):
        return self.name


class ActionType(models.Model):
    name = models.CharField(max_length=120, unique=True, verbose_name='Nom')
    category = models.CharField(max_length=20, choices=ACTION_CATEGORY_CHOICES, default='manual', verbose_name='Catégorie')
    chart_icon = models.CharField(
        max_length=20,
        choices=ACTION_ICON_CHOICES,
        blank=True,
        verbose_name='Icône graphique',
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
    organic_scope = models.CharField(max_length=10, choices=MOLECULE_ORGANIC_SCOPE_CHOICES, default='both', verbose_name='Portée bio/non bio')
    is_active = models.BooleanField(default=True, verbose_name='Active')

    class Meta:
        ordering = ['name']
        verbose_name = 'Molécule'
        verbose_name_plural = 'Molécules'
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
    plant_series = models.ForeignKey(PlantSeries, on_delete=models.PROTECT, related_name='actions', verbose_name='Série de plants')
    department = models.CharField(max_length=10, verbose_name='Département')
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
    scope = models.CharField(max_length=12, choices=ACTION_SCOPE_CHOICES, default='general', verbose_name='Portée')
    molecule = models.ForeignKey(
        Molecule,
        on_delete=models.PROTECT,
        related_name='actions',
        null=True,
        blank=True,
        verbose_name='Molécule',
    )
    auxiliary_taxon = models.ForeignKey(
        AuxiliaryTaxon,
        on_delete=models.PROTECT,
        related_name='release_actions',
        null=True,
        blank=True,
        limit_choices_to={'is_releasable': True},
        verbose_name='Auxiliaire lâché',
    )
    decision_lever = models.ForeignKey(
        'DecisionLever',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='actions',
        verbose_name='Levier activé',
    )
    notes = models.TextField(blank=True, verbose_name='Détails')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Créée le')

    class Meta:
        ordering = ['-action_date', '-created_at']
        verbose_name = 'Action préventive ou curative'
        verbose_name_plural = 'Actions préventives ou curatives'

    def clean(self):
        errors = {}
        category = self.action_type.category if self.action_type_id else None
        if category != 'treatment' and self.molecule_id:
            errors['molecule'] = 'La molécule est réservée aux actions de type traitement.'
        if category == 'release':
            if not self.auxiliary_taxon_id:
                errors['auxiliary_taxon'] = "Choisissez un auxiliaire pour un lâcher."
            elif not self.auxiliary_taxon.is_releasable:
                errors['auxiliary_taxon'] = "Cet auxiliaire n'est pas marqué comme lâchable."
        elif self.auxiliary_taxon_id:
            errors['auxiliary_taxon'] = "L'auxiliaire est réservé au type lâcher."
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
    priority = models.PositiveSmallIntegerField(default=100, verbose_name='Priorité')
    is_active = models.BooleanField(default=True, verbose_name='Active')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Créée le')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Mise à jour le')

    class Meta:
        ordering = ['crop__name', 'priority', 'title']
        verbose_name = 'Règle de décision'
        verbose_name_plural = 'Règles de décision'

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
            errors['week_min'] = 'La semaine min doit être comprise entre 1 et 53.'
        if self.week_max is not None and self.week_max > 53:
            errors['week_max'] = 'La semaine max doit être comprise entre 1 et 53.'
        if self.week_min is not None and self.week_max is not None and self.week_min > self.week_max:
            errors['week_max'] = 'La semaine max doit être supérieure ou égale à la semaine min.'
        if self.infestation_min is not None and self.infestation_min < 0:
            errors['infestation_min'] = "La borne min d'infestation doit être positive."
        if self.infestation_max is not None and self.infestation_max > 100:
            errors['infestation_max'] = "La borne max d'infestation ne peut pas depasser 100 %."
        if (
            self.infestation_min is not None
            and self.infestation_max is not None
            and self.infestation_min >= self.infestation_max
        ):
            errors['infestation_max'] = "La borne max d'infestation doit être strictement supérieure à la borne min."
        if self.auxiliary_min is not None and self.auxiliary_max is not None and self.auxiliary_min >= self.auxiliary_max:
            errors['auxiliary_max'] = "La borne max d'auxiliaires doit être strictement supérieure à la borne min."
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
    scope = models.CharField(max_length=12, choices=ACTION_SCOPE_CHOICES, default='general', verbose_name='Portée')
    molecule = models.ForeignKey(
        Molecule,
        on_delete=models.PROTECT,
        related_name='decision_levers',
        null=True,
        blank=True,
        verbose_name='Molécule présélectionnée',
    )
    auxiliary_taxon = models.ForeignKey(
        AuxiliaryTaxon,
        on_delete=models.PROTECT,
        related_name='decision_levers',
        null=True,
        blank=True,
        limit_choices_to={'is_releasable': True},
        verbose_name='Auxiliaire présélectionné',
    )
    notes_template = models.TextField(blank=True, verbose_name='Détails préremplis')
    display_order = models.PositiveSmallIntegerField(default=1, verbose_name="Ordre d'affichage")
    is_active = models.BooleanField(default=True, verbose_name='Actif')

    class Meta:
        ordering = ['display_order', 'title']
        verbose_name = 'Levier de décision'
        verbose_name_plural = 'Leviers de décision'

    def __str__(self):
        return f'{self.rule.title} - {self.title}'

    def clean(self):
        errors = {}
        category = self.action_type.category if self.action_type_id else None
        if self.molecule_id and category != 'treatment':
            errors['molecule'] = 'La molécule ne peut être présélectionnée que pour un type traitement.'
        if self.auxiliary_taxon_id:
            if category != 'release':
                errors['auxiliary_taxon'] = "L'auxiliaire ne peut être présélectionné que pour un type lâcher."
            elif not self.auxiliary_taxon.is_releasable:
                errors['auxiliary_taxon'] = "Cet auxiliaire n'est pas marqué comme lâchable."
        if self.molecule_id and self.rule_id and not self.molecule.crops.filter(id=self.rule.crop_id).exists():
            errors['molecule'] = "La molécule présélectionnée n'est pas autorisée pour la culture de cette règle."
        if errors:
            raise ValidationError(errors)


class RecommendationDismissReason(models.Model):
    label = models.CharField(max_length=160, unique=True, verbose_name='Libellé')
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
        verbose_name='Règle de décision',
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
    dismiss_note = models.TextField(blank=True, verbose_name='Précision libre')
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
        verbose_name='Action créée',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Créée le')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Mise à jour le')

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
                errors['lever'] = 'Un levier suivi ne peut pas être renseigné pour une recommandation non suivie.'
            if self.action_id:
                errors['action'] = 'Une action ne peut pas être liée a une recommandation non suivie.'
        if self.status == 'followed' and self.dismiss_reason_id:
            errors['dismiss_reason'] = 'Le motif de non-suivi est réservé aux recommandations non suivies.'
        if self.dismiss_reason_id and not self.dismiss_reason.is_active:
            errors['dismiss_reason'] = 'Le motif sélectionné est inactif.'
        if self.lever_id and self.rule_id and self.lever.rule_id != self.rule_id:
            errors['lever'] = 'Le levier doit appartenir à la même règle que la recommandation.'
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
        verbose_name='Espèce de puceron',
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


class LeafOtherPestObservation(models.Model):
    leaf_observation = models.ForeignKey(
        LeafObservation,
        on_delete=models.CASCADE,
        related_name='other_pest_observations',
        verbose_name='Observation de feuille',
    )
    taxon = models.ForeignKey(
        OtherPestTaxon,
        on_delete=models.CASCADE,
        related_name='leaf_observations',
        verbose_name='Autre ravageur',
    )

    class Meta:
        verbose_name = "Observation d'autre ravageur"
        verbose_name_plural = "Observations d'autres ravageurs"
        constraints = [
            models.UniqueConstraint(
                fields=['leaf_observation', 'taxon'],
                name='unique_other_pest_per_leaf_and_taxon',
            )
        ]

    def __str__(self):
        return self.taxon.name
