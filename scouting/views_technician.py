import json
from io import BytesIO

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import strip_tags
from django.utils import timezone

try:
    from openpyxl import Workbook
except Exception:  # pragma: no cover
    Workbook = None

from .decision_engine import evaluate_record_recommendation
from .forms import BulletinMessageForm, TechnicianCoFollowRequestForm, TechnicianDeactivationForm
from .models import (
    AuxiliaryTaxon,
    BulletinMessage,
    BulletinRecipient,
    NotificationDelivery,
    NotificationPreference,
    PlantAction,
    PlantSeries,
    ProducerTechnicianAssignment,
    ScoutingRecord,
    TechnicianCoFollowRequest,
    TechnicianCoFollowRequestItem,
    UserProfile,
)
from .utils import display_user_name
from .views_support import (
    ACTING_PRODUCER_SESSION_KEY,
    ACTING_TECHNICIAN_SESSION_KEY,
    _accessible_technician_profiles,
    _accessible_producer_profiles,
    _acting_technician_profile,
    _acting_producer_profile,
    _effective_user,
    _filter_records,
    _get_profile,
    _is_technician,
    _manager_user,
    _latest_series_recommendation,
    _sync_producer_technicians,
    _technician_visibility_q,
)

User = get_user_model()


def _active_technician_names_for_profile(profile):
    assignments = getattr(profile, 'active_assignments_prefetched', None)
    if assignments is None:
        assignments = profile.technician_assignments.filter(is_active=True).select_related('technician')
    names = []
    seen = set()
    for assignment in assignments:
        name = display_user_name(assignment.technician)
        if name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _require_bulletin_technician(request):
    manager_user = _manager_user(request)
    manager_profile = _get_profile(manager_user)
    if not _is_technician(manager_user):
        messages.error(request, 'Acces reserve aux techniciens.')
        return None, None, redirect('dashboard')
    if request.user.is_superuser and manager_user == request.user:
        messages.error(
            request,
            'Selectionnez un technicien en mode controle pour gerer ses bulletins.',
        )
        return None, None, redirect('technician_records')
    if (not request.user.is_superuser) and not manager_profile.has_active_license:
        messages.error(request, manager_profile.deactivation_message or 'Votre licence technicien est inactive.')
        return None, None, redirect('dashboard')
    return manager_user, manager_profile, None


def _bulletin_producer_queryset(manager_user):
    return _accessible_producer_profiles(manager_user).select_related('user').distinct()


def _send_bulletin_email_notifications(request, recipient_ids):
    recipients = (
        BulletinRecipient.objects.filter(id__in=recipient_ids)
        .select_related('bulletin', 'bulletin__priority', 'producer_profile', 'producer_profile__user')
        .prefetch_related('bulletin__types')
    )
    for recipient in recipients:
        producer_user = recipient.producer_profile.user
        delivery = NotificationDelivery.objects.create(
            recipient=recipient,
            channel=NotificationDelivery.CHANNEL_EMAIL,
            status=NotificationDelivery.STATUS_PENDING,
        )
        if not producer_user.email:
            delivery.status = NotificationDelivery.STATUS_SKIPPED
            delivery.error = 'Email producteur non renseigne.'
            delivery.save(update_fields=['status', 'error'])
            continue
        preference = NotificationPreference.objects.get_or_create(user=producer_user)[0]
        if not preference.wants_bulletin_email(recipient.bulletin):
            delivery.status = NotificationDelivery.STATUS_SKIPPED
            delivery.error = 'Desactive par le producteur.'
            delivery.save(update_fields=['status', 'error'])
            continue

        detail_url = request.build_absolute_uri(reverse('my_bulletin_detail', args=[recipient.id]))
        body_preview = strip_tags(recipient.bulletin.body or '').strip()
        if len(body_preview) > 700:
            body_preview = f'{body_preview[:700]}...'
        try:
            send_mail(
                subject=f'Nouveau bulletin PUCERON: {recipient.bulletin.title}',
                message=(
                    f'Un nouveau bulletin est disponible dans PUCERON.\n\n'
                    f'Titre: {recipient.bulletin.title}\n'
                    f'Types: {recipient.bulletin.type_labels or "-"}\n'
                    f'Priorite: {recipient.bulletin.priority_label or "-"}\n\n'
                    f'{body_preview}\n\n'
                    f'Ouvrir le bulletin: {detail_url}'
                ),
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                recipient_list=[producer_user.email],
                fail_silently=False,
            )
        except Exception as exc:
            delivery.status = NotificationDelivery.STATUS_FAILED
            delivery.error = str(exc)
            delivery.save(update_fields=['status', 'error'])
            continue

        delivery.status = NotificationDelivery.STATUS_SENT
        delivery.sent_at = timezone.now()
        delivery.save(update_fields=['status', 'sent_at'])


