from django.db.models import Q
from .models import AccessControlSettings, PlantSeries, ProducerTechnicianAssignment, UserProfile

ACTING_PRODUCER_SESSION_KEY = 'acting_producer_user_id'
ACTING_TECHNICIAN_SESSION_KEY = 'acting_technician_user_id'


def _get_profile(user):
    profile = UserProfile.objects.get_or_create(user=user)[0]
    if user.is_superuser and profile.assigned_technician_id:
        UserProfile.objects.filter(pk=profile.pk).update(assigned_technician=None)
        profile.assigned_technician = None
    return profile


def _is_technician(user):
    if user.is_superuser:
        return True
    profile = _get_profile(user)
    return profile.role == UserProfile.ROLE_TECHNICIAN


def _technician_has_active_license(user):
    if user.is_superuser:
        return True
    profile = _get_profile(user)
    return profile.role != UserProfile.ROLE_TECHNICIAN or profile.has_active_license


def _can_manage_producers(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return _is_technician(user) and _technician_has_active_license(user)


def _access_control_settings():
    return AccessControlSettings.get_solo()


def _technician_denied_message(profile):
    if profile.deactivation_message:
        return profile.deactivation_message
    return _access_control_settings().default_technician_denied_message


def _technician_visibility_q(user, profile_prefix='user__profile'):
    profile = _get_profile(user)
    if user.is_superuser:
        return Q()
    if profile.role != UserProfile.ROLE_TECHNICIAN:
        return Q(pk__in=[])
    assignment_lookup = f'{profile_prefix}__technician_assignments' if profile_prefix else 'technician_assignments'
    return Q(
        **{
            f'{assignment_lookup}__is_active': True,
            f'{assignment_lookup}__technician': user,
        }
    )


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
        ).distinct()
    return qs.filter(user=user)


def _accessible_producer_profiles(user):
    qs = (
        UserProfile.objects.select_related('user')
        .prefetch_related(
            'user__plant_series',
            'technician_assignments__technician',
            'technician_assignments__technician__profile',
            'technician_assignments__technician__profile__structure',
        )
        .filter(role=UserProfile.ROLE_PRODUCER, user__is_superuser=False)
    )
    if user.is_superuser:
        return qs.order_by('farm_name', 'user__username')
    profile = _get_profile(user)
    if profile.role != UserProfile.ROLE_TECHNICIAN:
        return qs.none()
    return qs.filter(_technician_visibility_q(user, '')).distinct().order_by('farm_name', 'user__username')


def _accessible_technician_profiles(user):
    qs = (
        UserProfile.objects.select_related('user', 'structure')
        .filter(role=UserProfile.ROLE_TECHNICIAN, user__is_superuser=False)
        .order_by('user__first_name', 'user__last_name', 'user__username')
    )
    if user.is_superuser:
        return qs
    return qs.filter(user=user)


def _active_technician_profiles_for_producer(profile, include_inactive_license=False):
    assignments = profile.technician_assignments.filter(
        is_active=True,
        technician__profile__role=UserProfile.ROLE_TECHNICIAN,
    ).select_related('technician', 'technician__profile', 'technician__profile__structure')
    if not include_inactive_license:
        assignments = assignments.filter(technician__profile__license_status=UserProfile.LICENSE_STATUS_ACTIVE)
    return [assignment.technician.profile for assignment in assignments]


def _sync_producer_technicians(
    producer_profile,
    technicians,
    *,
    changed_by=None,
    reason=ProducerTechnicianAssignment.END_REASON_ADMIN_REMOVED,
    message='',
):
    if producer_profile.role != UserProfile.ROLE_PRODUCER:
        return {'added': 0, 'removed': 0, 'active_count': 0}

    desired_technicians = []
    desired_ids = set()
    for technician in technicians:
        if not technician or technician.id in desired_ids:
            continue
        technician_profile = _get_profile(technician)
        if technician_profile.role != UserProfile.ROLE_TECHNICIAN:
            continue
        desired_technicians.append(technician)
        desired_ids.add(technician.id)

    active_assignments = {
        assignment.technician_id: assignment
        for assignment in producer_profile.technician_assignments.filter(is_active=True).select_related('technician')
    }

    removed_count = 0
    added_count = 0

    for technician_id, assignment in active_assignments.items():
        if technician_id in desired_ids:
            continue
        assignment.close(ended_by=changed_by, reason=reason, message=message)
        removed_count += 1

    for technician in desired_technicians:
        if technician.id in active_assignments:
            continue
        ProducerTechnicianAssignment.objects.create(
            producer_profile=producer_profile,
            technician=technician,
            is_active=True,
            created_by=changed_by,
        )
        added_count += 1

    first_technician = desired_technicians[0] if desired_technicians else None
    if producer_profile.assigned_technician_id != (first_technician.id if first_technician else None):
        producer_profile.assigned_technician = first_technician
        producer_profile.save(update_fields=['assigned_technician'])

    return {
        'added': added_count,
        'removed': removed_count,
        'active_count': len(desired_technicians),
    }


