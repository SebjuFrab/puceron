from django.db.models import Q

from .models import PlantSeries, UserProfile

def _get_profile(user):
    return UserProfile.objects.get_or_create(user=user)[0]


def _is_technician(user):
    if user.is_superuser:
        return True
    profile = _get_profile(user)
    return profile.role == UserProfile.ROLE_TECHNICIAN


def _can_manage_producers(user):
    return bool(user.is_authenticated and (user.is_superuser or _is_technician(user)))


def _technician_visibility_q(user, profile_prefix='user__profile'):
    profile = _get_profile(user)
    assigned_lookup = f'{profile_prefix}__assigned_technician' if profile_prefix else 'assigned_technician'
    department_lookup = f'{profile_prefix}__department' if profile_prefix else 'department'
    base_query = Q(**{assigned_lookup: user})
    if profile.department:
        base_query |= Q(**{f'{assigned_lookup}__isnull': True, department_lookup: profile.department})
    return base_query


def _series_queryset_for_user(user):
    qs = PlantSeries.objects.select_related('crop', 'conduct_type', 'variety', 'user', 'user__profile').filter(
        is_active=True
    )
    if user.is_superuser:
        return qs
    profile = _get_profile(user)
    if profile.role == UserProfile.ROLE_TECHNICIAN:
        return qs.filter(user__profile__role=UserProfile.ROLE_PRODUCER).filter(
            _technician_visibility_q(user, 'user__profile')
        )
    return qs.filter(user=user)


def _accessible_producer_profiles(user):
    qs = (
        UserProfile.objects.select_related('user', 'assigned_technician')
        .prefetch_related('user__plant_series')
        .filter(role=UserProfile.ROLE_PRODUCER)
    )
    if user.is_superuser:
        return qs.order_by('farm_name', 'user__username')
    return qs.filter(_technician_visibility_q(user, '')).order_by('farm_name', 'user__username')


def _filter_records(request, queryset):
    year = request.GET.get('year')
    crop = request.GET.get('crop')
    department = request.GET.get('department')
    producer = request.GET.get('producer')

    if year:
        queryset = queryset.filter(year=year)
    if crop:
        queryset = queryset.filter(crop=crop)
    if department:
        queryset = queryset.filter(department=department)
    if producer:
        queryset = queryset.filter(user_id=producer)
    return queryset


def _parse_count(value):
    if value in (None, ''):
        return 0
    try:
        parsed = int(value)
    except ValueError:
        return 0
    return max(parsed, 0)


def _parse_positive_int(value, default=None):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default

def _target_user_for_series(request_user, selected_series, is_tech_user):
    if is_tech_user:
        return selected_series.user
    if selected_series.user_id == request_user.id:
        return request_user
    return selected_series.user


def _profile_address_context(profile):
    return {
        'profile_map_initial_lat': float(profile.latitude) if profile.latitude is not None else 46.603354,
        'profile_map_initial_lng': float(profile.longitude) if profile.longitude is not None else 1.888334,
        'profile_has_coordinates': profile.latitude is not None and profile.longitude is not None,
    }
