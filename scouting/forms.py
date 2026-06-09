from django import forms
import html
import os
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.db.models import Q
from django.utils.html import strip_tags

from .models import (
    ActionType,
    AphidSpecies,
    AuxiliaryTaxon,
    BulletinAttachment,
    BulletinMessage,
    BulletinMessageType,
    BulletinPriority,
    ConductType,
    Crop,
    Department,
    Molecule,
    NotificationPreference,
    OtherPestTaxon,
    PlantAction,
    ProducerTechnicianAssignment,
    PlantSeries,
    RecommendationDismissReason,
    ScoutingRecord,
    ServicePlant,
    TechnicianCoFollowRequest,
    TechnicianCoFollowRequestItem,
    TechnicianStructure,
    UserProfile,
    Variety,
)
from .utils import display_user_name
from .view_access import _sync_producer_technicians

User = get_user_model()

BULLETIN_MAX_PHOTOS = 8
BULLETIN_MAX_ATTACHMENTS = 8
BULLETIN_MAX_PHOTO_SIZE = 8 * 1024 * 1024
BULLETIN_MAX_ATTACHMENT_SIZE = 15 * 1024 * 1024
BULLETIN_PHOTO_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
BULLETIN_ATTACHMENT_EXTENSIONS = {
    '.csv',
    '.doc',
    '.docx',
    '.jpg',
    '.jpeg',
    '.pdf',
    '.png',
    '.ppt',
    '.pptx',
    '.txt',
    '.webp',
    '.xls',
    '.xlsx',
    '.zip',
}


_ALLOWED_RICH_TEXT_TAGS = {
    'a',
    'b',
    'blockquote',
    'br',
    'code',
    'div',
    'em',
    'h1',
    'h2',
    'h3',
    'i',
    'li',
    'ol',
    'p',
    'pre',
    's',
    'span',
    'strong',
    'sub',
    'sup',
    'u',
    'ul',
}
_DROP_RICH_TEXT_CONTENT_TAGS = {'script', 'style', 'iframe', 'object', 'embed'}
_ALLOWED_COLOR_RE = re.compile(
    r'^(#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?|rgb\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*\))$'
)


def _clean_rich_text_href(value):
    value = (value or '').strip()
    if not value:
        return ''
    parsed = urlparse(value)
    if parsed.scheme and parsed.scheme.lower() not in {'http', 'https', 'mailto', 'tel'}:
        return ''
    if not parsed.scheme and not value.startswith('/'):
        return ''
    return value


def _clean_rich_text_style(value):
    clean_rules = []
    for rule in (value or '').split(';'):
        if ':' not in rule:
            continue
        property_name, property_value = [part.strip().lower() for part in rule.split(':', 1)]
        if property_name not in {'color', 'background-color'}:
            continue
        if _ALLOWED_COLOR_RE.match(property_value):
            clean_rules.append(f'{property_name}: {property_value}')
    return '; '.join(clean_rules)


class _RichTextSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.drop_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _DROP_RICH_TEXT_CONTENT_TAGS:
            self.drop_depth += 1
            return
        if self.drop_depth or tag not in _ALLOWED_RICH_TEXT_TAGS:
            return
        clean_attrs = []
        attrs_dict = {name.lower(): value for name, value in attrs}
        if tag == 'a':
            href = _clean_rich_text_href(attrs_dict.get('href'))
            if href:
                clean_attrs.extend(
                    [
                        ('href', href),
                        ('target', '_blank'),
                        ('rel', 'noopener noreferrer'),
                    ]
                )
        elif tag == 'span':
            style = _clean_rich_text_style(attrs_dict.get('style'))
            if style:
                clean_attrs.append(('style', style))
        if tag == 'br':
            self.parts.append('<br>')
            return
        attrs_html = ''.join(
            f' {name}="{html.escape(value, quote=True)}"' for name, value in clean_attrs
        )
        self.parts.append(f'<{tag}{attrs_html}>')

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _DROP_RICH_TEXT_CONTENT_TAGS:
            self.drop_depth = max(self.drop_depth - 1, 0)
            return
        if self.drop_depth or tag not in _ALLOWED_RICH_TEXT_TAGS or tag == 'br':
            return
        self.parts.append(f'</{tag}>')

    def handle_data(self, data):
        if not self.drop_depth:
            self.parts.append(html.escape(data))

    def get_html(self):
        return ''.join(self.parts).strip()


def sanitize_bulletin_body(value):
    sanitizer = _RichTextSanitizer()
    sanitizer.feed(value or '')
    sanitizer.close()
    return sanitizer.get_html()


class TechnicianChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return display_user_name(obj)


class ProducerChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        profile = getattr(obj, 'profile', None)
        if profile and profile.farm_name:
            return f'{profile.farm_name} ({obj.username})'
        return display_user_name(obj)


class ProducerProfileChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        label = obj.farm_name or display_user_name(obj.user)
        details = []
        if obj.city:
            details.append(obj.city)
        if obj.department:
            details.append(obj.department)
        if details:
            return f'{label} ({", ".join(details)})'
        return label


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        if not data:
            return []
        if not isinstance(data, (list, tuple)):
            data = [data]
        return [super(MultipleFileField, self).clean(item, initial) for item in data]