@login_required
def technician_bulletin_list_view(request):
    manager_user, manager_profile, error_response = _require_bulletin_technician(request)
    if error_response:
        return error_response

    bulletins = list(
        BulletinMessage.objects.filter(author=manager_user)
        .select_related('author', 'priority')
        .prefetch_related('types', 'crops', 'departments')
        .annotate(
            recipient_count=Count('recipients', distinct=True),
            opened_count=Count(
                'recipients',
                filter=Q(recipients__first_opened_at__isnull=False),
                distinct=True,
            ),
            acknowledged_count=Count(
                'recipients',
                filter=Q(recipients__acknowledged_at__isnull=False),
                distinct=True,
            ),
        )
    )
    for bulletin in bulletins:
        bulletin.pending_acknowledgement_count = max(
            bulletin.recipient_count - bulletin.acknowledged_count,
            0,
        )

    return render(
        request,
        'scouting/technician_bulletin_list.html',
        {
            'bulletins': bulletins,
            'manager_user': manager_user,
            'manager_profile': manager_profile,
        },
    )


@login_required
def technician_bulletin_create_view(request):
    manager_user, manager_profile, error_response = _require_bulletin_technician(request)
    if error_response:
        return error_response

    producer_queryset = _bulletin_producer_queryset(manager_user)
    has_available_producers = producer_queryset.exists()
    if request.method == 'POST':
        form = BulletinMessageForm(request.POST, request.FILES, producer_queryset=producer_queryset)
        if form.is_valid():
            with transaction.atomic():
                bulletin = form.save(commit=False)
                bulletin.author = manager_user
                bulletin.created_by = request.user
                bulletin.status = BulletinMessage.STATUS_SENT
                bulletin.sent_at = timezone.now()
                bulletin.save()
                form.save_m2m()
                for attachment in form.attachment_objects(bulletin):
                    attachment.save()
                producers = list(form.cleaned_data['producers'])
                BulletinRecipient.objects.bulk_create(
                    [
                        BulletinRecipient(
                            bulletin=bulletin,
                            producer_profile=producer_profile,
                        )
                        for producer_profile in producers
                    ]
                )
                recipient_ids = list(
                    BulletinRecipient.objects.filter(
                        bulletin=bulletin,
                        producer_profile__in=producers,
                    ).values_list('id', flat=True)
                )
                transaction.on_commit(lambda: _send_bulletin_email_notifications(request, recipient_ids))
            messages.success(request, 'Bulletin envoye aux producteurs selectionnes.')
            return redirect('technician_bulletin_detail', bulletin.id)
        editor_body = getattr(form, 'sanitized_body', '')
    else:
        form = BulletinMessageForm(producer_queryset=producer_queryset)
        editor_body = ''

    return render(
        request,
        'scouting/technician_bulletin_form.html',
        {
            'form': form,
            'manager_user': manager_user,
            'manager_profile': manager_profile,
            'has_available_producers': has_available_producers,
            'editor_body': editor_body,
        },
    )


@login_required
def technician_bulletin_detail_view(request, bulletin_id):
    manager_user, manager_profile, error_response = _require_bulletin_technician(request)
    if error_response:
        return error_response

    bulletin = get_object_or_404(
        BulletinMessage.objects.select_related('author', 'priority').prefetch_related(
            'types',
            'crops',
            'departments',
            'attachments',
        ),
        pk=bulletin_id,
        author=manager_user,
    )
    photos = [attachment for attachment in bulletin.attachments.all() if attachment.is_photo]
    files = [attachment for attachment in bulletin.attachments.all() if not attachment.is_photo]
    recipients = list(
        bulletin.recipients.select_related(
            'producer_profile',
            'producer_profile__user',
            'acknowledged_by',
        )
    )
    recipient_count = len(recipients)
    opened_count = sum(1 for recipient in recipients if recipient.first_opened_at)
    acknowledged_count = sum(1 for recipient in recipients if recipient.acknowledged_at)
    stats = {
        'recipient_count': recipient_count,
        'not_opened_count': max(recipient_count - opened_count, 0),
        'opened_count': opened_count,
        'opened_without_ack_count': max(opened_count - acknowledged_count, 0),
        'acknowledged_count': acknowledged_count,
    }

    return render(
        request,
        'scouting/technician_bulletin_detail.html',
        {
            'bulletin': bulletin,
            'recipients': recipients,
            'photos': photos,
            'files': files,
            'stats': stats,
            'manager_user': manager_user,
            'manager_profile': manager_profile,
        },
    )


