from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .forms import UserProfileForm
from .models import PlantAction, ScoutingRecord
from .utils import display_user_name
from .view_access import (
    _effective_profile,
    _effective_user,
    _filter_records,
    _get_profile,
    _manager_user,
    _is_technician,
    _is_acting_as_producer,
    _profile_address_context,
    _technician_visibility_q,
)


def _unique_ordered_labels(values):
    labels = []
    seen = set()
    for value in values:
        label = str(value).strip()
        if not label or label in seen:
            continue
        seen.add(label)
        labels.append(label)
    return labels


@login_required
def my_profile_view(request):
    effective_user = _effective_user(request)
    profile = _effective_profile(request)
    technician_profile = _get_profile(profile.assigned_technician) if profile.assigned_technician_id else None
    if request.method == 'POST':
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
        'technician_profile': technician_profile,
    }
    context.update(_profile_address_context(profile))
    return render(request, 'scouting/my_profile.html', context)


@login_required
def my_records_view(request):
    effective_user = _effective_user(request)
    manager_user = _manager_user(request)
    technician_scope = _is_technician(request.user) and not _is_acting_as_producer(request)

    records = (
        ScoutingRecord.objects.select_related('user', 'user__profile', 'plant_series', 'primary_aphid_species')
        .prefetch_related('leaf_observations__aphid_species', 'leaf_observations__auxiliary_observations__taxon')
    )
    actions = PlantAction.objects.select_related(
        'user',
        'user__profile',
        'action_type',
        'plant_series',
        'molecule',
        'auxiliary_taxon',
    )

    if technician_scope:
        if not manager_user.is_superuser:
            visibility_q = _technician_visibility_q(manager_user)
            records = records.filter(visibility_q)
            actions = actions.filter(visibility_q)
        export_scope_all = True
    else:
        records = records.filter(user=effective_user)
        actions = actions.filter(user=effective_user)
        export_scope_all = False

    records = _filter_records(request, records)
    records = records.order_by('-scouting_date', '-created_at')
    actions = actions.order_by('-action_date', '-created_at')

    for rec in records:
        rec.producer_label = rec.user.profile.farm_name or display_user_name(rec.user)
        aphid_species = _unique_ordered_labels(
            leaf.aphid_species for leaf in rec.leaf_observations.all() if leaf.aphid_present and leaf.aphid_species
        )
        auxiliaries = _unique_ordered_labels(
            aux.taxon for leaf in rec.leaf_observations.all() for aux in leaf.auxiliary_observations.all() if aux.count > 0
        )
        rec.aphid_species_list = ', '.join(aphid_species) if aphid_species else '-'
        rec.auxiliary_taxa_list = ', '.join(auxiliaries) if auxiliaries else '-'
    for action in actions:
        action.producer_label = action.user.profile.farm_name or display_user_name(action.user)

    return render(
        request,
        'scouting/my_records.html',
        {
            'records': records,
            'actions': actions,
            'show_producer_column': technician_scope,
            'export_scope_all': export_scope_all,
        },
    )


@login_required
def record_delete_view(request, record_id):
    if request.method != 'POST':
        return redirect('my_records')

    effective_user = _effective_user(request)
    manager_user = _manager_user(request)
    technician_scope = _is_technician(request.user) and not _is_acting_as_producer(request)

    records = ScoutingRecord.objects.select_related('user', 'user__profile')
    if technician_scope:
        if not manager_user.is_superuser:
            records = records.filter(_technician_visibility_q(manager_user))
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

    effective_user = _effective_user(request)
    manager_user = _manager_user(request)
    technician_scope = _is_technician(request.user) and not _is_acting_as_producer(request)

    actions = PlantAction.objects.select_related('user', 'user__profile')
    if technician_scope:
        if not manager_user.is_superuser:
            actions = actions.filter(_technician_visibility_q(manager_user))
    else:
        actions = actions.filter(user=effective_user)

    action = get_object_or_404(actions, id=action_id)
    action.delete()
    messages.success(request, 'Action supprimee.')
    return redirect('my_records')
