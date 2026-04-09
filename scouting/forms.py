from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

from .models import (
    ActionType,
    AphidSpecies,
    AuxiliaryTaxon,
    ConductType,
    Crop,
    Molecule,
    OtherPestTaxon,
    PlantAction,
    PlantSeries,
    RecommendationDismissReason,
    ScoutingRecord,
    UserProfile,
    Variety,
)
from .utils import display_user_name

User = get_user_model()


class TechnicianChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return display_user_name(obj)


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
    email = forms.EmailField(required=False, label='Email')
    first_name = forms.CharField(required=False, max_length=150, label='PrÃ©nom')
    last_name = forms.CharField(required=False, max_length=150, label='Nom')

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
            'department': 'DÃ©partement',
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
        self.fields['department'].widget.attrs['class'] = 'form-select'
        if self.instance and self.instance.farm_address and not self.instance.street_address:
            self.fields['street_address'].initial = self.instance.farm_address
        if self.instance and self.instance.role == UserProfile.ROLE_PRODUCER and self.instance.assigned_technician_id:
            self.fields['department'].disabled = True

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if not email:
            return ''
        qs = User.objects.filter(email__iexact=email)
        if self.user is not None:
            qs = qs.exclude(id=self.user.id)
        if qs.exists():
            raise forms.ValidationError('Cette adresse mail existe deja.')
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
        return profile


class ProducerAccountCreationForm(UserCreationForm):
    email = forms.EmailField(required=False, label='Email')
    technician = TechnicianChoiceField(queryset=User.objects.none(), label='Technicien rÃ©fÃ©rent')
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
        self.technician_display_name = ''
        for field in self.fields.values():
            if not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs['class'] = 'form-control'
        self.fields['email'].widget.attrs['class'] = 'form-control'
        self.fields['phone'].widget.attrs['type'] = 'tel'
        technician_qs = User.objects.filter(profile__role=UserProfile.ROLE_TECHNICIAN).order_by(
            'first_name',
            'last_name',
            'username',
        )
        if self.creator.is_superuser:
            self.fields['technician'].queryset = technician_qs
            self.fields['technician'].widget.attrs['class'] = 'form-select'
        else:
            self.fields['technician'].queryset = technician_qs.filter(id=self.creator.id)
            self.fields['technician'].initial = self.creator
            self.fields['technician'].widget = forms.HiddenInput()
            self.technician_display_name = display_user_name(self.creator)

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if not email:
            return ''
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Cette adresse mail existe deja.')
        return email

    def clean_technician(self):
        technician = self.cleaned_data['technician']
        if not self.creator.is_superuser and technician != self.creator:
            raise forms.ValidationError('Un technicien ne peut creer que des comptes rattaches a lui-meme.')
        return technician

    def clean(self):
        cleaned = super().clean()
        technician = cleaned.get('technician')
        if technician:
            technician_profile = UserProfile.objects.get_or_create(user=technician)[0]
            if technician_profile.role != UserProfile.ROLE_TECHNICIAN:
                self.add_error('technician', 'Le rattachement doit pointer vers un technicien.')
            if not technician_profile.department:
                self.add_error('technician', 'Le technicien doit avoir un dÃ©partement renseignÃ©.')
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=commit)
        user.email = self.cleaned_data.get('email', '')
        if commit:
            user.save(update_fields=['email'])
        technician = self.cleaned_data['technician']
        technician_profile = UserProfile.objects.get_or_create(user=technician)[0]
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = UserProfile.ROLE_PRODUCER
        profile.assigned_technician = technician
        profile.department = technician_profile.department
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
        return user