def _uploaded_file_extension(uploaded_file):
    return os.path.splitext(uploaded_file.name or '')[1].lower()


def _validate_uploaded_files(uploaded_files, *, max_count, max_size, allowed_extensions, label):
    uploaded_files = uploaded_files or []
    if len(uploaded_files) > max_count:
        raise forms.ValidationError(f'{label}: maximum {max_count} fichier(s).')
    for uploaded_file in uploaded_files:
        extension = _uploaded_file_extension(uploaded_file)
        if extension not in allowed_extensions:
            allowed = ', '.join(sorted(allowed_extensions))
            raise forms.ValidationError(f'{uploaded_file.name}: format non autorise. Formats acceptes: {allowed}.')
        if uploaded_file.size > max_size:
            max_size_mb = max_size // (1024 * 1024)
            raise forms.ValidationError(f'{uploaded_file.name}: fichier trop lourd, maximum {max_size_mb} Mo.')
    return uploaded_files


class BulletinMessageForm(forms.ModelForm):
    producers = ProducerProfileChoiceField(
        queryset=UserProfile.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        label='Destinataires',
    )
    photos = MultipleFileField(
        required=False,
        label='Photos',
        widget=MultipleFileInput(
            attrs={
                'accept': 'image/jpeg,image/png,image/webp,image/gif',
                'multiple': True,
            }
        ),
    )
    attachments = MultipleFileField(
        required=False,
        label='Pièces jointes',
        widget=MultipleFileInput(
            attrs={
                'accept': '.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.zip,.jpg,.jpeg,.png,.webp',
                'multiple': True,
            }
        ),
    )

    class Meta:
        model = BulletinMessage
        fields = [
            'title',
            'types',
            'priority',
            'crops',
            'departments',
            'valid_until',
            'body',
        ]
        widgets = {
            'types': forms.CheckboxSelectMultiple,
            'crops': forms.CheckboxSelectMultiple,
            'departments': forms.CheckboxSelectMultiple,
            'valid_until': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'body': forms.Textarea(attrs={'rows': 7}),
        }
        labels = {
            'title': 'Titre',
            'types': 'Types',
            'priority': 'Priorité',
            'crops': 'Cultures',
            'departments': 'Départements',
            'valid_until': "Valable jusqu'au",
            'body': 'Message',
        }

    def __init__(self, *args, **kwargs):
        producer_queryset = kwargs.pop('producer_queryset', UserProfile.objects.none())
        super().__init__(*args, **kwargs)
        self.fields['producers'].queryset = producer_queryset
        self.fields['types'].queryset = BulletinMessageType.objects.filter(is_active=True).order_by(
            'display_order',
            'label',
        )
        self.fields['priority'].queryset = BulletinPriority.objects.filter(is_active=True).order_by(
            'display_order',
            'label',
        )
        self.fields['crops'].queryset = Crop.objects.filter(is_active=True).order_by('name')
        self.fields['departments'].queryset = Department.objects.filter(is_active=True).order_by('code')
        self.fields['types'].required = True
        self.fields['priority'].required = True
        self.fields['crops'].required = False
        self.fields['departments'].required = False
        self.fields['valid_until'].required = False
        self.fields['valid_until'].input_formats = ['%Y-%m-%d']
        self.fields['title'].widget.attrs.update(
            {
                'placeholder': 'Ex. Vigilance pucerons sous abris',
                'maxlength': 180,
            }
        )
        for field_name, field in self.fields.items():
            if field_name == 'producers':
                continue
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            elif isinstance(field.widget, forms.CheckboxSelectMultiple):
                field.widget.attrs['class'] = 'choice-widget'
            else:
                field.widget.attrs['class'] = 'form-control'
        if not self.is_bound:
            default_type = self.fields['types'].queryset.filter(code=BulletinMessageType.CODE_BSV).first()
            default_priority = self.fields['priority'].queryset.filter(code=BulletinPriority.CODE_INFO).first()
            if default_type:
                self.fields['types'].initial = [default_type.pk]
            if default_priority:
                self.fields['priority'].initial = default_priority.pk
            self.fields['producers'].initial = list(producer_queryset.values_list('pk', flat=True))
        body_classes = self.fields['body'].widget.attrs.get('class', '')
        self.fields['body'].widget.attrs['class'] = f'{body_classes} rich-editor-source'.strip()

    def clean_body(self):
        body = sanitize_bulletin_body(self.cleaned_data.get('body') or '')
        self.sanitized_body = body
        text_content = strip_tags(body).replace('\xa0', '').strip()
        if not text_content:
            raise forms.ValidationError('Renseignez un message.')
        return body

    def clean_photos(self):
        photos = self.cleaned_data.get('photos') or []
        return _validate_uploaded_files(
            photos,
            max_count=BULLETIN_MAX_PHOTOS,
            max_size=BULLETIN_MAX_PHOTO_SIZE,
            allowed_extensions=BULLETIN_PHOTO_EXTENSIONS,
            label='Photos',
        )

    def clean_attachments(self):
        attachments = self.cleaned_data.get('attachments') or []
        return _validate_uploaded_files(
            attachments,
            max_count=BULLETIN_MAX_ATTACHMENTS,
            max_size=BULLETIN_MAX_ATTACHMENT_SIZE,
            allowed_extensions=BULLETIN_ATTACHMENT_EXTENSIONS,
            label='Pièces jointes',
        )

    def attachment_objects(self, bulletin):
        for uploaded_file in self.cleaned_data.get('photos') or []:
            yield BulletinAttachment(
                bulletin=bulletin,
                file=uploaded_file,
                original_name=uploaded_file.name,
                attachment_type=BulletinAttachment.TYPE_PHOTO,
            )
        for uploaded_file in self.cleaned_data.get('attachments') or []:
            yield BulletinAttachment(
                bulletin=bulletin,
                file=uploaded_file,
                original_name=uploaded_file.name,
                attachment_type=BulletinAttachment.TYPE_FILE,
            )


