from django import forms
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.db.models import Count, Q
from django.utils.html import format_html
from rest_framework.authtoken.models import Token, TokenProxy

from .models import (
    AccessControlSettings,
    ActionType,
    AphidSpecies,
    AuxiliaryCount,
    AuxiliaryTaxon,
    ConductType,
    Crop,
    Department,
    DecisionLever,
    DecisionRule,
    LeafAuxiliaryObservation,
    LeafObservation,
    Molecule,
    OtherPestTaxon,
    LeafOtherPestObservation,
    PlantSeries,
    PlantAction,
    QuickRecordAphidSpecies,
    QuickRecordAuxiliaryCount,
    QuickRecordOtherPestCount,
    RecommendationDismissReason,
    ScoutingRecord,
    ServicePlant,
    ProducerTechnicianAssignment,
    TechnicianCoFollowRequest,
    TechnicianCoFollowRequestItem,
    TechnicianStructure,
    UserProfile,
    Variety,
)

User = get_user_model()

ADMIN_APP_ORDER = {
    'scouting': 10,
    'auth': 20,
    'authtoken': 30,
}

ADMIN_MODEL_ORDER = {
    'scouting': {
        'UserProfile': 10,
        'ProducerTechnicianAssignment': 20,
        'TechnicianCoFollowRequest': 30,
        'TechnicianStructure': 40,
        'PlantSeries': 50,
        'ScoutingRecord': 60,
        'PlantAction': 70,
        'Department': 80,
        'Crop': 90,
        'ConductType': 100,
        'Variety': 110,
        'ServicePlant': 120,
        'AuxiliaryTaxon': 130,
        'AphidSpecies': 140,
        'OtherPestTaxon': 150,
        'Molecule': 160,
        'RecommendationDismissReason': 170,
        'ActionType': 180,
        'DecisionRule': 190,
        'DecisionLever': 200,
        'AccessControlSettings': 210,
        'LeafObservation': 220,
    },
    'auth': {
        'User': 10,
        'Group': 20,
    },
}

SCOUTING_ADMIN_GROUPS = [
    (
        'users',
        'Utilisateurs',
        ['UserProfile', 'ProducerTechnicianAssignment', 'TechnicianCoFollowRequest', 'TechnicianStructure'],
    ),
    ('observations', 'Observations', ['PlantSeries', 'ScoutingRecord', 'LeafObservation']),
    (
        'settings',
        'Parametrage',
        [
            'Department',
            'Crop',
            'ConductType',
            'Variety',
            'ServicePlant',
            'AuxiliaryTaxon',
            'AphidSpecies',
            'OtherPestTaxon',
            'Molecule',
            'RecommendationDismissReason',
            'AccessControlSettings',
        ],
    ),
    (
        'decisions',
        "Preconisation et plan d'intervention",
        ['PlantAction', 'ActionType', 'DecisionRule', 'DecisionLever'],
    ),
]


def _attach_admin_groups(app):
    if app['app_label'] != 'scouting':
        return app

    models_by_name = {model['object_name']: model for model in app['models']}
    used_models = set()
    grouped_models = []

    for key, title, object_names in SCOUTING_ADMIN_GROUPS:
        models = []
        for object_name in object_names:
            model = models_by_name.get(object_name)
            if model:
                models.append(model)
                used_models.add(object_name)
        if models:
            grouped_models.append({'key': key, 'title': title, 'models': models})

    app['grouped_models'] = grouped_models
    app['ungrouped_models'] = [model for model in app['models'] if model['object_name'] not in used_models]
    return app


def _sorted_admin_app_list(self, request, app_label=None):
    app_dict = self._build_app_dict(request, app_label)
    app_list = sorted(
        app_dict.values(),
        key=lambda app: (ADMIN_APP_ORDER.get(app['app_label'], 999), app['name'].lower()),
    )
    for app in app_list:
        model_order = ADMIN_MODEL_ORDER.get(app['app_label'], {})
        app['models'].sort(
            key=lambda model: (model_order.get(model['object_name'], 999), model['name'].lower())
        )
        _attach_admin_groups(app)
    return app_list


admin.AdminSite.get_app_list = _sorted_admin_app_list