@login_required
def technician_records_view(request):
    if not _is_technician(request.user):
        messages.error(request, 'Acces reserve aux techniciens.')
        return redirect('dashboard')

    manager_user = _manager_user(request)
    technician_profiles = list(_accessible_technician_profiles(request.user)) if request.user.is_superuser else []
    active_technician_profile = _acting_technician_profile(request)
    if request.user.is_superuser and active_technician_profile:
        producer_profiles = list(
            _accessible_producer_profiles(request.user)
            .filter(
                technician_assignments__technician_id=active_technician_profile.user_id,
                technician_assignments__is_active=True,
            )
            .distinct()
        )
    else:
        producer_profiles = list(_accessible_producer_profiles(manager_user))
    active_control_profile = _acting_producer_profile(request)
    can_create_cofollow_request = (not request.user.is_superuser) or (
        request.user.is_superuser and active_technician_profile is not None
    )
    incoming_cofollow_requests = []
    outgoing_cofollow_requests = []
    if can_create_cofollow_request:
        incoming_cofollow_requests = list(
            TechnicianCoFollowRequest.objects.filter(target_technician=manager_user)
            .select_related('source_technician', 'target_technician')
            .annotate(producer_count=Count('items', distinct=True))
            .order_by('-created_at')[:8]
        )
        outgoing_cofollow_requests = list(
            TechnicianCoFollowRequest.objects.filter(source_technician=manager_user)
            .select_related('source_technician', 'target_technician')
            .annotate(producer_count=Count('items', distinct=True))
            .order_by('-created_at')[:8]
        )
    selected_producer = None
    selected_producer_id = request.GET.get('producer')

    if selected_producer_id:
        selected_producer = next(
            (profile for profile in producer_profiles if str(profile.user_id) == str(selected_producer_id)),
            None,
        )
        if selected_producer is None:
            messages.error(request, 'Le producteur sélectionné est introuvable ou hors périmètre.')

    producer_map_data = []
    producers_without_coordinates = []
    selected_producer_data = None

    for profile in producer_profiles:
        active_series = [series for series in profile.user.plant_series.all() if series.is_active]
        first_photo_series = next((series for series in active_series if series.photo), None)
        producer_name = profile.farm_name or display_user_name(profile.user)
        producer_data = {
            'id': profile.user_id,
            'name': producer_name,
            'display_name': display_user_name(profile.user),
            'username': profile.user.username,
            'address': profile.full_address,
            'photo_url': first_photo_series.photo.url if first_photo_series and first_photo_series.photo else '',
            'series_count': len(active_series),
            'target_url': f"{reverse('technician_records')}?producer={profile.user_id}",
            'is_selected': bool(selected_producer and selected_producer.user_id == profile.user_id),
            'is_controlled': bool(active_control_profile and active_control_profile.user_id == profile.user_id),
        }
        if profile.latitude is not None and profile.longitude is not None:
            producer_data['lat'] = float(profile.latitude)
            producer_data['lng'] = float(profile.longitude)
            producer_map_data.append(producer_data)
        else:
            producers_without_coordinates.append(producer_data)
        if selected_producer and selected_producer.user_id == profile.user_id:
            selected_producer_data = producer_data

    records = ScoutingRecord.objects.select_related(
        'user',
        'plant_series',
        'crop_ref',
        'conduct_type_ref',
        'variety_ref',
    ).prefetch_related(
        'leaf_observations',
        'recommendation_responses__dismiss_reason',
        'recommendation_responses__lever',
        'recommendation_responses__action',
    )
    actions = PlantAction.objects.select_related(
        'user',
        'action_type',
        'plant_series',
        'molecule',
        'auxiliary_taxon',
    )
    if not manager_user.is_superuser:
        visibility_query = _technician_visibility_q(manager_user)
        records = records.filter(visibility_query).distinct()
        actions = actions.filter(visibility_query).distinct()

    if selected_producer:
        records = list(records.filter(user=selected_producer.user))
        for record in records:
            record.recommendation = evaluate_record_recommendation(record)
        actions = actions.filter(user=selected_producer.user)
        selected_series = list(
            PlantSeries.objects.filter(user=selected_producer.user, is_active=True)
            .select_related('crop', 'conduct_type', 'variety')
            .prefetch_related(
                Prefetch(
                    'records',
                    queryset=ScoutingRecord.objects.select_related('crop_ref', 'plant_series').prefetch_related(
                        'leaf_observations',
                        'recommendation_responses__dismiss_reason',
                        'recommendation_responses__lever',
                        'recommendation_responses__action',
                    ),
                )
            )
            .order_by('name')
        )
        for series in selected_series:
            _latest_series_recommendation(series)
    else:
        records = records.none()
        actions = actions.none()
        selected_series = []

    actions = actions.order_by('-action_date', '-created_at')
    return render(
        request,
        'scouting/technician_records.html',
        {
            'records': records,
            'actions': actions,
            'producer_profiles': producer_profiles,
            'technician_profiles': technician_profiles,
            'active_technician_profile': active_technician_profile,
            'producer_map_data_json': json.dumps(producer_map_data),
            'mapped_producers_count': len(producer_map_data),
            'producers_without_coordinates': producers_without_coordinates,
            'selected_producer': selected_producer,
            'selected_producer_data': selected_producer_data,
            'selected_series': selected_series,
            'active_control_profile': active_control_profile,
            'can_create_cofollow_request': can_create_cofollow_request,
            'incoming_cofollow_requests': incoming_cofollow_requests,
            'outgoing_cofollow_requests': outgoing_cofollow_requests,
        },
    )