class ScoutingRecordForm(forms.ModelForm):
    class Meta:
        model = ScoutingRecord
        fields = [
            'plant_series',
            'crop',
            'scouting_date',
            'comment',
        ]
        widgets = {
            'scouting_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'comment': forms.Textarea(attrs={'rows': 3}),
        }
        labels = {
            'scouting_date': 'Date observation',
        }

    def __init__(self, *args, **kwargs):
        series_queryset = kwargs.pop('series_queryset', PlantSeries.objects.none())
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
        self.fields['scouting_date'].input_formats = ['%Y-%m-%d']
        self.fields['plant_series'].queryset = series_queryset
        self.fields['plant_series'].widget.attrs['class'] = 'form-select'
        self.fields['crop'].widget.attrs['class'] = 'form-select'
        self.fields['crop'].required = False
        self.fields['crop'].widget = forms.HiddenInput()


class QuickScoutingRecordForm(forms.ModelForm):
    class Meta:
        model = ScoutingRecord
        fields = [
            'plant_series',
            'crop',
            'scouting_date',
            'observed_plants_count',
            'observed_leaves_count',
            'aphid_infested_leaves_count',
            'comment',
        ]
        widgets = {
            'scouting_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'comment': forms.Textarea(attrs={'rows': 3}),
        }
        labels = {
            'scouting_date': 'Date observation',
            'observed_plants_count': 'Plants observes',
            'observed_leaves_count': 'Feuilles observees',
            'aphid_infested_leaves_count': 'Feuilles infestees de pucerons',
        }

    def __init__(self, *args, **kwargs):
        series_queryset = kwargs.pop('series_queryset', PlantSeries.objects.none())
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
        self.fields['scouting_date'].input_formats = ['%Y-%m-%d']
        self.fields['plant_series'].queryset = series_queryset
        self.fields['plant_series'].widget = forms.HiddenInput()
        self.fields['crop'].required = False
        self.fields['crop'].widget = forms.HiddenInput()
        for field_name in ('observed_plants_count', 'observed_leaves_count', 'aphid_infested_leaves_count'):
            self.fields[field_name].widget.attrs.update({'min': 0, 'step': 1})
        self.fields['observed_plants_count'].widget.attrs['form'] = 'quick-record-form'
        self.fields['observed_leaves_count'].widget.attrs['form'] = 'quick-record-form'
        self.fields['aphid_infested_leaves_count'].widget.attrs['form'] = 'quick-record-form'

    def clean(self):
        cleaned = super().clean()
        observed_plants = cleaned.get('observed_plants_count') or 0
        observed_leaves = cleaned.get('observed_leaves_count') or 0
        aphid_infested = cleaned.get('aphid_infested_leaves_count') or 0
        if observed_plants <= 0:
            self.add_error('observed_plants_count', 'Renseignez au moins 1 plant observe.')
        if observed_leaves <= 0:
            self.add_error('observed_leaves_count', 'Renseignez au moins 1 feuille observee.')
        if aphid_infested > observed_leaves:
            self.add_error(
                'aphid_infested_leaves_count',
                "Le nombre de feuilles infestees de pucerons ne peut pas depasser les feuilles observees.",
            )
        return cleaned


