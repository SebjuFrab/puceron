import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import connection, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags

from .models import NotificationDelivery, NotificationPreference

logger = logging.getLogger(__name__)


def queue_bulletin_email_notifications(request, recipient_ids):
    existing_recipient_ids = set(
        NotificationDelivery.objects.filter(
            recipient_id__in=recipient_ids,
            channel=NotificationDelivery.CHANNEL_EMAIL,
        ).values_list('recipient_id', flat=True)
    )
    deliveries = [
        NotificationDelivery(
            recipient_id=recipient_id,
            channel=NotificationDelivery.CHANNEL_EMAIL,
            status=NotificationDelivery.STATUS_PENDING,
            target_url=request.build_absolute_uri(
                reverse('my_bulletin_detail', args=[recipient_id])
            ),
        )
        for recipient_id in recipient_ids
        if recipient_id not in existing_recipient_ids
    ]
    if deliveries:
        NotificationDelivery.objects.bulk_create(deliveries)
    return len(deliveries)


def _claim_next_email_delivery():
    now = timezone.now()
    processing_timeout = max(
        getattr(settings, 'NOTIFICATION_PROCESSING_TIMEOUT_SECONDS', 900),
        60,
    )
    stale_before = now - timedelta(seconds=processing_timeout)
    eligible = (
        Q(status=NotificationDelivery.STATUS_PENDING)
        & (Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
    ) | Q(
        status=NotificationDelivery.STATUS_PROCESSING,
        updated_at__lte=stale_before,
    )

    with transaction.atomic():
        queryset = NotificationDelivery.objects.filter(
            channel=NotificationDelivery.CHANNEL_EMAIL,
        ).filter(eligible)
        if connection.features.has_select_for_update:
            queryset = queryset.select_for_update(
                skip_locked=connection.features.has_select_for_update_skip_locked
            )
        delivery = queryset.order_by('created_at', 'id').first()
        if delivery is None:
            return None

        delivery.status = NotificationDelivery.STATUS_PROCESSING
        delivery.attempt_count += 1
        delivery.last_attempt_at = now
        delivery.next_attempt_at = None
        delivery.error = ''
        delivery.save(
            update_fields=[
                'status',
                'attempt_count',
                'last_attempt_at',
                'next_attempt_at',
                'error',
                'updated_at',
            ]
        )
        return delivery.id


def _mark_skipped(delivery, reason):
    delivery.status = NotificationDelivery.STATUS_SKIPPED
    delivery.error = reason
    delivery.next_attempt_at = None
    delivery.save(update_fields=['status', 'error', 'next_attempt_at', 'updated_at'])


def _mark_failed_or_retry(delivery, error):
    max_attempts = max(getattr(settings, 'NOTIFICATION_MAX_ATTEMPTS', 3), 1)
    retry_delay = max(getattr(settings, 'NOTIFICATION_RETRY_DELAY_SECONDS', 300), 1)
    delivery.error = str(error)
    if delivery.attempt_count >= max_attempts:
        delivery.status = NotificationDelivery.STATUS_FAILED
        delivery.next_attempt_at = None
    else:
        delivery.status = NotificationDelivery.STATUS_PENDING
        delivery.next_attempt_at = timezone.now() + timedelta(
            seconds=retry_delay * (2 ** (delivery.attempt_count - 1))
        )
    delivery.save(update_fields=['status', 'error', 'next_attempt_at', 'updated_at'])


def process_email_delivery(delivery_id):
    delivery = (
        NotificationDelivery.objects.select_related(
            'recipient',
            'recipient__bulletin',
            'recipient__bulletin__priority',
            'recipient__producer_profile',
            'recipient__producer_profile__user',
        )
        .prefetch_related('recipient__bulletin__types')
        .get(id=delivery_id)
    )
    if delivery.status != NotificationDelivery.STATUS_PROCESSING:
        return False

    recipient = delivery.recipient
    bulletin = recipient.bulletin
    producer_user = recipient.producer_profile.user
    if not producer_user.email:
        _mark_skipped(delivery, 'Email producteur non renseigné.')
        return True

    preference = NotificationPreference.objects.get_or_create(user=producer_user)[0]
    if not preference.wants_bulletin_email(bulletin):
        _mark_skipped(delivery, 'Désactivé par le producteur.')
        return True

    detail_url = delivery.target_url
    if not detail_url:
        detail_url = (
            f"{settings.PUBLIC_BASE_URL}"
            f"{reverse('my_bulletin_detail', args=[recipient.id])}"
        )
    body_preview = strip_tags(bulletin.body or '').strip()
    if len(body_preview) > 700:
        body_preview = f'{body_preview[:700]}...'

    try:
        send_mail(
            subject=f'Nouveau bulletin PUCERON: {bulletin.title}',
            message=(
                'Un nouveau bulletin est disponible dans PUCERON.\n\n'
                f'Titre: {bulletin.title}\n'
                f'Types: {bulletin.type_labels or "-"}\n'
                f'Priorité: {bulletin.priority_label or "-"}\n\n'
                f'{body_preview}\n\n'
                f'Ouvrir le bulletin: {detail_url}'
            ),
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
            recipient_list=[producer_user.email],
            fail_silently=False,
        )
    except Exception as exc:
        logger.exception('Échec de livraison email pour la notification %s', delivery.id)
        _mark_failed_or_retry(delivery, exc)
        return True

    delivery.status = NotificationDelivery.STATUS_SENT
    delivery.sent_at = timezone.now()
    delivery.error = ''
    delivery.next_attempt_at = None
    delivery.save(
        update_fields=['status', 'sent_at', 'error', 'next_attempt_at', 'updated_at']
    )
    return True


def process_pending_email_deliveries(limit=50):
    processed = 0
    for _ in range(max(limit, 1)):
        delivery_id = _claim_next_email_delivery()
        if delivery_id is None:
            break
        process_email_delivery(delivery_id)
        processed += 1
    return processed
