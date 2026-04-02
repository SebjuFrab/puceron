import csv
import secrets
import string
import unicodedata
from io import StringIO

from django.contrib.auth import get_user_model
from django.db import transaction

from .models import UserProfile
from .utils import display_user_name
from .view_access import _get_profile

User = get_user_model()

CSV_IMPORT_COLUMN_ALIASES = {
    'raison social': 'farm_name',
    'raison sociale': 'farm_name',
    'nom': 'last_name',
    'prenom': 'first_name',
    'departement': 'department',
    'mail': 'email',
    'adresse': 'street_address',
    'code postal': 'postal_code',
    'commune': 'city',
    'idtek referents': 'technician_ref',
    'idtek referent': 'technician_ref',
    'idtek reference': 'technician_ref',
    'mobile': 'phone',
}

CSV_IMPORT_REQUIRED_FIELDS = (
    'farm_name',
    'last_name',
    'first_name',
    'email',
    'street_address',
    'postal_code',
    'city',
)


def _normalize_csv_header(value):
    normalized = unicodedata.normalize('NFKD', str(value or ''))
    ascii_value = normalized.encode('ascii', 'ignore').decode('ascii')
    ascii_value = ' '.join(ascii_value.replace('_', ' ').replace('\t', ' ').split())
    return ascii_value.strip().lower()


def _decode_csv_upload(uploaded_file):
    raw = uploaded_file.read()
    for encoding in ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='ignore')


def _load_csv_rows(uploaded_file):
    content = _decode_csv_upload(uploaded_file)
    sample = content[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=';,|\t,')
    except csv.Error:
        class _FallbackDialect(csv.excel):
            delimiter = ';'
        dialect = _FallbackDialect

    reader = csv.DictReader(StringIO(content), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError('Le fichier CSV est vide ou sans entetes.')

    normalized_headers = {}
    for original in reader.fieldnames:
        normalized = _normalize_csv_header(original)
        mapped = CSV_IMPORT_COLUMN_ALIASES.get(normalized)
        if mapped:
            normalized_headers[original] = mapped

    missing = [field for field in CSV_IMPORT_REQUIRED_FIELDS if field not in normalized_headers.values()]
    if missing:
        raise ValueError(
            'Colonnes manquantes dans le CSV: ' + ', '.join(missing) + '.'
        )

    rows = []
    for index, row in enumerate(reader, start=2):
        mapped_row = {'_line': index}
        for original, mapped in normalized_headers.items():
            mapped_row[mapped] = (row.get(original) or '').strip()
        if not any(value for key, value in mapped_row.items() if key != '_line'):
            continue
        rows.append(mapped_row)
    return rows


def _random_initial_password():
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(20))


def _generate_unique_username(first_name, last_name, farm_name, email):
    if email:
        base = (email.split('@', 1)[0] or '').strip()
    else:
        base = ''
    if not base:
        base = '.'.join(part for part in [first_name, last_name] if part).strip('.')
    if not base:
        base = farm_name or 'producteur'
    slug = unicodedata.normalize('NFKD', base).encode('ascii', 'ignore').decode('ascii').lower()
    slug = ''.join(ch if ch.isalnum() else '.' for ch in slug)
    slug = '.'.join(filter(None, slug.split('.'))).strip('.')
    slug = slug[:120] or 'producteur'
    candidate = slug
    suffix = 2
    while User.objects.filter(username__iexact=candidate).exists():
        candidate = f'{slug[:140]}-{suffix}'
        suffix += 1
    return candidate[:150]


def _resolve_import_technician(importer, technician_ref):
    if not importer.is_superuser:
        technician_profile = _get_profile(importer)
        if technician_profile.role != UserProfile.ROLE_TECHNICIAN:
            raise ValueError('Seuls les techniciens ou super-admin peuvent importer des producteurs.')
        return importer

    reference = (technician_ref or '').strip()
    if not reference:
        raise ValueError('Colonne "IDtek referents" obligatoire pour un import super-admin.')

    technician_qs = User.objects.filter(profile__role=UserProfile.ROLE_TECHNICIAN)
    technician = None
    if reference.isdigit():
        technician = technician_qs.filter(id=int(reference)).first()
    if technician is None:
        technician = technician_qs.filter(username__iexact=reference).first()
    if technician is None:
        technician = technician_qs.filter(email__iexact=reference).first()
    if technician is None:
        raise ValueError(f'Technicien introuvable pour la reference "{reference}".')
    return technician


def _upsert_producer_from_csv_row(row, importer, update_existing):
    technician = _resolve_import_technician(importer, row.get('technician_ref', ''))
    technician_profile = _get_profile(technician)
    if technician_profile.role != UserProfile.ROLE_TECHNICIAN:
        raise ValueError(f'{display_user_name(technician)} n est pas technicien.')
    if not technician_profile.department:
        raise ValueError(f'{display_user_name(technician)} n a pas de departement renseigne.')

    email = (row.get('email') or '').strip().lower()
    if not email:
        raise ValueError('Email obligatoire pour identifier ou creer le producteur.')

    existing_email_user = User.objects.filter(email__iexact=email).first()
    if existing_email_user and not update_existing:
        raise ValueError(f'Un utilisateur existe deja avec l email {email}.')
    existing_user = existing_email_user if update_existing else None
    created = False

    with transaction.atomic():
        if existing_user:
            user = existing_user
            profile, _ = UserProfile.objects.get_or_create(user=user)
            if profile.role not in (UserProfile.ROLE_PRODUCER, ''):
                raise ValueError(f'L utilisateur {display_user_name(user)} existe deja avec un role non producteur.')
            action = 'updated'
        else:
            user = User(
                username=_generate_unique_username(
                    row.get('first_name', ''),
                    row.get('last_name', ''),
                    row.get('farm_name', ''),
                    email,
                ),
                email=email,
            )
            user.set_password(_random_initial_password())
            action = 'created'
            created = True

        user.first_name = row.get('first_name', '')
        user.last_name = row.get('last_name', '')
        user.email = email
        user.save()

        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = UserProfile.ROLE_PRODUCER
        profile.assigned_technician = technician
        profile.department = technician_profile.department
        profile.farm_name = row.get('farm_name', '')
        profile.phone = row.get('phone', '')
        profile.street_address = row.get('street_address', '')
        profile.postal_code = row.get('postal_code', '')
        profile.city = row.get('city', '')
        profile.save()

    requested_department = (row.get('department') or '').strip()
    notes = []
    if requested_department and requested_department != technician_profile.department:
        notes.append(
            f'Departement CSV {requested_department} remplace par {technician_profile.department} (technicien referent).'
        )

    return {
        'status': action,
        'created': created,
        'user': user,
        'profile': profile,
        'technician': technician,
        'note': ' '.join(notes),
    }