class UserProfileForm(forms.ModelForm):
    department = forms.ChoiceField(required=False, label='Département')
    email = forms.EmailField(required=False, label='Email')
    first_name = forms.CharField(required=False, max_length=150, label='Prénom')
    last_name = forms.CharField(required=False, max_length=150, label='Nom')
    bulletin_email_enabled = forms.BooleanField(
        required=False,
        label='Recevoir un email quand un nouveau bulletin est envoyé',
    )
    bulletin_email_urgent_only = forms.BooleanField(
        required=False,
        label='Recevoir seulement les bulletins urgents par email',
    )

    class Meta:
        model = UserProfile
        fields = [
            'farm_name',
            'photo',
            'phone',
            'street_address',
            'postal_code',
            'city',
            'department',
            'structure',
            'latitude',
            'longitude',
        ]
        widgets = {
            'latitude': forms.HiddenInput(),
            'longitude': forms.HiddenInput(),
        }
        labels = {
            'farm_name': 'Nom de ferme',
            'photo': 'Photo / logo',
            'phone': 'Mobile',
            'street_address': 'Adresse',
            'postal_code': 'Code postal',
            'city': 'Commune',
            'department': 'Département',
            'structure': 'Structure',
            'latitude': 'Latitude',
            'longitude': 'Longitude',
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
        self.fields['email'].widget.attrs['class'] = 'form-control'
        self.fields['phone'].widget.attrs['type'] = 'tel'
        self.fields['email'].widget.attrs['class'] = 'form-control'
        self.fields['email'].initial = self.user.email if self.user else ''
        self.fields['first_name'].widget.attrs['class'] = 'form-control'
        self.fields['last_name'].widget.attrs['class'] = 'form-control'
        self.fields['first_name'].initial = self.user.first_name if self.user else ''
        self.fields['last_name'].initial = self.user.last_name if self.user else ''
        if self.instance and self.instance.role == UserProfile.ROLE_PRODUCER and self.user:
            notification_preference = NotificationPreference.objects.get_or_create(user=self.user)[0]
            self.fields['bulletin_email_enabled'].initial = notification_preference.bulletin_email_enabled
            self.fields['bulletin_email_urgent_only'].initial = notification_preference.bulletin_email_urgent_only
            self.fields['bulletin_email_enabled'].widget.attrs['class'] = 'form-check-input'
            self.fields['bulletin_email_urgent_only'].widget.attrs['class'] = 'form-check-input'
        else:
            self.fields.pop('bulletin_email_enabled', None)
            self.fields.pop('bulletin_email_urgent_only', None)
        active_departments = list(Department.objects.filter(is_active=True).order_by('code'))
        department_choices = [('', '---------')] + [(d.code, d.label) for d in active_departments]
        current_department = (self.instance.department or '').strip() if self.instance else ''
        if current_department and all(code != current_department for code, _ in department_choices):
            fallback = Department.objects.filter(code=current_department).first()
            label = fallback.label if fallback else current_department
            department_choices.append((current_department, label))
        self.fields['department'].choices = department_choices
        self.fields['department'].widget.attrs['class'] = 'form-select'
        self.fields['structure'].queryset = TechnicianStructure.objects.order_by('name')
        self.fields['structure'].required = False
        self.fields['structure'].widget.attrs['class'] = 'form-select'
        if self.instance and self.instance.farm_address and not self.instance.street_address:
            self.fields['street_address'].initial = self.instance.farm_address
        if self.instance and self.instance.role == UserProfile.ROLE_PRODUCER:
            self.fields['structure'].widget = forms.HiddenInput()

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if not email:
            return ''
        qs = User.objects.filter(email__iexact=email)
        if self.user is not None:
            qs = qs.exclude(id=self.user.id)
        if qs.exists():
            raise forms.ValidationError('Cette adresse mail existe déjà.')
        return email

    def save(self, commit=True):
        profile = super().save(commit=False)
        if self.user is not None and self.user.is_superuser:
            profile.assigned_technician = None
        profile.sync_profile_fields()
        if self.user is not None:
            self.user.email = self.cleaned_data.get('email', '')
            self.user.first_name = self.cleaned_data.get('first_name', '')
            self.user.last_name = self.cleaned_data.get('last_name', '')
        if commit:
            if self.user is not None:
                self.user.save(update_fields=['email', 'first_name', 'last_name'])
            profile.save()
            if (
                self.user is not None
                and profile.role == UserProfile.ROLE_PRODUCER
                and 'bulletin_email_enabled' in self.cleaned_data
            ):
                notification_preference = NotificationPreference.objects.get_or_create(user=self.user)[0]
                notification_preference.bulletin_email_enabled = self.cleaned_data.get('bulletin_email_enabled', False)
                notification_preference.bulletin_email_urgent_only = self.cleaned_data.get(
                    'bulletin_email_urgent_only',
                    False,
                )
                notification_preference.save(
                    update_fields=['bulletin_email_enabled', 'bulletin_email_urgent_only', 'updated_at']
                )
        return profile


class ProducerAccountCreationForm(UserCreationForm):
    email = forms.EmailField(required=False, label='Email')
    technicians = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        label='Techniciens rattachés',
        required=True,
    )
    farm_name = forms.CharField(max_length=150, label='Nom de ferme')
    phone = forms.CharField(required=False, max_length=30, label='Mobile')
    street_address = forms.CharField(max_length=255, label='Adresse')
    postal_code = forms.CharField(max_length=10, label='Code postal')
    city = forms.CharField(max_length=120, label='Commune')
    latitude = forms.DecimalField(required=False, decimal_places=6, max_digits=9, widget=forms.HiddenInput())
    longitude = forms.DecimalField(required=False, decimal_places=6, max_digits=9, widget=forms.HiddenInput())
    crops_grown = forms.CharField(required=False, max_length=255, label='Mes cultures')
    tracked_plants = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}), label='Mes plants suivis')

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ['username', 'first_name', 'last_name', 'password1', 'password2']

    def __init__(self, *args, **kwargs):
        self.creator = kwargs.pop('creator')
        super().__init__(*args, **kwargs)
        self.technician_display_names = []
        for field in self.fields.values():
            if not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs['class'] = 'form-control'
        self.fields['email'].widget.attrs['class'] = 'form-control'
        self.fields['phone'].widget.attrs['type'] = 'tel'
        technician_qs = User.objects.filter(
            profile__role=UserProfile.ROLE_TECHNICIAN,
            profile__license_status=UserProfile.LICENSE_STATUS_ACTIVE,
        ).order_by(
            'first_name',
            'last_name',
            'username',
        )
        if self.creator.is_superuser:
            self.fields['technicians'].queryset = technician_qs
            self.fields['technicians'].widget.attrs['class'] = 'form-select'
            self.fields['technicians'].widget.attrs['size'] = 8
        else:
            self.fields['technicians'].queryset = technician_qs.filter(id=self.creator.id)
            self.fields['technicians'].initial = [self.creator.id]
            self.fields['technicians'].widget = forms.MultipleHiddenInput()
            self.technician_display_names = [display_user_name(self.creator)]

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if not email:
            return ''
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Cette adresse mail existe déjà.')
        return email

    def clean_technicians(self):
        technicians = list(self.cleaned_data['technicians'])
        if not technicians:
            raise forms.ValidationError('Sélectionnez au moins un technicien.')
        if not self.creator.is_superuser:
            ids = {technician.id for technician in technicians}
            if ids != {self.creator.id}:
                raise forms.ValidationError('Un technicien ne peut créer que des comptes rattachés à lui-même.')
        return technicians

    def clean(self):
        cleaned = super().clean()
        technicians = cleaned.get('technicians') or []
        for technician in technicians:
            technician_profile = UserProfile.objects.get_or_create(user=technician)[0]
            if technician_profile.role != UserProfile.ROLE_TECHNICIAN:
                self.add_error('technicians', 'Le rattachement doit pointer vers des techniciens.')
                break
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=commit)
        user.email = self.cleaned_data.get('email', '')
        if commit:
            user.save(update_fields=['email'])
        technicians = self.cleaned_data['technicians']
        first_technician = technicians[0] if technicians else None
        first_technician_profile = (
            UserProfile.objects.get_or_create(user=first_technician)[0] if first_technician else None
        )
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = UserProfile.ROLE_PRODUCER
        profile.assigned_technician = first_technician
        if first_technician_profile and first_technician_profile.department and not profile.department:
            profile.department = first_technician_profile.department
        profile.farm_name = self.cleaned_data['farm_name']
        profile.phone = self.cleaned_data.get('phone', '')
        profile.street_address = self.cleaned_data['street_address']
        profile.postal_code = self.cleaned_data['postal_code']
        profile.city = self.cleaned_data['city']
        profile.latitude = self.cleaned_data.get('latitude')
        profile.longitude = self.cleaned_data.get('longitude')
        profile.crops_grown = self.cleaned_data.get('crops_grown', '')
        profile.tracked_plants = self.cleaned_data.get('tracked_plants', '')
        profile.save()
        _sync_producer_technicians(profile, technicians, changed_by=self.creator)
        return user


