from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import UserProfileForm
from .models import Department, PlantAction, ScoutingRecord
from .utils import display_user_name
from .view_access import (
    _active_technician_profiles_for_producer,
    _accessible_technician_profiles,
    _effective_access_restriction,
    _effective_profile,
    _effective_user,
    _filter_records,
    _get_profile,
    _manager_user,
    _is_technician,
    _is_acting_as_producer,
    _is_acting_as_technician,
    _profile_address_context,
    _technician_visibility_q,
)


def _unique_ordered_objects(values):
    objects = []
    seen_ids = set()
    for value in values:
        if not value or value.pk in seen_ids:
            continue
        seen_ids.add(value.pk)
        objects.append(value)
    return objects


def _active_technicians(profile):
    return [
        assignment.technician
        for assignment in profile.technician_assignments.all()
        if assignment.is_active
    ]


def _record_aphid_species(record):
    if record.entry_mode == 'quick':
        species = list(
            row.species for row in record.quick_aphid_species.all() if row.species_id
        )
    else:
        species = [
            leaf.aphid_species
            for leaf in record.leaf_observations.all()
            if leaf.aphid_present and leaf.aphid_species_id
        ]
    if record.primary_aphid_species_id:
        species.append(record.primary_aphid_species)
    return _unique_ordered_objects(species)


def _record_auxiliary_taxa(record):
    if record.entry_mode == 'quick':
        return _unique_ordered_objects(
            row.taxon for row in record.quick_auxiliary_counts.all() if row.count > 0
        )
    return _unique_ordered_objects(
        observation.taxon
        for leaf in record.leaf_observations.all()
        for observation in leaf.auxiliary_observations.all()
        if observation.count > 0
    )


def _record_other_pest_taxa(record):
    if record.entry_mode == 'quick':
        return _unique_ordered_objects(
            row.taxon
            for row in record.quick_other_pest_counts.all()
            if row.infested_leaves_count > 0
        )
    return _unique_ordered_objects(
        observation.taxon
        for leaf in record.leaf_observations.all()
        for observation in leaf.other_pest_observations.all()
    )


def _record_filter_value(record, key):
    if key == 'department':
        return str(record.department or '')
    if key == 'technician':
        return str(getattr(record.user.profile, 'assigned_technician_id', '') or '')
    if key == 'producer':
        return str(record.user_id)
    if key == 'crop':
        if record.crop_ref_id:
            return str(record.crop_ref_id)
        if record.plant_series_id and record.plant_series and record.plant_series.crop_id:
            return str(record.plant_series.crop_id)
        return str(record.crop or '')
    if key == 'year':
        return str(record.year or '')
    if key == 'series':
        return str(record.plant_series_id or '')
    return ''


def _record_filter_values(record, key):
    if key == 'technician':
        return {str(technician.id) for technician in _active_technicians(record.user.profile)}
    if key == 'aphid_species':
        return {str(species.id) for species in _record_aphid_species(record)}
    if key == 'auxiliary':
        return {str(taxon.id) for taxon in _record_auxiliary_taxa(record)}
    if key == 'other_pest':
        return {str(taxon.id) for taxon in _record_other_pest_taxa(record)}
    if key == 'entry_mode':
        return {record.entry_mode}
    value = _record_filter_value(record, key)
    return {value} if value else set()


def _action_filter_value(action, key):
    if key == 'department':
        return str(action.department or '')
    if key == 'technician':
        return str(getattr(action.user.profile, 'assigned_technician_id', '') or '')
    if key == 'producer':
        return str(action.user_id)
    if key == 'crop':
        if action.crop_ref_id:
            return str(action.crop_ref_id)
        if action.plant_series_id and action.plant_series and action.plant_series.crop_id:
            return str(action.plant_series.crop_id)
        return ''
    if key == 'year':
        if action.plant_series_id and action.plant_series and action.plant_series.year:
            return str(action.plant_series.year)
        return ''
    if key == 'series':
        return str(action.plant_series_id or '')
    return ''


def _action_filter_values(action, key):
    if key == 'technician':
        return {str(technician.id) for technician in _active_technicians(action.user.profile)}
    value = _action_filter_value(action, key)
    return {value} if value else set()


def _matches_prior_filters(value_getter, item, filters, ordered_keys, current_key):
    for key in ordered_keys:
        if key == current_key:
            break
        selected_value = filters.get(key) or ''
        if selected_value and selected_value not in value_getter(item, key):
            return False
    return True


