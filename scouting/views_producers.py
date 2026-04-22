import csv
from io import StringIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import (
    ProducerAccountCreationForm,
    ProducerImportForm,
    ProducerProfileUpdateForm,
)
from .utils import display_user_name
from .views_support import (
    CSV_IMPORT_REQUIRED_FIELDS,
    _accessible_producer_profiles,
    _can_manage_producers,
    _effective_access_restriction,
    _is_acting_as_producer,
    _manager_user,
    _load_csv_rows,
    _profile_address_context,
    _upsert_producer_from_csv_row,
)

@login_required
def producer_create_view(request):
    if _is_acting_as_producer(request):
        messages.error(request, 'Quittez le mode producteur avant de gerer des comptes producteurs.')
        return redirect('dashboard')

    manager_user = _manager_user(request)
    if not _can_manage_producers(manager_user):
        messages.error(request, 'Acces reserve aux techniciens et au super-admin.')
        return redirect('dashboard')
    restriction = _effective_access_restriction(request, for_write=True)
    if restriction:
        messages.error(request, restriction['message'])
        return redirect('dashboard')

    if request.method == 'POST':
        form = ProducerAccountCreationForm(request.POST, creator=manager_user)
        if form.is_valid():
            created_user = form.save()
            technician_count = len(form.cleaned_data.get('technicians') or [])
            messages.success(
                request,
                (
                    f'Compte producteur cree: {display_user_name(created_user)} '
                    f'(identifiant: {created_user.username}, {technician_count} technicien(s) rattache(s)).'
                ),
            )
            return redirect('producer_create')
    else:
        form = ProducerAccountCreationForm(creator=manager_user)

    return render(
        request,
        'scouting/producer_create.html',
        {
            'form': form,
            'is_super_admin_creator': manager_user.is_superuser,
        },
    )


@login_required
def producer_import_view(request):
    if _is_acting_as_producer(request):
        messages.error(request, 'Quittez le mode producteur avant d importer des producteurs.')
        return redirect('dashboard')

    manager_user = _manager_user(request)
    if not _can_manage_producers(manager_user):
        messages.error(request, 'Acces reserve aux techniciens et au super-admin.')
        return redirect('dashboard')
    restriction = _effective_access_restriction(request, for_write=True)
    if restriction:
        messages.error(request, restriction['message'])
        return redirect('dashboard')

    results = []
    summary = None
    if request.method == 'POST':
        form = ProducerImportForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                rows = _load_csv_rows(form.cleaned_data['csv_file'])
            except ValueError as exc:
                form.add_error('csv_file', str(exc))
            else:
                update_existing = form.cleaned_data['update_existing']
                created_count = 0
                updated_count = 0
                error_count = 0
                gps_found_count = 0
                gps_missing_count = 0
                gps_error_count = 0
                geocode_cache = {}
                geocode_state = {'last_request_at': None}

                for row in rows:
                    missing = [
                        field
                        for field in CSV_IMPORT_REQUIRED_FIELDS
                        if not (row.get(field) or '').strip()
                    ]
                    if missing:
                        error_count += 1
                        results.append(
                            {
                                'line': row['_line'],
                                'status': 'error',
                                'message': 'Champs obligatoires manquants: ' + ', '.join(missing),
                            }
                        )
                        continue

                    try:
                        result = _upsert_producer_from_csv_row(
                            row,
                            manager_user,
                            update_existing,
                            geocode_cache=geocode_cache,
                            geocode_state=geocode_state,
                        )
                    except ValueError as exc:
                        error_count += 1
                        results.append(
                            {
                                'line': row['_line'],
                                'status': 'error',
                                'message': str(exc),
                            }
                        )
                        continue

                    if result['created']:
                        created_count += 1
                    else:
                        updated_count += 1
                    if result['geocode_status'] == 'matched':
                        gps_found_count += 1
                    elif result['geocode_status'] == 'not_found':
                        gps_missing_count += 1
                    elif result['geocode_status'] in {'error', 'missing_input'}:
                        gps_error_count += 1
                    results.append(
                        {
                            'line': row['_line'],
                            'status': result['status'],
                            'producer_name': display_user_name(result['user']),
                            'username': result['user'].username,
                            'email': result['user'].email,
                            'technician_name': display_user_name(result['technician']),
                            'first_login': 'Faire "Mot de passe oublie"' if result['created'] else '-',
                            'gps_status': result['geocode_status'],
                            'message': result['note'] or '',
                        }
                    )

                summary = {
                    'total': len(rows),
                    'created': created_count,
                    'updated': updated_count,
                    'errors': error_count,
                    'gps_found': gps_found_count,
                    'gps_missing': gps_missing_count,
                    'gps_errors': gps_error_count,
                }
                if error_count:
                    messages.warning(
                        request,
                        f'Import termine: {created_count} crees, {updated_count} mis a jour, {error_count} en erreur.',
                    )
                else:
                    messages.success(
                        request,
                        f'Import termine: {created_count} crees, {updated_count} mis a jour.',
                    )
    else:
        form = ProducerImportForm()

    expected_columns = [
        'Raison social',
        'Nom',
        'Prenom',
        'Departement',
        'mail',
        'Adresse',
        'code postal',
        'commune',
        'IDtek referents',
        'mobile',
    ]
    return render(
        request,
        'scouting/producer_import.html',
        {
            'form': form,
            'results': results,
            'summary': summary,
            'expected_columns': expected_columns,
            'is_super_admin_creator': manager_user.is_superuser,
            'current_technician_name': display_user_name(manager_user) if not manager_user.is_superuser else '',
        },
    )