@login_required
def technician_producer_management_view(request):
    manager_user = _manager_user(request)
    manager_profile = _get_profile(manager_user)
    if not _is_technician(manager_user):
        messages.error(request, 'Acces reserve aux techniciens.')
        return redirect('dashboard')
    if request.user.is_superuser and manager_user == request.user:
        messages.error(
            request,
            'Selectionnez un technicien en mode controle pour acceder a la gestion des producteurs.',
        )
        return redirect('technician_records')
    if (not request.user.is_superuser) and not manager_profile.has_active_license:
        messages.error(request, manager_profile.deactivation_message or 'Votre licence technicien est inactive.')
        return redirect('dashboard')

    active_assignment_prefetch = Prefetch(
        'technician_assignments',
        queryset=ProducerTechnicianAssignment.objects.filter(is_active=True).select_related(
            'technician',
            'technician__profile',
        ),
        to_attr='active_assignments_prefetched',
    )

    managed_profiles = list(
        _accessible_producer_profiles(manager_user)
        .select_related('user')
        .prefetch_related(active_assignment_prefetch)
        .annotate(
            active_series_count=Count(
                'user__plant_series',
                filter=Q(user__plant_series__is_active=True),
                distinct=True,
            ),
            observation_count=Count('user__records', distinct=True),
        )
        .order_by('farm_name', 'user__username')
    )
    managed_rows = [
        {
            'producer_id': profile.user_id,
            'producer_name': profile.farm_name or display_user_name(profile.user),
            'producer_username': profile.user.username,
            'series_count': getattr(profile, 'active_series_count', 0),
            'observation_count': getattr(profile, 'observation_count', 0),
            'last_connection_at': profile.user.last_login,
            'technician_names': _active_technician_names_for_profile(profile),
        }
        for profile in managed_profiles
    ]

    pending_items = list(
        TechnicianCoFollowRequestItem.objects.filter(
            request__target_technician=manager_user,
            request__status=TechnicianCoFollowRequest.STATUS_PENDING,
            decision=TechnicianCoFollowRequestItem.DECISION_PENDING,
        )
        .select_related('request', 'request__source_technician', 'producer_profile', 'producer_profile__user')
        .prefetch_related(
            Prefetch(
                'producer_profile__technician_assignments',
                queryset=ProducerTechnicianAssignment.objects.filter(is_active=True).select_related(
                    'technician',
                    'technician__profile',
                ),
                to_attr='active_assignments_prefetched',
            )
        )
        .annotate(
            active_series_count=Count(
                'producer_profile__user__plant_series',
                filter=Q(producer_profile__user__plant_series__is_active=True),
                distinct=True,
            ),
            observation_count=Count('producer_profile__user__records', distinct=True),
        )
        .order_by('-request__created_at', 'producer_profile__farm_name', 'producer_profile__user__username')
    )
    pending_rows = [
        {
            'request_id': item.request_id,
            'request_created_at': item.request.created_at,
            'request_source_name': display_user_name(item.request.source_technician),
            'producer_id': item.producer_profile.user_id,
            'producer_name': item.producer_profile.farm_name or display_user_name(item.producer_profile.user),
            'producer_username': item.producer_profile.user.username,
            'series_count': getattr(item, 'active_series_count', 0),
            'observation_count': getattr(item, 'observation_count', 0),
            'last_connection_at': item.producer_profile.user.last_login,
            'technician_names': _active_technician_names_for_profile(item.producer_profile),
        }
        for item in pending_items
    ]
    pending_request_count = len({item.request_id for item in pending_items})

    return render(
        request,
        'scouting/technician_producer_management.html',
        {
            'manager_user': manager_user,
            'managed_rows': managed_rows,
            'pending_rows': pending_rows,
            'managed_count': len(managed_rows),
            'pending_item_count': len(pending_rows),
            'pending_request_count': pending_request_count,
        },
    )