class ProducerProfileUpdateForm(forms.ModelForm):
    username = forms.CharField(max_length=150, label='Identifiant')
    email = forms.EmailField(required=False, label='Email')
    first_name = forms.CharField(max_length=150, required=False, label='Prénom')
    last_name = forms.CharField(max_length=150, required=False, label='Nom')
    technicians = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        label='Techniciens rattachés',
        required=True,
    )

    class Meta:
        model = UserProfile
        fields = [
            'farm_name',
            'phone',
            'street_address',
            'postal_code',
            'city',
            'latitude',
            'longitude',
            'crops_grown',
            'tracked_plants',
        ]
        widgets = {
            'tracked_plants': forms.Textarea(attrs={'rows': 3}),
            'latitude': forms.HiddenInput(),
            'longitude': forms.HiddenInput(),
        }
        labels = {
            'farm_name': 'Nom de ferme',
            'phone': 'Mobile',
            'street_address': 'Adresse',
            'postal_code': 'Code postal',
            'city': 'Commune',
            'crops_grown': 'Mes cultures',
            'tracked_plants': 'Mes plants suivis',
        }

    def __init__(self, *args, **kwargs):
        self.editor = kwargs.pop('editor')
        self.producer_user = kwargs.pop('producer_user')
        super().__init__(*args, **kwargs)
        self.technician_display_names = []
        for field in self.fields.values():
            if not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs['class'] = 'form-control'
        self.fields['email'].widget.attrs['class'] = 'form-control'
        self.fields['phone'].widget.attrs['type'] = 'tel'
        technician_qs = User.objects.filter(
            profile__role=UserProfile.ROLE_TECHNICIAN,
            profile__license_status=UserProfile.LICENSE_STATUS_ACTIVE,
        ).order_by(
            'first_name',
            'last_name',
            'username',
        )
        active_technician_ids = list(
            self.instance.technician_assignments.filter(is_active=True).values_list('technician_id', flat=True)
        )
        if not active_technician_ids and self.instance.assigned_technician_id:
            active_technician_ids = [self.instance.assigned_technician_id]
        if self.editor.is_superuser:
            self.fields['technicians'].queryset = technician_qs
            self.fields['technicians'].widget.attrs['class'] = 'form-select'
            self.fields['technicians'].widget.attrs['size'] = 8
        else:
            self.fields['technicians'].queryset = technician_qs.filter(id=self.editor.id)
            self.fields['technicians'].initial = [self.editor.id]
            self.fields['technicians'].widget = forms.MultipleHiddenInput()
            self.technician_display_names = [display_user_name(self.editor)]

        profile = self.instance
        self.fields['username'].initial = self.producer_user.username
        self.fields['email'].initial = self.producer_user.email
        self.fields['first_name'].initial = self.producer_user.first_name
        self.fields['last_name'].initial = self.producer_user.last_name
        if self.editor.is_superuser:
            self.fields['technicians'].initial = active_technician_ids
        elif not self.technician_display_names:
            self.technician_display_names = [display_user_name(self.editor)]
        if profile and profile.farm_address and not profile.street_address:
            self.fields['street_address'].initial = profile.farm_address

    def clean_username(self):
        username = (self.cleaned_data.get('username') or '').strip()
        if not username:
            raise forms.ValidationError("L'identifiant est obligatoire.")
        exists = User.objects.exclude(id=self.producer_user.id).filter(username__iexact=username).exists()
        if exists:
            raise forms.ValidationError('Cet identifiant existe déjà.')
        return username

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if not email:
            return ''
        exists = User.objects.exclude(id=self.producer_user.id).filter(email__iexact=email).exists()
        if exists:
            raise forms.ValidationError('Cette adresse mail existe déjà.')
        return email

    def clean_technicians(self):
        technicians = list(self.cleaned_data['technicians'])
        if not technicians:
            raise forms.ValidationError('Sélectionnez au moins un technicien.')
        if not self.editor.is_superuser:
            ids = {technician.id for technician in technicians}
            if ids != {self.editor.id}:
                raise forms.ValidationError('Un technicien ne peut rattacher un producteur qu’à lui-même.')
        return technicians

    def clean(self):
        cleaned = super().clean()
        technicians = cleaned.get('technicians') or []
        for technician in technicians:
            technician_profile = UserProfile.objects.get_or_create(user=technician)[0]
            if technician_profile.role != UserProfile.ROLE_TECHNICIAN:
                self.add_error('technicians', 'Le rattachement doit pointer vers des techniciens.')
                break
        return cleaned

    def save(self, commit=True):
        user = self.producer_user
        user.username = self.cleaned_data['username']
        user.email = self.cleaned_data.get('email', '')
        user.first_name = self.cleaned_data.get('first_name', '')
        user.last_name = self.cleaned_data.get('last_name', '')
        if commit:
            user.save()

        profile = super().save(commit=False)
        technicians = self.cleaned_data['technicians']
        first_technician = technicians[0] if technicians else None
        first_technician_profile = (
            UserProfile.objects.get_or_create(user=first_technician)[0] if first_technician else None
        )
        profile.user = user
        profile.role = UserProfile.ROLE_PRODUCER
        profile.assigned_technician = first_technician
        if first_technician_profile and first_technician_profile.department and not profile.department:
            profile.department = first_technician_profile.department
        profile.sync_profile_fields()
        if commit:
            profile.save()
            _sync_producer_technicians(profile, technicians, changed_by=self.editor)
        return user