@login_required
def producer_import_template_view(request):
    if not _can_manage_producers(_manager_user(request)):
        messages.error(request, 'Acces reserve aux techniciens et au super-admin.')
        return redirect('dashboard')

    output = StringIO()
    writer = csv.writer(output, delimiter=';')
    header = [
        'Raison social',
        'Nom',
        'Prenom',
        'Departement',
        'mail',
        'Adresse',
        'code postal',
        'commune',
        'IDtek referents',
        'mobile',
    ]
    writer.writerow(header)
    writer.writerow(
        [
            'GAEC Exemple',
            'Martin',
            'Claire',
            '56',
            'claire.martin@example.org',
            '12 route des serres',
            '56000',
            'Vannes',
            'tek56',
            '0612345678',
        ]
    )

    csv_bytes = output.getvalue().encode('cp1252', errors='replace')
    response = HttpResponse(csv_bytes, content_type='text/csv; charset=windows-1252')
    response['Content-Disposition'] = 'attachment; filename="template_import_producteurs.csv"'
    return response


@login_required
def producer_update_view(request, producer_id):
    if _is_acting_as_producer(request):
        messages.error(request, 'Quittez le mode producteur avant de modifier un producteur.')
        return redirect('dashboard')

    manager_user = _manager_user(request)
    if not _can_manage_producers(manager_user):
        messages.error(request, 'Acces reserve aux techniciens et au super-admin.')
        return redirect('dashboard')
    restriction = _effective_access_restriction(request, for_write=True)
    if restriction:
        messages.error(request, restriction['message'])
        return redirect('dashboard')

    producer_profile_qs = _accessible_producer_profiles(manager_user).select_related('user')
    producer_profile = get_object_or_404(producer_profile_qs, user_id=producer_id)
    producer_user = producer_profile.user

    if request.method == 'POST':
        form = ProducerProfileUpdateForm(
            request.POST,
            instance=producer_profile,
            editor=manager_user,
            producer_user=producer_user,
        )
        if form.is_valid():
            form.save()
            messages.success(request, 'Profil producteur mis a jour.')
            next_view = request.POST.get('next') or request.GET.get('next')
            if next_view == 'technician_records':
                return redirect(f"{reverse('technician_records')}?producer={producer_user.id}")
            return redirect('producer_update', producer_id=producer_user.id)
    else:
        form = ProducerProfileUpdateForm(
            instance=producer_profile,
            editor=manager_user,
            producer_user=producer_user,
        )

    context = {
        'form': form,
        'producer_profile': producer_profile,
        'producer_user': producer_user,
        'is_super_admin_editor': manager_user.is_superuser,
    }
    context.update(_profile_address_context(producer_profile))
    return render(request, 'scouting/producer_update.html', context)