@login_required
def producer_control_start_view(request, producer_id):
    if request.method != 'POST':
        return redirect('technician_records')
    manager_user = _manager_user(request)
    if not _is_technician(manager_user):
        messages.error(request, 'Acces reserve aux techniciens.')
        return redirect('dashboard')

    if request.user.is_superuser and manager_user != request.user:
        producer_scope = _accessible_producer_profiles(request.user).filter(
            technician_assignments__technician_id=manager_user.id,
            technician_assignments__is_active=True,
        )
    else:
        producer_scope = _accessible_producer_profiles(manager_user)
    producer_profile = get_object_or_404(producer_scope.distinct(), user_id=producer_id)
    request.session[ACTING_PRODUCER_SESSION_KEY] = producer_profile.user_id
    request.session.modified = True
    if hasattr(request, '_acting_technician_profile_cache'):
        delattr(request, '_acting_technician_profile_cache')
    if hasattr(request, '_acting_producer_profile_cache'):
        delattr(request, '_acting_producer_profile_cache')
    messages.success(request, f'Interface producteur active pour {producer_profile.farm_name or display_user_name(producer_profile.user)}.')
    return redirect('dashboard')


@login_required
def producer_control_stop_view(request):
    if request.method != 'POST':
        return redirect('technician_records')
    request.session.pop(ACTING_PRODUCER_SESSION_KEY, None)
    request.session.modified = True
    if hasattr(request, '_acting_producer_profile_cache'):
        delattr(request, '_acting_producer_profile_cache')
    messages.success(request, 'Retour a la vue technicien.')
    return redirect('technician_records')


@login_required
def technician_stop_follow_view(request, producer_id):
    if request.method != 'POST':
        return redirect('technician_records')

    manager_user = _manager_user(request)
    manager_profile = _get_profile(manager_user)
    if not _is_technician(manager_user):
        messages.error(request, 'Acces reserve aux techniciens.')
        return redirect('dashboard')
    if (not request.user.is_superuser) and not manager_profile.has_active_license:
        messages.error(request, manager_profile.deactivation_message or 'Votre licence technicien est inactive.')
        return redirect('dashboard')

    if request.user.is_superuser and manager_user != request.user:
        producer_scope = _accessible_producer_profiles(request.user).filter(
            technician_assignments__technician_id=manager_user.id,
            technician_assignments__is_active=True,
        )
    else:
        producer_scope = _accessible_producer_profiles(manager_user)
    producer_profile = get_object_or_404(producer_scope.distinct(), user_id=producer_id)

    explanation = (request.POST.get('message') or '').strip()
    assignment = producer_profile.technician_assignments.filter(
        is_active=True,
        technician=manager_user,
    ).first()
    if assignment is None:
        messages.error(request, 'Aucune affectation active a retirer pour ce producteur.')
        return redirect(f'{reverse("technician_records")}?producer={producer_id}')

    assignment.close(
        ended_by=request.user,
        reason=ProducerTechnicianAssignment.END_REASON_TECHNICIAN_STOP,
        message=explanation,
    )

    remaining_technicians = [
        row.technician
        for row in producer_profile.technician_assignments.filter(is_active=True).select_related('technician')
    ]
    _sync_producer_technicians(
        producer_profile,
        remaining_technicians,
        changed_by=request.user,
        reason=ProducerTechnicianAssignment.END_REASON_TECHNICIAN_STOP,
        message=explanation,
    )

    messages.success(request, 'Arret de suivi enregistre.')
    return redirect('technician_records')


@login_required
def technician_cofollow_request_create_view(request):
    manager_user = _manager_user(request)
    manager_profile = _get_profile(manager_user)
    if not _is_technician(manager_user):
        messages.error(request, 'Acces reserve aux techniciens.')
        return redirect('dashboard')
    if (not request.user.is_superuser) and not manager_profile.has_active_license:
        messages.error(request, manager_profile.deactivation_message or 'Votre licence technicien est inactive.')
        return redirect('dashboard')
    if request.user.is_superuser and manager_user == request.user:
        messages.error(
            request,
            'Selectionnez un technicien en mode controle pour envoyer une demande de co-suivi.',
        )
        return redirect('technician_records')

    producer_profiles = _accessible_producer_profiles(manager_user)
    producer_users = User.objects.filter(
        id__in=producer_profiles.values_list('user_id', flat=True)
    ).select_related('profile').order_by('profile__farm_name', 'username')
    has_available_producers = producer_users.exists()
    if not has_available_producers:
        messages.warning(
            request,
            'Aucun producteur rattache au technicien source. Verifiez les affectations technicien <-> producteur.',
        )

    if request.method == 'POST':
        form = TechnicianCoFollowRequestForm(
            request.POST,
            source_technician=manager_user,
            producer_queryset=producer_users,
        )
        if form.is_valid():
            request_obj = form.save()
            review_url = request.build_absolute_uri(
                reverse('technician_cofollow_review', args=[request_obj.id])
            )
            target_email = request_obj.target_technician.email
            if target_email:
                send_mail(
                    subject='Nouvelle demande de co-suivi producteur',
                    message=(
                        f'{display_user_name(manager_user)} vous propose de co-suivre des producteurs.\n\n'
                        f'Lien de traitement: {review_url}\n\n'
                        f'Message: {request_obj.message or "-"}'
                    ),
                    from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                    recipient_list=[target_email],
                    fail_silently=True,
                )
            messages.success(request, 'Demande envoyee au technicien cible.')
            return redirect('technician_records')
    else:
        form = TechnicianCoFollowRequestForm(
            source_technician=manager_user,
            producer_queryset=producer_users,
        )

    return render(
        request,
        'scouting/technician_cofollow_request.html',
        {
            'form': form,
            'source_technician': manager_user,
            'has_available_producers': has_available_producers,
        },
    )