class ProducerImportForm(forms.Form):
    csv_file = forms.FileField(label='Fichier CSV')
    update_existing = forms.BooleanField(
        required=False,
        initial=True,
        label='Mettre à jour les producteurs déjà existants (recherche par email)',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['csv_file'].widget.attrs['class'] = 'form-control'
        self.fields['csv_file'].widget.attrs['accept'] = '.csv,text/csv'
        self.fields['update_existing'].widget.attrs['class'] = 'form-check-input'

    def clean_csv_file(self):
        csv_file = self.cleaned_data['csv_file']
        if not csv_file.name.lower().endswith('.csv'):
            raise forms.ValidationError('Importez un fichier .csv.')
        return csv_file


class TechnicianDeactivationForm(forms.Form):
    REASSIGN_MODE_NONE = 'none'
    REASSIGN_MODE_SELECTED = 'selected'
    REASSIGN_MODE_ALL = 'all'
    REASSIGN_MODE_CHOICES = [
        (REASSIGN_MODE_ALL, 'Réaffecter tous les producteurs sélectionnés'),
        (REASSIGN_MODE_SELECTED, 'Réaffecter seulement la sélection'),
        (REASSIGN_MODE_NONE, 'Ne rien reaffecter'),
    ]

    reassign_mode = forms.ChoiceField(choices=REASSIGN_MODE_CHOICES, label='Strategie')
    target_technician = TechnicianChoiceField(
        queryset=User.objects.none(),
        required=False,
        label='Technicien cible',
    )
    producers = ProducerChoiceField(
        queryset=User.objects.none(),
        required=False,
        label='Producteurs concernes',
    )
    deactivation_message = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
        label='Message producteur',
    )

    def __init__(self, *args, **kwargs):
        self.technician = kwargs.pop('technician')
        super().__init__(*args, **kwargs)
        self.fields['producers'].widget = forms.CheckboxSelectMultiple()
        active_assignments = ProducerTechnicianAssignment.objects.filter(
            technician=self.technician,
            is_active=True,
        ).select_related('producer_profile__user')
        producer_ids = [assignment.producer_profile.user_id for assignment in active_assignments]
        self.fields['producers'].queryset = (
            User.objects.filter(id__in=producer_ids)
            .select_related('profile')
            .order_by('profile__farm_name', 'username')
        )

        technicians_qs = User.objects.filter(
            profile__role=UserProfile.ROLE_TECHNICIAN,
            profile__license_status=UserProfile.LICENSE_STATUS_ACTIVE,
        ).exclude(id=self.technician.id).order_by('first_name', 'last_name', 'username')
        self.fields['target_technician'].queryset = technicians_qs

        self.fields['reassign_mode'].widget.attrs['class'] = 'form-select'
        self.fields['target_technician'].widget.attrs['class'] = 'form-select'
        self.fields['deactivation_message'].widget.attrs['class'] = 'form-control'

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get('reassign_mode')
        target = cleaned.get('target_technician')
        if mode in {self.REASSIGN_MODE_ALL, self.REASSIGN_MODE_SELECTED} and target is None:
            self.add_error('target_technician', 'Sélectionnez un technicien cible.')
        return cleaned