try:
    admin.site.unregister(TokenProxy)
except admin.sites.NotRegistered:
    pass

try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass


@admin.register(Token)
class TokenAdmin(admin.ModelAdmin):
    list_display = ('key', 'user', 'created')
    search_fields = ('user__username', 'key')
    readonly_fields = ('key', 'created')

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


class SuperuserOnlyAdminMixin:
    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


def _department_choices(current_value=''):
    current_value = (current_value or '').strip()
    choices = [('', '---------')]
    for department in Department.objects.order_by('code'):
        label = department.label
        if not department.is_active:
            label = f'{label} [inactif]'
        choices.append((department.code, label))
    if current_value and all(code != current_value for code, _ in choices):
        choices.append((current_value, current_value))
    return choices


class UserProfileAdminForm(forms.ModelForm):
    department = forms.ChoiceField(required=False, label='Departement')

    class Meta:
        model = UserProfile
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current_value = self.instance.department if getattr(self.instance, 'pk', None) else ''
        self.fields['department'].choices = _department_choices(current_value)


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    form = UserProfileAdminForm
    fk_name = 'user'
    can_delete = False
    extra = 0
    max_num = 1
    autocomplete_fields = ('assigned_technician',)
    fields = (
        'role',
        'assigned_technician',
        'department',
        'structure',
        'license_status',
        'deactivation_message',
        'farm_name',
        'phone',
        'photo',
        'street_address',
        'postal_code',
        'city',
        'latitude',
        'longitude',
    )

    def get_extra(self, request, obj=None, **kwargs):
        if obj and hasattr(obj, 'profile'):
            return 0
        return 1


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    inlines = (UserProfileInline,)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    form = UserProfileAdminForm
    list_display = (
        'user',
        'role',
        'license_status',
        'structure',
        'assigned_technician',
        'department',
        'active_producer_count',
        'active_series_count',
        'observation_count',
        'farm_name',
        'phone',
        'postal_code',
        'city',
    )
    list_filter = ('role', 'license_status', 'department', 'structure', 'assigned_technician')
    search_fields = ('user__username', 'farm_name', 'phone', 'street_address', 'postal_code', 'city')
    autocomplete_fields = ('assigned_technician',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'user',
                    'role',
                    'license_status',
                    'deactivation_message',
                    'structure',
                    'assigned_technician',
                    'department',
                    'farm_name',
                    'phone',
                    'photo',
                    'street_address',
                    'postal_code',
                    'city',
                    'latitude',
                    'longitude',
                    'crops_grown',
                    'tracked_plants',
                )
            },
        ),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related('user', 'structure')
        return qs.annotate(
            _active_producer_count=Count(
                'user__producer_assignments__producer_profile',
                filter=Q(
                    user__producer_assignments__is_active=True,
                    user__producer_assignments__technician__profile__license_status=UserProfile.LICENSE_STATUS_ACTIVE,
                ),
                distinct=True,
            ),
            _active_series_count=Count(
                'user__producer_assignments__producer_profile__user__plant_series',
                filter=Q(
                    user__producer_assignments__is_active=True,
                    user__producer_assignments__producer_profile__user__plant_series__is_active=True,
                    user__producer_assignments__technician__profile__license_status=UserProfile.LICENSE_STATUS_ACTIVE,
                ),
                distinct=True,
            ),
            _observation_count=Count(
                'user__producer_assignments__producer_profile__user__records',
                filter=Q(
                    user__producer_assignments__is_active=True,
                    user__producer_assignments__technician__profile__license_status=UserProfile.LICENSE_STATUS_ACTIVE,
                ),
                distinct=True,
            ),
        )

    @admin.display(description='Nb producteurs')
    def active_producer_count(self, obj):
        if obj.role != UserProfile.ROLE_TECHNICIAN:
            return 0
        return getattr(obj, '_active_producer_count', 0)

    @admin.display(description='Nb series')
    def active_series_count(self, obj):
        if obj.role != UserProfile.ROLE_TECHNICIAN:
            return 0
        return getattr(obj, '_active_series_count', 0)

    @admin.display(description='Nb observations')
    def observation_count(self, obj):
        if obj.role != UserProfile.ROLE_TECHNICIAN:
            return 0
        return getattr(obj, '_observation_count', 0)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('code', 'name')
    ordering = ('code',)