def _acting_technician_profile(request):
    if hasattr(request, '_acting_technician_profile_cache'):
        return request._acting_technician_profile_cache

    profile = None
    if request.user.is_authenticated and request.user.is_superuser:
        technician_user_id = request.session.get(ACTING_TECHNICIAN_SESSION_KEY)
        if technician_user_id:
            profile = _accessible_technician_profiles(request.user).filter(user_id=technician_user_id).first()
            if profile is None:
                request.session.pop(ACTING_TECHNICIAN_SESSION_KEY, None)
                request.session.modified = True

    request._acting_technician_profile_cache = profile
    return profile


def _is_acting_as_technician(request):
    return _acting_technician_profile(request) is not None


def _manager_user(request):
    acting_technician = _acting_technician_profile(request)
    return acting_technician.user if acting_technician else request.user


def _acting_producer_profile(request):
    if hasattr(request, '_acting_producer_profile_cache'):
        return request._acting_producer_profile_cache

    profile = None
    manager_user = _manager_user(request)
    if request.user.is_authenticated and _is_technician(manager_user):
        producer_user_id = request.session.get(ACTING_PRODUCER_SESSION_KEY)
        if producer_user_id:
            profile = _accessible_producer_profiles(manager_user).filter(user_id=producer_user_id).first()
            if profile is None:
                request.session.pop(ACTING_PRODUCER_SESSION_KEY, None)
                request.session.modified = True

    request._acting_producer_profile_cache = profile
    return profile


def _is_acting_as_producer(request):
    return _acting_producer_profile(request) is not None


def _effective_user(request):
    acting_producer = _acting_producer_profile(request)
    if acting_producer:
        return acting_producer.user
    acting_technician = _acting_technician_profile(request)
    return acting_technician.user if acting_technician else request.user


def _effective_profile(request):
    acting_producer = _acting_producer_profile(request)
    if acting_producer:
        return acting_producer
    acting_technician = _acting_technician_profile(request)
    return acting_technician or _get_profile(request.user)


def _is_effective_technician_denied(request):
    if not request.user.is_authenticated or request.user.is_superuser:
        return False
    manager_profile = _get_profile(_manager_user(request))
    return manager_profile.role == UserProfile.ROLE_TECHNICIAN and not manager_profile.has_active_license


def _is_effective_producer_read_only(request):
    if not request.user.is_authenticated or request.user.is_superuser:
        return False
    profile = _effective_profile(request)
    return profile.role == UserProfile.ROLE_PRODUCER and not profile.has_active_technician()


def _effective_access_restriction(request, for_write=False):
    if not request.user.is_authenticated:
        return None
    if request.user.is_superuser:
        return None

    if _is_effective_technician_denied(request):
        manager_profile = _get_profile(_manager_user(request))
        return {
            'code': 'technician_denied',
            'message': _technician_denied_message(manager_profile),
        }

    if for_write and _is_effective_producer_read_only(request):
        effective_profile = _effective_profile(request)
        return {
            'code': 'producer_read_only',
            'message': effective_profile.producer_readonly_message(),
        }

    return None


def _show_producer_interface(request):
    if not request.user.is_authenticated:
        return False
    if request.user.is_superuser and not _is_acting_as_producer(request):
        return False
    return _effective_profile(request).role == UserProfile.ROLE_PRODUCER


def _show_technician_interface(request):
    if not request.user.is_authenticated or _is_acting_as_producer(request):
        return False
    manager_user = _manager_user(request)
    if not _is_technician(manager_user):
        return False
    if request.user.is_superuser:
        return True
    return _technician_has_active_license(manager_user)


def _filter_records(request, queryset):
    year = request.GET.get('year')
    crop = request.GET.get('crop')
    department = request.GET.get('department')
    technician = request.GET.get('technician')
    producer = request.GET.get('producer')
    series = request.GET.get('series')

    if year:
        queryset = queryset.filter(year=year)
    if crop:
        queryset = queryset.filter(Q(crop_ref_id=crop) | Q(plant_series__crop_id=crop) | Q(crop=crop))
    if department:
        queryset = queryset.filter(department=department)
    if technician:
        queryset = queryset.filter(
            user__profile__technician_assignments__technician_id=technician,
            user__profile__technician_assignments__is_active=True,
        )
    if producer:
        queryset = queryset.filter(user_id=producer)
    if series:
        queryset = queryset.filter(plant_series_id=series)
    return queryset.distinct()


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