@login_required
def technician_cofollow_review_view(request, request_id):
    request_obj = get_object_or_404(
        TechnicianCoFollowRequest.objects.select_related('source_technician', 'target_technician').prefetch_related(
            'items__producer_profile__user'
        ),
        id=request_id,
    )

    if not request.user.is_superuser and request.user.id != request_obj.target_technician_id:
        messages.error(request, 'Acces reserve au technicien cible.')
        return redirect('dashboard')

    items = list(request_obj.items.all())
    if request.method == 'POST' and request_obj.status == TechnicianCoFollowRequest.STATUS_PENDING:
        selected_profile_ids = {
            int(raw_id)
            for raw_id in request.POST.getlist('accepted_producers')
            if str(raw_id).isdigit()
        }
        accepted_count = 0
        rejected_count = 0
        for item in items:
            if item.producer_profile_id in selected_profile_ids:
                item.decision = TechnicianCoFollowRequestItem.DECISION_ACCEPTED
                ProducerTechnicianAssignment.objects.get_or_create(
                    producer_profile=item.producer_profile,
                    technician=request_obj.target_technician,
                    is_active=True,
                    defaults={'created_by': request.user},
                )
                accepted_count += 1
            else:
                item.decision = TechnicianCoFollowRequestItem.DECISION_REJECTED
                rejected_count += 1
            item.decided_at = timezone.now()
            item.save(update_fields=['decision', 'decided_at'])

        if accepted_count and not rejected_count:
            request_obj.status = TechnicianCoFollowRequest.STATUS_ACCEPTED
        elif rejected_count and not accepted_count:
            request_obj.status = TechnicianCoFollowRequest.STATUS_REJECTED
        else:
            request_obj.status = TechnicianCoFollowRequest.STATUS_PARTIAL
        request_obj.responded_at = timezone.now()
        request_obj.save(update_fields=['status', 'responded_at'])
        messages.success(request, 'Demande traitee.')
        return redirect('technician_records')

    return render(
        request,
        'scouting/technician_cofollow_review.html',
        {
            'request_obj': request_obj,
            'items': items,
        },
    )


@login_required
def superadmin_technician_management_view(request):
    if not request.user.is_superuser:
        messages.error(request, 'Acces reserve au super-admin.')
        return redirect('dashboard')

    technician_profiles = list(
        UserProfile.objects.select_related('user', 'structure')
        .filter(role=UserProfile.ROLE_TECHNICIAN, user__is_superuser=False)
        .annotate(
            active_producer_count=Count(
                'user__producer_assignments__producer_profile',
                filter=Q(user__producer_assignments__is_active=True),
                distinct=True,
            )
        )
        .order_by('user__last_name', 'user__first_name', 'user__username')
    )

    rows = [
        {
            'technician_id': profile.user_id,
            'last_name': profile.user.last_name or '-',
            'first_name': profile.user.first_name or '-',
            'username': profile.user.username,
            'structure_name': profile.structure.name if profile.structure else '-',
            'is_active': profile.license_status == UserProfile.LICENSE_STATUS_ACTIVE,
            'active_producer_count': getattr(profile, 'active_producer_count', 0),
        }
        for profile in technician_profiles
    ]

    return render(
        request,
        'scouting/superadmin_technician_management.html',
        {
            'rows': rows,
            'technician_count': len(rows),
        },
    )


@login_required
def technician_control_start_view(request, technician_id):
    if request.method != 'POST':
        return redirect('technician_records')
    if not request.user.is_superuser:
        messages.error(request, 'Acces reserve au super-admin.')
        return redirect('dashboard')

    technician_profile = get_object_or_404(_accessible_technician_profiles(request.user), user_id=technician_id)
    request.session[ACTING_TECHNICIAN_SESSION_KEY] = technician_profile.user_id
    request.session.pop(ACTING_PRODUCER_SESSION_KEY, None)
    request.session.modified = True
    if hasattr(request, '_acting_technician_profile_cache'):
        delattr(request, '_acting_technician_profile_cache')
    if hasattr(request, '_acting_producer_profile_cache'):
        delattr(request, '_acting_producer_profile_cache')
    messages.success(request, f'Interface technicien active pour {display_user_name(technician_profile.user)}.')
    return redirect('technician_records')