class TechnicianCoFollowRequestForm(forms.Form):
    target_technician = TechnicianChoiceField(queryset=User.objects.none(), label='Technicien cible')
    producers = ProducerChoiceField(
        queryset=User.objects.none(),
        required=True,
        label='Producteurs proposes',
    )
    message = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
        label='Message',
    )

    def __init__(self, *args, **kwargs):
        self.source_technician = kwargs.pop('source_technician')
        self.producer_queryset = kwargs.pop('producer_queryset', User.objects.none())
        super().__init__(*args, **kwargs)
        self.fields['producers'].widget = forms.CheckboxSelectMultiple()
        self.fields['target_technician'].queryset = User.objects.filter(
            profile__role=UserProfile.ROLE_TECHNICIAN,
            profile__license_status=UserProfile.LICENSE_STATUS_ACTIVE,
        ).exclude(id=self.source_technician.id).order_by('first_name', 'last_name', 'username')
        self.fields['producers'].queryset = self.producer_queryset
        self.fields['target_technician'].widget.attrs['class'] = 'form-select'
        self.fields['message'].widget.attrs['class'] = 'form-control'

    def save(self):
        request_obj = TechnicianCoFollowRequest.objects.create(
            source_technician=self.source_technician,
            target_technician=self.cleaned_data['target_technician'],
            message=(self.cleaned_data.get('message') or '').strip(),
            status=TechnicianCoFollowRequest.STATUS_PENDING,
        )
        producer_profiles = UserProfile.objects.filter(user__in=self.cleaned_data['producers'])
        TechnicianCoFollowRequestItem.objects.bulk_create(
            [
                TechnicianCoFollowRequestItem(
                    request=request_obj,
                    producer_profile=profile,
                    decision=TechnicianCoFollowRequestItem.DECISION_PENDING,
                )
                for profile in producer_profiles
            ]
        )
        return request_obj