@admin.register(TechnicianStructure)
class TechnicianStructureAdmin(admin.ModelAdmin):
    list_display = ('name', 'generic_contact', 'website')
    search_fields = ('name', 'generic_contact', 'address', 'website')


@admin.register(ProducerTechnicianAssignment)
class ProducerTechnicianAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        'producer_profile',
        'technician',
        'is_active',
        'created_at',
        'ended_at',
        'end_reason',
    )
    list_filter = (
        'is_active',
        'end_reason',
        'technician__profile__license_status',
    )
    search_fields = (
        'producer_profile__farm_name',
        'producer_profile__user__username',
        'technician__username',
    )
    autocomplete_fields = ('producer_profile', 'technician', 'created_by', 'ended_by')


class TechnicianCoFollowRequestItemInline(admin.TabularInline):
    model = TechnicianCoFollowRequestItem
    extra = 0
    readonly_fields = ('producer_profile', 'decision', 'decided_at')
    can_delete = False


@admin.register(TechnicianCoFollowRequest)
class TechnicianCoFollowRequestAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'source_technician',
        'target_technician',
        'status',
        'producer_count',
        'created_at',
        'responded_at',
    )
    list_filter = ('status', 'created_at')
    search_fields = (
        'source_technician__username',
        'target_technician__username',
        'source_technician__first_name',
        'source_technician__last_name',
        'target_technician__first_name',
        'target_technician__last_name',
    )
    autocomplete_fields = ('source_technician', 'target_technician')
    inlines = (TechnicianCoFollowRequestItemInline,)

    @admin.display(description='Nb producteurs')
    def producer_count(self, obj):
        return obj.items.count()


@admin.register(AccessControlSettings)
class AccessControlSettingsAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display = ('id', 'updated_at')