@login_required
def technician_control_stop_view(request):
    if request.method != 'POST':
        return redirect('technician_records')
    if not request.user.is_superuser:
        messages.error(request, 'Acces reserve au super-admin.')
        return redirect('dashboard')

    request.session.pop(ACTING_TECHNICIAN_SESSION_KEY, None)
    request.session.pop(ACTING_PRODUCER_SESSION_KEY, None)
    request.session.modified = True
    if hasattr(request, '_acting_technician_profile_cache'):
        delattr(request, '_acting_technician_profile_cache')
    if hasattr(request, '_acting_producer_profile_cache'):
        delattr(request, '_acting_producer_profile_cache')
    messages.success(request, 'Retour au compte super-admin.')
    return redirect('technician_records')


@login_required
def technician_deactivate_view(request, technician_id):
    if not request.user.is_superuser:
        messages.error(request, 'Acces reserve au super-admin.')
        return redirect('dashboard')

    technician_profile = get_object_or_404(
        UserProfile.objects.select_related('user'),
        user_id=technician_id,
        role=UserProfile.ROLE_TECHNICIAN,
    )
    active_assignments = list(
        ProducerTechnicianAssignment.objects.filter(
            technician=technician_profile.user,
            is_active=True,
        ).select_related('producer_profile__user')
    )

    initial = {'deactivation_message': technician_profile.deactivation_message}
    if request.method == 'POST':
        form = TechnicianDeactivationForm(request.POST, technician=technician_profile.user)
        if form.is_valid():
            mode = form.cleaned_data['reassign_mode']
            target_technician = form.cleaned_data.get('target_technician')
            message_for_producer = (form.cleaned_data.get('deactivation_message') or '').strip()

            selected_users = form.cleaned_data.get('producers')
            selected_profile_ids = set(
                UserProfile.objects.filter(user__in=selected_users).values_list('id', flat=True)
            )
            if mode == TechnicianDeactivationForm.REASSIGN_MODE_ALL:
                selected_profile_ids = {assignment.producer_profile_id for assignment in active_assignments}
            elif mode == TechnicianDeactivationForm.REASSIGN_MODE_NONE:
                selected_profile_ids = set()

            technician_profile.license_status = UserProfile.LICENSE_STATUS_INACTIVE
            technician_profile.deactivation_message = message_for_producer
            technician_profile.save(update_fields=['license_status', 'deactivation_message'])

            touched_profile_ids = set()
            reassigned_count = 0
            disabled_count = 0
            for assignment in active_assignments:
                touched_profile_ids.add(assignment.producer_profile_id)
                should_reassign = (
                    target_technician is not None and assignment.producer_profile_id in selected_profile_ids
                )
                assignment.close(
                    ended_by=request.user,
                    reason=(
                        ProducerTechnicianAssignment.END_REASON_REASSIGNED
                        if should_reassign
                        else ProducerTechnicianAssignment.END_REASON_TECHNICIAN_DISABLED
                    ),
                    message=message_for_producer,
                )
                disabled_count += 1
                if should_reassign:
                    ProducerTechnicianAssignment.objects.get_or_create(
                        producer_profile=assignment.producer_profile,
                        technician=target_technician,
                        is_active=True,
                        defaults={'created_by': request.user},
                    )
                    reassigned_count += 1

            for producer_profile in UserProfile.objects.filter(id__in=touched_profile_ids):
                active_technicians = [
                    row.technician
                    for row in producer_profile.technician_assignments.filter(is_active=True).select_related('technician')
                ]
                _sync_producer_technicians(producer_profile, active_technicians, changed_by=request.user)

            messages.success(
                request,
                (
                    f'Technicien desactive. Affectations fermees: {disabled_count}. '
                    f'Reaffectations creees: {reassigned_count}.'
                ),
            )
            return redirect('technician_records')
    else:
        form = TechnicianDeactivationForm(technician=technician_profile.user, initial=initial)

    return render(
        request,
        'scouting/technician_deactivate.html',
        {
            'form': form,
            'technician_profile': technician_profile,
            'active_assignments': active_assignments,
        },
    )