class PlantSeriesForm(forms.ModelForm):
    new_variety_name = forms.CharField(required=False, label='Nouvelle variété (si absente)')
    service_plants = forms.ModelMultipleChoiceField(
        queryset=ServicePlant.objects.none(),
        required=False,
        label='Plantes de service',
        widget=forms.SelectMultiple(),
    )

    class Meta:
        model = PlantSeries
        fields = [
            'name',
            'photo',
            'crop',
            'conduct_type',
            'organic_mode',
            'variety',
            'greenhouse',
            'has_service_plants',
            'service_plants',
            'year',
            'planting_week',
            'plants_count',
            'leaves_per_plant',
            'is_active',
        ]
        labels = {
            'name': 'Nom de la série',
            'crop': 'Culture',
            'conduct_type': 'Conduite',
            'organic_mode': 'Mode de conduite',
            'variety': 'Variété',
            'greenhouse': 'Serre',
            'has_service_plants': 'Presence de plantes de service',
            'service_plants': 'Plantes de service',
            'year': 'Année',
            'planting_week': 'Numéro de la semaine de plantation',
            'plants_count': 'Nb plants',
            'leaves_per_plant': 'Nb feuilles / plant',
            'is_active': 'Série active',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
        for key in ['crop', 'conduct_type', 'organic_mode', 'variety']:
            self.fields[key].widget.attrs['class'] = 'form-select'
        self.fields['greenhouse'].widget.attrs['placeholder'] = 'Ex. Tunnel 2 / Serre nord'
        self.fields['planting_week'].widget.attrs.update({'min': 1, 'max': 53, 'placeholder': 'Ex. 14'})
        self.fields['crop'].queryset = Crop.objects.filter(is_active=True)
        self.fields['conduct_type'].queryset = ConductType.objects.filter(is_active=True)
        self.fields['variety'].queryset = Variety.objects.filter(is_active=True)
        self.fields['variety'].required = False
        service_plant_queryset = ServicePlant.objects.filter(is_active=True)
        if self.instance.pk:
            service_plant_queryset = ServicePlant.objects.filter(
                Q(is_active=True) | Q(pk__in=self.instance.service_plants.values('pk'))
            ).distinct()
        self.fields['service_plants'].queryset = service_plant_queryset
        self.fields['service_plants'].widget.attrs['class'] = 'd-none'
        self.fields['has_service_plants'].widget.attrs['class'] = 'form-check-input'
        self.fields['is_active'].widget.attrs['class'] = 'form-check-input'
        self.fields['new_variety_name'].widget.attrs.update(
            {
                'list': 'varietySuggestions',
                'autocomplete': 'on',
                'placeholder': 'Tapez pour auto-completion',
            }
        )

    def clean(self):
        cleaned = super().clean()
        crop = cleaned.get('crop')
        variety = cleaned.get('variety')
        new_variety_name = (cleaned.get('new_variety_name') or '').strip()
        has_service_plants = cleaned.get('has_service_plants')
        service_plants = cleaned.get('service_plants')
        if variety and crop and variety.crop_id != crop.id:
            self.add_error('variety', 'La variété doit appartenir à la culture choisie.')
        if not variety and not new_variety_name:
            self.add_error('variety', 'Choisissez une variété ou renseignez une nouvelle variété.')
        if has_service_plants and not service_plants:
            self.add_error('service_plants', 'Choisissez au moins une plante de service.')
        if not has_service_plants:
            cleaned['service_plants'] = ServicePlant.objects.none()
        return cleaned


class PlantActionForm(forms.ModelForm):
    class Meta:
        model = PlantAction
        fields = [
            'plant_series',
            'action_date',
            'action_type',
            'scope',
            'molecule',
            'auxiliary_taxon',
            'notes',
        ]
        widgets = {
            'action_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }
        labels = {
            'action_date': "Date d'action",
            'action_type': "Type d'action",
            'scope': 'Portée',
            'auxiliary_taxon': 'Auxiliaire lâché',
            'notes': 'Détails',
        }

    def __init__(self, *args, **kwargs):
        series_queryset = kwargs.pop('series_queryset', PlantSeries.objects.none())
        selected_series = kwargs.pop('selected_series', None)
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            else:
                field.widget.attrs['class'] = 'form-control'
        self.fields['action_date'].input_formats = ['%Y-%m-%d']

        self.fields['plant_series'].queryset = series_queryset
        self.fields['action_type'].queryset = ActionType.objects.filter(is_active=True).order_by('display_order', 'name')
        self.fields['auxiliary_taxon'].queryset = AuxiliaryTaxon.objects.filter(
            is_active=True,
            is_releasable=True,
        ).order_by('display_order', 'name')

        if selected_series is not None:
            self.fields['plant_series'].initial = selected_series
            self.fields['plant_series'].widget = forms.HiddenInput()

        selected_series_id = None
        if selected_series is not None:
            selected_series_id = selected_series.id
        else:
            selected_series_id = self.data.get('plant_series') or self.initial.get('plant_series')
        series = series_queryset.filter(id=selected_series_id).first() if selected_series_id else None
        molecules = Molecule.objects.filter(is_active=True)
        if series:
            molecules = molecules.filter(crops=series.crop, organic_scope__in=[series.organic_mode, 'both']).distinct()
        else:
            molecules = molecules.none()
        self.fields['molecule'].queryset = molecules.order_by('name')

    def clean(self):
        cleaned = super().clean()
        action_type = cleaned.get('action_type')
        molecule = cleaned.get('molecule')
        auxiliary_taxon = cleaned.get('auxiliary_taxon')
        if not action_type:
            return cleaned
        if action_type.category != 'treatment' and molecule:
            self.add_error('molecule', 'La molécule est réservée au type traitement.')
        if action_type.category == 'release':
            if not auxiliary_taxon:
                self.add_error('auxiliary_taxon', 'Choisissez un auxiliaire à lâcher.')
        elif auxiliary_taxon:
            self.add_error('auxiliary_taxon', 'Cet auxiliaire est réservé au type lâcher.')
        return cleaned


class RecommendationDismissForm(forms.Form):
    dismiss_reason = forms.ModelChoiceField(
        queryset=RecommendationDismissReason.objects.none(),
        required=False,
        empty_label='Pourquoi ne pas suivre ? (facultatif)',
        label='Motif',
    )
    dismiss_note = forms.CharField(
        required=False,
        label='Précision libre',
        widget=forms.Textarea(attrs={'rows': 2}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['dismiss_reason'].queryset = RecommendationDismissReason.objects.filter(is_active=True).order_by(
            'display_order',
            'label',
        )
        self.fields['dismiss_reason'].widget.attrs['class'] = 'form-select form-select-sm js-dismiss-reason'
        self.fields['dismiss_note'].widget.attrs['class'] = 'form-control form-control-sm js-dismiss-note d-none'