class ProducerProfileUpdateForm(forms.ModelForm):
    username = forms.CharField(max_length=150, label='Identifiant')
    email = forms.EmailField(required=False, label='Email')
    first_name = forms.CharField(max_length=150, required=False, label='PrÃ©nom')
    last_name = forms.CharField(max_length=150, required=False, label='Nom')
    technician = TechnicianChoiceField(queryset=User.objects.none(), label='Technicien rÃ©fÃ©rent')

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
        self.technician_display_name = ''
        for field in self.fields.values():
            if not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs['class'] = 'form-control'
        self.fields['email'].widget.attrs['class'] = 'form-control'
        self.fields['phone'].widget.attrs['type'] = 'tel'
        technician_qs = User.objects.filter(profile__role=UserProfile.ROLE_TECHNICIAN).order_by(
            'first_name',
            'last_name',
            'username',
        )
        if self.editor.is_superuser:
            self.fields['technician'].queryset = technician_qs
            self.fields['technician'].widget.attrs['class'] = 'form-select'
        else:
            self.fields['technician'].queryset = technician_qs.filter(id=self.editor.id)
            self.fields['technician'].initial = self.editor
            self.fields['technician'].widget = forms.HiddenInput()

        profile = self.instance
        self.fields['username'].initial = self.producer_user.username
        self.fields['email'].initial = self.producer_user.email
        self.fields['first_name'].initial = self.producer_user.first_name
        self.fields['last_name'].initial = self.producer_user.last_name
        self.fields['technician'].initial = profile.assigned_technician or (
            self.editor if not self.editor.is_superuser else None
        )
        technician_user = profile.assigned_technician or (self.editor if not self.editor.is_superuser else None)
        self.technician_display_name = display_user_name(technician_user)
        if profile and profile.farm_address and not profile.street_address:
            self.fields['street_address'].initial = profile.farm_address

    def clean_username(self):
        username = (self.cleaned_data.get('username') or '').strip()
        if not username:
            raise forms.ValidationError("L'identifiant est obligatoire.")
        exists = User.objects.exclude(id=self.producer_user.id).filter(username__iexact=username).exists()
        if exists:
            raise forms.ValidationError('Cet identifiant existe deja.')
        return username

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if not email:
            return ''
        exists = User.objects.exclude(id=self.producer_user.id).filter(email__iexact=email).exists()
        if exists:
            raise forms.ValidationError('Cette adresse mail existe deja.')
        return email

    def clean_technician(self):
        technician = self.cleaned_data['technician']
        if not self.editor.is_superuser and technician != self.editor:
            raise forms.ValidationError('Un technicien ne peut rattacher un producteur qu a lui-meme.')
        return technician

    def clean(self):
        cleaned = super().clean()
        technician = cleaned.get('technician')
        if technician:
            technician_profile = UserProfile.objects.get_or_create(user=technician)[0]
            if technician_profile.role != UserProfile.ROLE_TECHNICIAN:
                self.add_error('technician', 'Le rattachement doit pointer vers un technicien.')
            if not technician_profile.department:
                self.add_error('technician', 'Le technicien doit avoir un dÃ©partement renseignÃ©.')
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
        technician = self.cleaned_data['technician']
        technician_profile = UserProfile.objects.get_or_create(user=technician)[0]
        profile.user = user
        profile.role = UserProfile.ROLE_PRODUCER
        profile.assigned_technician = technician
        profile.department = technician_profile.department
        profile.sync_profile_fields()
        if commit:
            profile.save()
        return user


class ProducerImportForm(forms.Form):
    csv_file = forms.FileField(label='Fichier CSV')
    update_existing = forms.BooleanField(
        required=False,
        initial=True,
        label='Mettre a jour les producteurs deja existants (recherche par email)',
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


class PlantSeriesForm(forms.ModelForm):
    new_variety_name = forms.CharField(required=False, label='Nouvelle variÃ©tÃ© (si absente)')

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
            'year',
            'planting_week',
            'plants_count',
            'leaves_per_plant',
            'is_active',
        ]
        labels = {
            'name': 'Nom de la sÃ©rie',
            'crop': 'Culture',
            'conduct_type': 'Conduite',
            'organic_mode': 'Mode de conduite',
            'variety': 'VariÃ©tÃ©',
            'greenhouse': 'Serre',
            'year': 'AnnÃ©e',
            'planting_week': 'NumÃ©ro de la semaine de plantation',
            'plants_count': 'Nb plants',
            'leaves_per_plant': 'Nb feuilles / plant',
            'is_active': 'SÃ©rie active',
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
        if variety and crop and variety.crop_id != crop.id:
            self.add_error('variety', 'La variete doit appartenir a la culture choisie.')
        if not variety and not new_variety_name:
            self.add_error('variety', 'Choisissez une variete ou renseignez une nouvelle variete.')
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
            'auxiliary_taxon': 'Auxiliaire lache',
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
        if action_type.category == 'treatment':
            if not molecule:
                self.add_error('molecule', 'Choisissez une molecule.')
        elif molecule:
            self.add_error('molecule', 'La molecule est reservee au type traitement.')
        if action_type.category == 'release':
            if not auxiliary_taxon:
                self.add_error('auxiliary_taxon', 'Choisissez un auxiliaire a lacher.')
        elif auxiliary_taxon:
            self.add_error('auxiliary_taxon', 'Cet auxiliaire est reserve au type lacher.')
        return cleaned


class RecommendationDismissForm(forms.Form):
    dismiss_reason = forms.ModelChoiceField(
        queryset=RecommendationDismissReason.objects.none(),
        required=False,
        empty_label='Pourquoi ne pas suivre Ã© (facultatif)',
        label='Motif',
    )
    dismiss_note = forms.CharField(
        required=False,
        label='Precision libre',
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