@login_required
def my_profile_view(request):
    effective_user = _effective_user(request)
    profile = _effective_profile(request)
    technician_profiles = _active_technician_profiles_for_producer(profile)
    if request.method == 'POST':
        restriction = _effective_access_restriction(request, for_write=True)
        if restriction:
            messages.error(request, restriction['message'])
            return redirect('my_profile')
        form = UserProfileForm(request.POST, request.FILES, instance=profile, user=effective_user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Profil mis a jour.')
            return redirect('my_profile')
    else:
        form = UserProfileForm(instance=profile, user=effective_user)
    context = {
        'form': form,
        'profile': profile,
        'profile_user': effective_user,
        'technician_profiles': technician_profiles,
    }
    context.update(_profile_address_context(profile))
    return render(request, 'scouting/my_profile.html', context)


@login_required
def my_records_view(request):
    effective_user = _effective_user(request)
    manager_user = _manager_user(request)
    technician_scope = _is_technician(request.user) and not _is_acting_as_producer(request)
    show_producer_column = technician_scope
    show_department_column = request.user.is_superuser and not _is_acting_as_technician(request) and not _is_acting_as_producer(request)
    show_technician_filter = show_department_column

    base_records = (
        ScoutingRecord.objects.select_related(
            'user',
            'user__profile',
            'user__profile__assigned_technician',
            'plant_series',
            'plant_series__crop',
            'crop_ref',
            'primary_aphid_species',
        )
        .prefetch_related(
            'user__profile__technician_assignments__technician',
            'leaf_observations__aphid_species',
            'leaf_observations__auxiliary_observations__taxon',
            'leaf_observations__other_pest_observations__taxon',
            'quick_aphid_species__species',
            'quick_auxiliary_counts__taxon',
            'quick_other_pest_counts__taxon',
        )
    )
    base_actions = PlantAction.objects.select_related(
        'user',
        'user__profile',
        'user__profile__assigned_technician',
        'action_type',
        'plant_series',
        'plant_series__crop',
        'crop_ref',
        'molecule',
        'auxiliary_taxon',
    ).prefetch_related('user__profile__technician_assignments__technician')

    if technician_scope:
        if not manager_user.is_superuser:
            visibility_q = _technician_visibility_q(manager_user)
            base_records = base_records.filter(visibility_q).distinct()
            base_actions = base_actions.filter(visibility_q).distinct()
        export_scope_all = True
    else:
        base_records = base_records.filter(user=effective_user)
        base_actions = base_actions.filter(user=effective_user)
        export_scope_all = False

    filter_year = (request.GET.get('year') or '').strip()
    filter_crop = (request.GET.get('crop') or '').strip()
    filter_department = (request.GET.get('department') or '').strip()
    filter_technician = (request.GET.get('technician') or '').strip()
    filter_producer = (request.GET.get('producer') or '').strip()
    filter_series = (request.GET.get('series') or '').strip()
    filter_aphid_species = (request.GET.get('aphid_species') or '').strip()
    filter_auxiliary = (request.GET.get('auxiliary') or '').strip()
    filter_other_pest = (request.GET.get('other_pest') or '').strip()
    filter_entry_mode = (request.GET.get('entry_mode') or '').strip()

    records = _filter_records(request, base_records).order_by('-scouting_date', '-created_at')
    actions = base_actions
    if filter_year:
        actions = actions.filter(plant_series__year=filter_year)
    if filter_crop:
        actions = actions.filter(Q(crop_ref_id=filter_crop) | Q(plant_series__crop_id=filter_crop))
    if filter_department:
        actions = actions.filter(department=filter_department)
    if filter_technician:
        actions = actions.filter(
            user__profile__technician_assignments__technician_id=filter_technician,
            user__profile__technician_assignments__is_active=True,
        ).distinct()
    if filter_producer:
        actions = actions.filter(user_id=filter_producer)
    if filter_series:
        actions = actions.filter(plant_series_id=filter_series)
    actions = actions.order_by('-action_date', '-created_at')

    base_records_list = list(base_records)
    base_actions_list = list(base_actions)
    department_labels = {dep.code: dep.label for dep in Department.objects.all()}
    active_filters = {
        'department': filter_department,
        'technician': filter_technician,
        'producer': filter_producer,
        'crop': filter_crop,
        'year': filter_year,
        'series': filter_series,
        'aphid_species': filter_aphid_species,
        'auxiliary': filter_auxiliary,
        'other_pest': filter_other_pest,
        'entry_mode': filter_entry_mode,
    }
    ordered_filter_keys = ['department']
    if show_technician_filter:
        ordered_filter_keys.append('technician')
    if show_producer_column:
        ordered_filter_keys.append('producer')
    ordered_filter_keys.extend(
        ['crop', 'year', 'series', 'aphid_species', 'auxiliary', 'other_pest', 'entry_mode']
    )

    crop_options_map = {}
    series_options_map = {}
    producer_options_map = {}
    technician_options_map = {}
    department_options_map = {}
    aphid_species_options_map = {}
    auxiliary_options_map = {}
    other_pest_options_map = {}
    year_values = set()

    for rec in base_records_list:
        if show_department_column and _matches_prior_filters(
            _record_filter_values,
            rec,
            active_filters,
            ordered_filter_keys,
            'department',
        ):
            department_value = _record_filter_value(rec, 'department')
            if department_value:
                department_options_map[department_value] = department_labels.get(department_value, department_value)
        if show_technician_filter and _matches_prior_filters(
            _record_filter_values,
            rec,
            active_filters,
            ordered_filter_keys,
            'technician',
        ):
            for technician in _active_technicians(rec.user.profile):
                technician_options_map[technician.id] = display_user_name(technician)
        if show_producer_column and _matches_prior_filters(
            _record_filter_values,
            rec,
            active_filters,
            ordered_filter_keys,
            'producer',
        ):
            producer_options_map[rec.user_id] = rec.user.profile.farm_name or display_user_name(rec.user)
        if _matches_prior_filters(_record_filter_values, rec, active_filters, ordered_filter_keys, 'crop'):
            crop = rec.crop_ref or (rec.plant_series.crop if rec.plant_series_id and rec.plant_series else None)
            if crop:
                crop_options_map[crop.id] = crop.name
        if _matches_prior_filters(_record_filter_values, rec, active_filters, ordered_filter_keys, 'year') and rec.year:
            year_values.add(rec.year)
        if _matches_prior_filters(_record_filter_values, rec, active_filters, ordered_filter_keys, 'series'):
            if rec.plant_series_id and rec.plant_series:
                series_label = rec.plant_series.name
                if show_producer_column:
                    series_label = f"{rec.plant_series.name} - {rec.user.profile.farm_name or display_user_name(rec.user)}"
                series_options_map[rec.plant_series_id] = series_label
        if technician_scope and _matches_prior_filters(
            _record_filter_values,
            rec,
            active_filters,
            ordered_filter_keys,
            'aphid_species',
        ):
            for species in _record_aphid_species(rec):
                aphid_species_options_map[species.id] = str(species)
        if technician_scope and _matches_prior_filters(
            _record_filter_values,
            rec,
            active_filters,
            ordered_filter_keys,
            'auxiliary',
        ):
            for taxon in _record_auxiliary_taxa(rec):
                auxiliary_options_map[taxon.id] = taxon.name
        if technician_scope and _matches_prior_filters(
            _record_filter_values,
            rec,
            active_filters,
            ordered_filter_keys,
            'other_pest',
        ):
            for taxon in _record_other_pest_taxa(rec):
                other_pest_options_map[taxon.id] = taxon.name

    for action in base_actions_list:
        if show_department_column and _matches_prior_filters(
            _action_filter_values,
            action,
            active_filters,
            ordered_filter_keys,
            'department',
        ):
            department_value = _action_filter_value(action, 'department')
            if department_value:
                department_options_map[department_value] = department_labels.get(department_value, department_value)
        if show_technician_filter and _matches_prior_filters(
            _action_filter_values,
            action,
            active_filters,
            ordered_filter_keys,
            'technician',
        ):
            for technician in _active_technicians(action.user.profile):
                technician_options_map[technician.id] = display_user_name(technician)
        if show_producer_column and _matches_prior_filters(
            _action_filter_values,
            action,
            active_filters,
            ordered_filter_keys,
            'producer',
        ):
            producer_options_map[action.user_id] = action.user.profile.farm_name or display_user_name(action.user)
        if _matches_prior_filters(_action_filter_values, action, active_filters, ordered_filter_keys, 'crop'):
            crop = action.crop_ref or (action.plant_series.crop if action.plant_series_id and action.plant_series else None)
            if crop:
                crop_options_map[crop.id] = crop.name
        if _matches_prior_filters(_action_filter_values, action, active_filters, ordered_filter_keys, 'year'):
            if action.plant_series_id and action.plant_series and action.plant_series.year:
                year_values.add(action.plant_series.year)
        if _matches_prior_filters(_action_filter_values, action, active_filters, ordered_filter_keys, 'series'):
            if action.plant_series_id and action.plant_series:
                series_label = action.plant_series.name
                if show_producer_column:
                    series_label = f"{action.plant_series.name} - {action.user.profile.farm_name or display_user_name(action.user)}"
                series_options_map[action.plant_series_id] = series_label

    for rec in records:
        rec.producer_label = rec.user.profile.farm_name or display_user_name(rec.user)
        rec.crop_label = (
            rec.crop_ref.name
            if rec.crop_ref_id and rec.crop_ref
            else (rec.plant_series.crop.name if rec.plant_series_id and rec.plant_series else rec.crop or '-')
        )
        aphid_species = _record_aphid_species(rec)
        auxiliaries = _record_auxiliary_taxa(rec)
        other_pests = _record_other_pest_taxa(rec)
        rec.aphid_species_label = str(rec.primary_aphid_species) if rec.primary_aphid_species_id else '-'
        rec.infestation_pct = float(rec.aphid_infested_percent or 0)
        rec.aphid_species_list = ', '.join(str(item) for item in aphid_species) if aphid_species else '-'
        rec.auxiliary_species_list = ', '.join(str(item) for item in auxiliaries) if auxiliaries else '-'
        rec.other_pest_species_list = ', '.join(str(item) for item in other_pests) if other_pests else '-'
    for action in actions:
        action.producer_label = action.user.profile.farm_name or display_user_name(action.user)
        action.crop_label = (
            action.crop_ref.name
            if action.crop_ref_id and action.crop_ref
            else (action.plant_series.crop.name if action.plant_series_id and action.plant_series and action.plant_series.crop_id else '-')
        )
        action.week_label = action.action_date.isocalendar().week if action.action_date else '-'

    export_params = request.GET.copy()
    if export_scope_all:
        export_params['scope'] = 'all'
    else:
        export_params.pop('scope', None)
    export_url = reverse('export_records')
    encoded_params = export_params.urlencode()
    if encoded_params:
        export_url = f'{export_url}?{encoded_params}'

    export_actions_url = reverse('export_actions')
    if encoded_params:
        export_actions_url = f'{export_actions_url}?{encoded_params}'

    return render(
        request,
        'scouting/my_records.html',
        {
            'records': records,
            'actions': actions,
            'show_producer_column': show_producer_column,
            'show_department_column': show_department_column,
            'export_scope_all': export_scope_all,
            'export_url': export_url,
            'export_actions_url': export_actions_url,
            'filter_values': {
                'year': filter_year,
                'crop': filter_crop,
                'department': filter_department,
                'technician': filter_technician,
                'producer': filter_producer,
                'series': filter_series,
                'aphid_species': filter_aphid_species,
                'auxiliary': filter_auxiliary,
                'other_pest': filter_other_pest,
                'entry_mode': filter_entry_mode,
            },
            'show_technician_filter': show_technician_filter,
            'show_advanced_filters': technician_scope,
            'year_options': sorted(year_values, reverse=True),
            'crop_options': sorted(crop_options_map.items(), key=lambda item: item[1].lower()),
            'series_options': sorted(series_options_map.items(), key=lambda item: item[1].lower()),
            'producer_options': sorted(producer_options_map.items(), key=lambda item: item[1].lower()),
            'technician_options': sorted(technician_options_map.items(), key=lambda item: item[1].lower()),
            'department_options': sorted(department_options_map.items(), key=lambda item: item[0]),
            'aphid_species_options': sorted(
                aphid_species_options_map.items(),
                key=lambda item: item[1].lower(),
            ),
            'auxiliary_options': sorted(auxiliary_options_map.items(), key=lambda item: item[1].lower()),
            'other_pest_options': sorted(other_pest_options_map.items(), key=lambda item: item[1].lower()),
        },
    )


@login_required
def record_delete_view(request, record_id):
    if request.method != 'POST':
        return redirect('my_records')
    restriction = _effective_access_restriction(request, for_write=True)
    if restriction:
        messages.error(request, restriction['message'])
        return redirect('my_records')

    effective_user = _effective_user(request)
    manager_user = _manager_user(request)
    technician_scope = _is_technician(request.user) and not _is_acting_as_producer(request)

    records = ScoutingRecord.objects.select_related('user', 'user__profile')
    if technician_scope:
        if not manager_user.is_superuser:
            records = records.filter(_technician_visibility_q(manager_user)).distinct()
    else:
        records = records.filter(user=effective_user)

    record = get_object_or_404(records, id=record_id)
    record.delete()
    messages.success(request, 'Comptage supprime.')
    return redirect('my_records')


@login_required
def action_delete_view(request, action_id):
    if request.method != 'POST':
        return redirect('my_records')
    restriction = _effective_access_restriction(request, for_write=True)
    if restriction:
        messages.error(request, restriction['message'])
        return redirect('my_records')

    effective_user = _effective_user(request)
    manager_user = _manager_user(request)
    technician_scope = _is_technician(request.user) and not _is_acting_as_producer(request)

    actions = PlantAction.objects.select_related('user', 'user__profile')
    if technician_scope:
        if not manager_user.is_superuser:
            actions = actions.filter(_technician_visibility_q(manager_user)).distinct()
    else:
        actions = actions.filter(user=effective_user)

    action = get_object_or_404(actions, id=action_id)
    action.delete()
    messages.success(request, 'Action supprimee.')
    return redirect('my_records')
