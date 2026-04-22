import csv
import json
import secrets
import string
import time
import unicodedata
from io import StringIO
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.contrib.auth import get_user_model
from django.db import transaction

from .models import UserProfile
from .utils import display_user_name
from .view_access import _get_profile, _sync_producer_technicians

User = get_user_model()

GEOCODE_ENDPOINT = 'https://nominatim.openstreetmap.org/search'
GEOCODE_USER_AGENT = 'PUCERON/1.0 (contact: no-reply@puceron.agrobio-bretagne.org)'
GEOCODE_THROTTLE_SECONDS = 1.0

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

MOJIBAKE_MARKERS = ('Ãƒ', 'Ã‚', 'Ã¢', 'â‚¬', 'â„¢', 'Å“', 'ï¿½')


def _normalize_csv_header(value):
    normalized = unicodedata.normalize('NFKD', _clean_csv_text(str(value or '')))
    ascii_value = normalized.encode('ascii', 'ignore').decode('ascii')
    ascii_value = ' '.join(ascii_value.replace('_', ' ').replace('\t', ' ').split())
    return ascii_value.strip().lower()


def _mojibake_score(value):
    return sum(value.count(marker) for marker in MOJIBAKE_MARKERS)


def _repair_mojibake(value):
    text = str(value or '')
    if not text:
        return text

    candidates = [text]
    for source_encoding in ('latin-1', 'cp1252'):
        try:
            repaired = text.encode(source_encoding).decode('utf-8')
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        candidates.append(repaired)

    best = min(candidates, key=_mojibake_score)
    if _mojibake_score(best) < _mojibake_score(text):
        return best
    return text


def _clean_csv_text(value):
    text = _repair_mojibake(str(value or ''))
    text = text.replace('\ufeff', '').strip()
    return unicodedata.normalize('NFC', text)


def _decode_csv_upload(uploaded_file):
    raw = uploaded_file.read()
    for encoding in ('utf-8-sig', 'utf-8', 'utf-16', 'utf-16-le', 'utf-16-be', 'windows-1252', 'cp1252', 'latin-1'):
        try:
            return _clean_csv_text(raw.decode(encoding))
        except UnicodeDecodeError:
            continue
    return _clean_csv_text(raw.decode('utf-8', errors='ignore'))


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
        raise ValueError('Colonnes manquantes dans le CSV: ' + ', '.join(missing) + '.')

    rows = []
    for index, row in enumerate(reader, start=2):
        mapped_row = {'_line': index}
        for original, mapped in normalized_headers.items():
            mapped_row[mapped] = _clean_csv_text(row.get(original) or '')
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

    technician_qs = User.objects.filter(
        profile__role=UserProfile.ROLE_TECHNICIAN,
        profile__license_status=UserProfile.LICENSE_STATUS_ACTIVE,
    )
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


def _geocode_address(street_address, postal_code, city, cache=None, rate_limiter=None):
    street = (street_address or '').strip()
    postal = (postal_code or '').strip()
    locality = (city or '').strip()
    if not street or not postal or not locality:
        return {
            'status': 'missing_input',
            'message': 'Adresse incomplete: GPS non calcule.',
            'latitude': None,
            'longitude': None,
        }

    query = f'{street}, {postal} {locality}, France'
    cache_key = query.lower()
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    if rate_limiter is not None:
        last_request_at = rate_limiter.get('last_request_at')
        if last_request_at is not None:
            elapsed = time.monotonic() - last_request_at
            if elapsed < GEOCODE_THROTTLE_SECONDS:
                time.sleep(GEOCODE_THROTTLE_SECONDS - elapsed)

    params = urlencode(
        {
            'format': 'jsonv2',
            'limit': 1,
            'countrycodes': 'fr',
            'addressdetails': 0,
            'q': query,
        }
    )
    request = Request(
        f'{GEOCODE_ENDPOINT}?{params}',
        headers={
            'User-Agent': GEOCODE_USER_AGENT,
            'Accept': 'application/json',
        },
    )

    try:
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode('utf-8'))
        if rate_limiter is not None:
            rate_limiter['last_request_at'] = time.monotonic()
    except (URLError, TimeoutError, json.JSONDecodeError, ValueError):
        result = {
            'status': 'error',
            'message': 'Geocodage indisponible: GPS non calcule.',
            'latitude': None,
            'longitude': None,
        }
    else:
        if payload:
            first = payload[0]
            result = {
                'status': 'matched',
                'message': 'GPS calcule a partir de l adresse.',
                'latitude': first.get('lat'),
                'longitude': first.get('lon'),
            }
        else:
            result = {
                'status': 'not_found',
                'message': 'Adresse sans correspondance: GPS non calcule.',
                'latitude': None,
                'longitude': None,
            }

    if cache is not None:
        cache[cache_key] = result
    return result


def _upsert_producer_from_csv_row(row, importer, update_existing, geocode_cache=None, geocode_state=None):
    technician = _resolve_import_technician(importer, row.get('technician_ref', ''))
    technician_profile = _get_profile(technician)
    if technician_profile.role != UserProfile.ROLE_TECHNICIAN:
        raise ValueError(f'{display_user_name(technician)} n est pas technicien.')
    requested_department = (row.get('department') or '').strip()

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
        if requested_department:
            profile.department = requested_department
        elif not profile.department and technician_profile.department:
            profile.department = technician_profile.department
        profile.farm_name = row.get('farm_name', '')
        profile.phone = row.get('phone', '')
        profile.street_address = row.get('street_address', '')
        profile.postal_code = row.get('postal_code', '')
        profile.city = row.get('city', '')
        profile.save()
        _sync_producer_technicians(profile, [technician], changed_by=importer)

    notes = []
    if requested_department and technician_profile.department and requested_department != technician_profile.department:
        notes.append(
            f'Departement CSV {requested_department} conserve (filtre independant du rattachement technicien).'
        )

    geocode_result = _geocode_address(
        profile.street_address,
        profile.postal_code,
        profile.city,
        cache=geocode_cache,
        rate_limiter=geocode_state,
    )
    if geocode_result['status'] == 'matched':
        profile.latitude = geocode_result['latitude']
        profile.longitude = geocode_result['longitude']
        profile.save(update_fields=['latitude', 'longitude'])
    notes.append(geocode_result['message'])

    return {
        'status': action,
        'created': created,
        'user': user,
        'profile': profile,
        'technician': technician,
        'geocode_status': geocode_result['status'],
        'note': ' '.join(note for note in notes if note),
    }

