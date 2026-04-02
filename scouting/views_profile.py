from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .forms import UserProfileForm
from .models import PlantAction, ScoutingRecord
from .view_access import _filter_records, _get_profile, _profile_address_context

@login_required
def my_profile_view(request):
    profile = _get_profile(request.user)
    technician_profile = _get_profile(profile.assigned_technician) if profile.assigned_technician_id else None
    if request.method == 'POST':
        form = UserProfileForm(request.POST, request.FILES, instance=profile, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Profil mis a jour.')
            return redirect('my_profile')
    else:
        form = UserProfileForm(instance=profile, user=request.user)
    context = {
        'form': form,
        'profile': profile,
        'technician_profile': technician_profile,
    }
    context.update(_profile_address_context(profile))
    return render(request, 'scouting/my_profile.html', context)


@login_required
def my_records_view(request):
    records = ScoutingRecord.objects.filter(user=request.user).prefetch_related('leaf_observations')
    records = _filter_records(request, records)
    actions = PlantAction.objects.filter(user=request.user).select_related(
        'action_type',
        'plant_series',
        'molecule',
        'auxiliary_taxon',
    )
    return render(request, 'scouting/my_records.html', {'records': records, 'actions': actions})