@login_required
def export_records_view(request):
    if Workbook is None:
        return HttpResponse('openpyxl manquant: installer openpyxl pour l export Excel.', status=500)

    effective_user = _effective_user(request)
    manager_user = _manager_user(request)
    scope = request.GET.get('scope', 'me')
    if scope == 'all' and _is_technician(manager_user):
        qs = ScoutingRecord.objects.select_related(
            'user',
            'primary_aphid_species',
            'plant_series',
            'crop_ref',
            'conduct_type_ref',
            'variety_ref',
        ).prefetch_related('leaf_observations')
        if not manager_user.is_superuser:
            qs = qs.filter(_technician_visibility_q(manager_user)).distinct()
    else:
        qs = (
            ScoutingRecord.objects.filter(user=effective_user)
            .select_related('user', 'primary_aphid_species', 'plant_series', 'crop_ref', 'conduct_type_ref', 'variety_ref')
            .prefetch_related('leaf_observations')
        )

    qs = _filter_records(request, qs)
    taxa = list(AuxiliaryTaxon.objects.order_by('display_order', 'name'))
    wb = Workbook()
    ws = wb.active
    ws.title = 'Comptages'
    header = [
        'Utilisateur',
        'Departement',
        'Serie',
        'Culture',
        'Conduite',
        'Variete',
        'Date saisie',
        'Annee',
        'Semaine',
        'Puceron principal',
        '% feuilles infestees',
        'Auxiliaires total',
        'Auxiliaires/plant',
        'Niveau risque',
        'Commentaire',
    ]
    header.extend([f'{taxon.name} (moy/plant)' for taxon in taxa])
    ws.append(header)
    for rec in qs:
        means = rec.species_means_per_plant()
        row = [
            display_user_name(rec.user),
            rec.department,
            rec.plant_series.name if rec.plant_series else '',
            rec.crop_ref.name if rec.crop_ref_id and rec.crop_ref else (rec.plant_series.crop.name if rec.plant_series_id and rec.plant_series else rec.crop),
            rec.conduct_type_ref.name if rec.conduct_type_ref else '',
            rec.variety_ref.name if rec.variety_ref else '',
            rec.scouting_date.isoformat(),
            rec.year,
            rec.week,
            str(rec.primary_aphid_species or ''),
            float(rec.aphid_infested_percent),
            rec.auxiliary_total,
            rec.auxiliaries_per_plant,
            rec.risk_level,
            rec.comment,
        ]
        row.extend([means.get(taxon.id, 0) for taxon in taxa])
        ws.append(row)

    content = BytesIO()
    wb.save(content)
    content.seek(0)
    response = HttpResponse(
        content.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="comptages_pucerons.xlsx"'
    return response


@login_required
def export_actions_view(request):
    if Workbook is None:
        return HttpResponse('openpyxl manquant: installer openpyxl pour l export Excel.', status=500)

    effective_user = _effective_user(request)
    manager_user = _manager_user(request)
    scope = request.GET.get('scope', 'me')

    if scope == 'all' and _is_technician(manager_user):
        qs = PlantAction.objects.select_related(
            'user',
            'user__profile',
            'action_type',
            'plant_series',
            'plant_series__crop',
            'crop_ref',
            'molecule',
            'auxiliary_taxon',
        )
        if not manager_user.is_superuser:
            qs = qs.filter(_technician_visibility_q(manager_user)).distinct()
    else:
        qs = PlantAction.objects.filter(user=effective_user).select_related(
            'user',
            'user__profile',
            'action_type',
            'plant_series',
            'plant_series__crop',
            'crop_ref',
            'molecule',
            'auxiliary_taxon',
        )

    department = request.GET.get('department')
    technician = request.GET.get('technician')
    producer = request.GET.get('producer')
    crop = request.GET.get('crop')
    year = request.GET.get('year')
    series = request.GET.get('series')

    if department:
        qs = qs.filter(department=department)
    if technician:
        qs = qs.filter(
            user__profile__technician_assignments__technician_id=technician,
            user__profile__technician_assignments__is_active=True,
        ).distinct()
    if producer:
        qs = qs.filter(user_id=producer)
    if crop:
        qs = qs.filter(Q(crop_ref_id=crop) | Q(plant_series__crop_id=crop))
    if year:
        qs = qs.filter(plant_series__year=year)
    if series:
        qs = qs.filter(plant_series_id=series)

    wb = Workbook()
    ws = wb.active
    ws.title = 'Actions'
    ws.append(
        [
            'Utilisateur',
            'Département',
            'Série',
            'Culture',
            'Année',
            'Date action',
            'Type',
            'Portée',
            'Molécule',
            'Auxiliaire',
            'Détails',
        ]
    )

    for action in qs.order_by('-action_date', '-created_at'):
        crop_name = (
            action.crop_ref.name
            if action.crop_ref_id and action.crop_ref
            else (action.plant_series.crop.name if action.plant_series_id and action.plant_series and action.plant_series.crop_id else '')
        )
        ws.append(
            [
                display_user_name(action.user),
                action.department,
                action.plant_series.name if action.plant_series else '',
                crop_name,
                action.plant_series.year if action.plant_series else '',
                action.action_date.isoformat(),
                action.action_type.name if action.action_type else '',
                action.get_scope_display(),
                action.molecule.name if action.molecule else '',
                action.auxiliary_taxon.name if action.auxiliary_taxon else '',
                action.notes,
            ]
        )

    content = BytesIO()
    wb.save(content)
    content.seek(0)
    response = HttpResponse(
        content.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="actions_pucerons.xlsx"'
    return response