@admin.register(Crop)
class CropAdmin(admin.ModelAdmin):
    list_display = ('name', 'decision_aux_metric', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name',)


@admin.register(ConductType)
class ConductTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name',)


@admin.register(Variety)
class VarietyAdmin(admin.ModelAdmin):
    list_display = ('name', 'crop', 'is_active', 'created_by')
    list_filter = ('crop', 'is_active')
    search_fields = ('name', 'crop__name')


@admin.register(PlantSeries)
class PlantSeriesAdmin(admin.ModelAdmin):
    list_display = (
        'photo_preview',
        'name',
        'greenhouse',
        'year',
        'planting_week',
        'user',
        'crop',
        'conduct_type',
        'organic_mode',
        'variety',
        'has_service_plants',
        'plants_count',
        'leaves_per_plant',
        'is_active',
    )
    list_filter = ('crop', 'conduct_type', 'is_active')
    search_fields = ('name', 'greenhouse', 'user__username')
    readonly_fields = ('photo_preview',)
    filter_horizontal = ('service_plants',)
    fields = (
        'name',
        'greenhouse',
        'year',
        'planting_week',
        'user',
        'photo',
        'photo_preview',
        'crop',
        'conduct_type',
        'organic_mode',
        'variety',
        'has_service_plants',
        'service_plants',
        'plants_count',
        'leaves_per_plant',
        'is_active',
    )

    def photo_preview(self, obj):
        image_url = obj.image_url
        if not image_url:
            return '-'
        return format_html('<img src="{}" style="height:48px;border-radius:6px;" />', image_url)

    photo_preview.short_description = 'Photo'


class AuxiliaryCountInline(admin.TabularInline):
    model = AuxiliaryCount
    extra = 0


class LeafObservationInline(admin.TabularInline):
    model = LeafObservation
    extra = 0


class LeafAuxiliaryObservationInline(admin.TabularInline):
    model = LeafAuxiliaryObservation
    extra = 0


class LeafOtherPestObservationInline(admin.TabularInline):
    model = LeafOtherPestObservation
    extra = 0


class QuickRecordAphidSpeciesInline(admin.TabularInline):
    model = QuickRecordAphidSpecies
    extra = 0


class QuickRecordAuxiliaryCountInline(admin.TabularInline):
    model = QuickRecordAuxiliaryCount
    extra = 0


class QuickRecordOtherPestCountInline(admin.TabularInline):
    model = QuickRecordOtherPestCount
    extra = 0


@admin.register(AuxiliaryTaxon)
class AuxiliaryTaxonAdmin(admin.ModelAdmin):
    list_display = ('photo_preview', 'name', 'code', 'display_order', 'is_releasable', 'is_active')
    list_display_links = ('name',)
    list_filter = ('is_active', 'is_releasable')
    search_fields = ('name', 'code')
    ordering = ('display_order', 'name')
    readonly_fields = ('photo_preview',)
    fields = ('name', 'code', 'photo', 'photo_preview', 'display_order', 'is_releasable', 'is_active')

    def photo_preview(self, obj):
        if not obj.photo:
            return '-'
        return format_html('<img src="{}" style="height:48px;border-radius:6px;" />', obj.photo.url)

    photo_preview.short_description = 'Photo'


@admin.register(OtherPestTaxon)
class OtherPestTaxonAdmin(admin.ModelAdmin):
    list_display = ('photo_preview', 'name', 'code', 'display_order', 'is_active')
    list_display_links = ('name',)
    list_filter = ('is_active',)
    search_fields = ('name', 'code')
    ordering = ('display_order', 'name')
    readonly_fields = ('photo_preview',)
    fields = ('name', 'code', 'photo', 'photo_preview', 'display_order', 'is_active')

    def photo_preview(self, obj):
        if not obj.photo:
            return '-'
        return format_html('<img src="{}" style="height:48px;border-radius:6px;" />', obj.photo.url)

    photo_preview.short_description = 'Photo'


@admin.register(AphidSpecies)
class AphidSpeciesAdmin(admin.ModelAdmin):
    list_display = ('photo_preview', 'vernacular_name', 'latin_name', 'display_order', 'is_active')
    list_display_links = ('vernacular_name',)
    list_filter = ('is_active',)
    search_fields = ('vernacular_name', 'latin_name', 'code')
    ordering = ('display_order', 'vernacular_name', 'latin_name')
    readonly_fields = ('photo_preview',)
    filter_horizontal = ('molecules', 'auxiliary_taxa')
    fields = (
        'vernacular_name',
        'latin_name',
        'code',
        'photo',
        'photo_preview',
        'molecules',
        'auxiliary_taxa',
        'description',
        'display_order',
        'is_active',
    )

    def photo_preview(self, obj):
        if not obj.photo:
            return '-'
        return format_html('<img src="{}" style="height:48px;border-radius:6px;" />', obj.photo.url)

    photo_preview.short_description = 'Photo'


@admin.register(ServicePlant)
class ServicePlantAdmin(admin.ModelAdmin):
    list_display = ('photo_preview', 'name', 'latin_name', 'code', 'display_order', 'is_active')
    list_display_links = ('name',)
    list_filter = ('is_active',)
    search_fields = ('name', 'latin_name', 'code')
    ordering = ('display_order', 'name', 'latin_name')
    readonly_fields = ('photo_preview',)
    fields = (
        'name',
        'latin_name',
        'code',
        'photo',
        'photo_preview',
        'description',
        'display_order',
        'is_active',
    )

    def photo_preview(self, obj):
        if not obj.photo:
            return '-'
        return format_html('<img src="{}" style="height:48px;border-radius:6px;" />', obj.photo.url)

    photo_preview.short_description = 'Photo'


@admin.register(LeafObservation)
class LeafObservationAdmin(admin.ModelAdmin):
    list_display = ('record', 'plant_number', 'leaf_position', 'aphid_present', 'aphid_species', 'total_auxiliaries')
    list_filter = ('leaf_position', 'aphid_present', 'aphid_species')
    inlines = [LeafAuxiliaryObservationInline, LeafOtherPestObservationInline]


@admin.register(ScoutingRecord)
class ScoutingRecordAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'plant_series',
        'department',
        'crop',
        'scouting_date',
        'year',
        'week',
        'primary_aphid_species',
        'aphid_infested_percent',
        'auxiliary_total',
    )
    list_filter = ('department', 'crop', 'year', 'week', 'entry_mode', 'primary_aphid_species')
    search_fields = ('user__username',)
    inlines = [
        LeafObservationInline,
        AuxiliaryCountInline,
        QuickRecordAphidSpeciesInline,
        QuickRecordAuxiliaryCountInline,
        QuickRecordOtherPestCountInline,
    ]


