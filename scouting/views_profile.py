from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

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
        ScoutingRecord.objects.select_related('user', 'user__profile', 'plant_series')
        .prefetch_related('leaf_observations')
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