@admin.register(ActionType)
class ActionTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'chart_icon', 'display_order', 'is_active')
    list_filter = ('category', 'is_active')
    search_fields = ('name',)
    ordering = ('display_order', 'name')
    fields = ('name', 'category', 'chart_icon', 'display_order', 'is_active')


class DecisionLeverInline(admin.TabularInline):
    model = DecisionLever
    extra = 0
    autocomplete_fields = ('action_type', 'molecule', 'auxiliary_taxon')
    fields = (
        'display_order',
        'title',
        'description',
        'action_type',
        'scope',
        'molecule',
        'auxiliary_taxon',
        'notes_template',
        'is_active',
    )


@admin.register(DecisionRule)
class DecisionRuleAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        'title',
        'crop',
        'week_range',
        'infestation_range',
        'auxiliary_range',
        'priority',
        'is_active',
    )
    list_filter = ('crop', 'is_active')
    search_fields = ('title', 'description', 'crop__name')
    inlines = (DecisionLeverInline,)
    fields = (
        'crop',
        'title',
        'description',
        'week_min',
        'week_max',
        'infestation_min',
        'infestation_max',
        'auxiliary_min',
        'auxiliary_max',
        'priority',
        'is_active',
    )

    def week_range(self, obj):
        week_min = obj.week_min if obj.week_min is not None else 1
        week_max = obj.week_max if obj.week_max is not None else 53
        return f'S{week_min} a S{week_max}'

    def infestation_range(self, obj):
        min_value = obj.infestation_min if obj.infestation_min is not None else 0
        max_value = obj.infestation_max if obj.infestation_max is not None else 'inf'
        return f'{min_value} <= x < {max_value}'

    def auxiliary_range(self, obj):
        min_value = obj.auxiliary_min if obj.auxiliary_min is not None else 0
        max_value = obj.auxiliary_max if obj.auxiliary_max is not None else 'inf'
        return f'{min_value} <= x < {max_value}'

    week_range.short_description = 'Plage semaines'
    infestation_range.short_description = 'Plage infestation'
    auxiliary_range.short_description = 'Plage auxiliaires'


@admin.register(DecisionLever)
class DecisionLeverAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display = ('title', 'rule', 'action_type', 'scope', 'display_order', 'is_active')
    list_filter = ('rule__crop', 'action_type__category', 'scope', 'is_active')
    search_fields = ('title', 'description', 'rule__title', 'rule__crop__name')
    autocomplete_fields = ('rule', 'action_type', 'molecule', 'auxiliary_taxon')


@admin.register(Molecule)
class MoleculeAdmin(admin.ModelAdmin):
    list_display = ('name', 'crops_list', 'organic_scope', 'is_active')
    list_filter = ('crops', 'organic_scope', 'is_active')
    search_fields = ('name', 'crops__name')
    filter_horizontal = ('crops',)

    def crops_list(self, obj):
        return ', '.join(obj.crops.order_by('name').values_list('name', flat=True))

    crops_list.short_description = 'Cultures'


@admin.register(RecommendationDismissReason)
class RecommendationDismissReasonAdmin(admin.ModelAdmin):
    list_display = ('label', 'requires_comment', 'display_order', 'is_active')
    list_filter = ('requires_comment', 'is_active')
    search_fields = ('label',)
    ordering = ('display_order', 'label')


@admin.register(PlantAction)
class PlantActionAdmin(admin.ModelAdmin):
    list_display = (
        'action_date',
        'user',
        'entered_by',
        'plant_series',
        'decision_lever',
        'action_type',
        'scope',
        'molecule',
        'auxiliary_taxon',
    )
    list_filter = ('action_type__category', 'scope', 'department', 'action_date')
    search_fields = ('user__username', 'entered_by__username', 'plant_series__name', 'notes')
