from rest_framework import serializers

from .models import (
    AuxiliaryTaxon,
    ConductType,
    Crop,
    LeafAuxiliaryObservation,
    LeafOtherPestObservation,
    LeafObservation,
    OtherPestTaxon,
    PlantSeries,
    QuickRecordAphidSpecies,
    QuickRecordAuxiliaryCount,
    QuickRecordOtherPestCount,
    ScoutingRecord,
    UserProfile,
    Variety,
)


class LeafAuxiliaryObservationSerializer(serializers.ModelSerializer):
    taxon_name = serializers.CharField(source='taxon.name', read_only=True)

    class Meta:
        model = LeafAuxiliaryObservation
        fields = ['id', 'taxon', 'taxon_name', 'count']


class LeafOtherPestObservationSerializer(serializers.ModelSerializer):
    taxon_name = serializers.CharField(source='taxon.name', read_only=True)

    class Meta:
        model = LeafOtherPestObservation
        fields = ['id', 'taxon', 'taxon_name']


class LeafObservationSerializer(serializers.ModelSerializer):
    auxiliary_observations = LeafAuxiliaryObservationSerializer(many=True, required=False)
    other_pest_observations = LeafOtherPestObservationSerializer(many=True, required=False)

    class Meta:
        model = LeafObservation
        fields = ['id', 'plant_number', 'leaf_position', 'aphid_present', 'auxiliary_observations', 'other_pest_observations']


class QuickRecordAphidSpeciesSerializer(serializers.ModelSerializer):
    species_name = serializers.StringRelatedField(source='species', read_only=True)

    class Meta:
        model = QuickRecordAphidSpecies
        fields = ['id', 'species', 'species_name']


class QuickRecordAuxiliaryCountSerializer(serializers.ModelSerializer):
    taxon_name = serializers.CharField(source='taxon.name', read_only=True)

    class Meta:
        model = QuickRecordAuxiliaryCount
        fields = ['id', 'taxon', 'taxon_name', 'count']


class QuickRecordOtherPestCountSerializer(serializers.ModelSerializer):
    taxon_name = serializers.CharField(source='taxon.name', read_only=True)

    class Meta:
        model = QuickRecordOtherPestCount
        fields = ['id', 'taxon', 'taxon_name', 'infested_leaves_count']


class ScoutingRecordSerializer(serializers.ModelSerializer):
    leaf_observations = LeafObservationSerializer(many=True, required=False)
    quick_aphid_species = QuickRecordAphidSpeciesSerializer(many=True, read_only=True)
    quick_auxiliary_counts = QuickRecordAuxiliaryCountSerializer(many=True, read_only=True)
    quick_other_pest_counts = QuickRecordOtherPestCountSerializer(many=True, read_only=True)
    risk_level = serializers.CharField(read_only=True)
    auxiliaries_per_plant = serializers.FloatField(read_only=True)

    class Meta:
        model = ScoutingRecord
        fields = [
            'id',
            'plant_series',
            'department',
            'crop',
            'crop_ref',
            'conduct_type_ref',
            'variety_ref',
            'scouting_date',
            'year',
            'week',
            'entry_mode',
            'observed_plants_count',
            'observed_leaves_count',
            'aphid_infested_leaves_count',
            'aphid_infested_percent',
            'auxiliary_total',
            'comment',
            'risk_level',
            'auxiliaries_per_plant',
            'leaf_observations',
            'quick_aphid_species',
            'quick_auxiliary_counts',
            'quick_other_pest_counts',
            'created_at',
        ]
        read_only_fields = [
            'department',
            'crop',
            'crop_ref',
            'conduct_type_ref',
            'variety_ref',
            'year',
            'week',
            'created_at',
        ]

    def create(self, validated_data):
        leaves_data = validated_data.pop('leaf_observations', [])
        request = self.context['request']
        user = request.user
        profile = UserProfile.objects.get_or_create(user=user)[0]
        series = validated_data['plant_series']
        owner_profile = UserProfile.objects.get_or_create(user=series.user)[0]

        if profile.role == UserProfile.ROLE_TECHNICIAN and not user.is_superuser:
            validated_data['user'] = series.user
        else:
            validated_data['user'] = user
        validated_data['department'] = owner_profile.department or profile.department
        validated_data['crop'] = series.crop.name
        validated_data['crop_ref'] = series.crop
        validated_data['conduct_type_ref'] = series.conduct_type
        validated_data['variety_ref'] = series.variety
        iso_date = validated_data['scouting_date'].isocalendar()
        validated_data['year'] = iso_date.year
        validated_data['week'] = iso_date.week
        validated_data['auxiliary_mode'] = 'detailed'
        validated_data['aphid_infested_percent'] = 0
        validated_data['auxiliary_total'] = 0

        record = ScoutingRecord.objects.create(**validated_data)
        self._save_leaves(record, leaves_data)
        record.recompute_from_leaf_observations()
        return record

    def update(self, instance, validated_data):
        leaves_data = validated_data.pop('leaf_observations', None)
        if 'scouting_date' in validated_data:
            iso_date = validated_data['scouting_date'].isocalendar()
            validated_data['year'] = iso_date.year
            validated_data['week'] = iso_date.week
        if 'plant_series' in validated_data:
            series = validated_data['plant_series']
            validated_data['crop'] = series.crop.name
            validated_data['crop_ref'] = series.crop
            validated_data['conduct_type_ref'] = series.conduct_type
            validated_data['variety_ref'] = series.variety

        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.save()

        if leaves_data is not None:
            instance.leaf_observations.all().delete()
            self._save_leaves(instance, leaves_data)
            instance.recompute_from_leaf_observations()
        return instance

    def _save_leaves(self, record, leaves_data):
        for leaf_data in leaves_data:
            aux_data = leaf_data.pop('auxiliary_observations', [])
            pest_data = leaf_data.pop('other_pest_observations', [])
            leaf = LeafObservation.objects.create(record=record, **leaf_data)
            for aux in aux_data:
                LeafAuxiliaryObservation.objects.create(leaf_observation=leaf, **aux)
            for pest in pest_data:
                LeafOtherPestObservation.objects.create(leaf_observation=leaf, **pest)

    def validate(self, attrs):
        request = self.context['request']
        user = request.user
        profile = UserProfile.objects.get_or_create(user=user)[0]
        if profile.role == UserProfile.ROLE_TECHNICIAN and not user.is_superuser and not profile.has_active_license:
            raise serializers.ValidationError('Votre licence technicien est inactive.')
        series = attrs.get('plant_series')
        if series and not user.is_superuser:
            if profile.role == UserProfile.ROLE_PRODUCER and series.user_id != user.id:
                raise serializers.ValidationError('Cette serie ne vous appartient pas.')
            if profile.role == UserProfile.ROLE_TECHNICIAN:
                owner_profile = UserProfile.objects.get_or_create(user=series.user)[0]
                has_assignment = owner_profile.technician_assignments.filter(
                    is_active=True,
                    technician=user,
                    technician__profile__license_status=UserProfile.LICENSE_STATUS_ACTIVE,
                ).exists()
                if not has_assignment:
                    raise serializers.ValidationError("Cette serie n'est pas rattachee a votre compte technicien.")
        return attrs


class AuxiliaryTaxonSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuxiliaryTaxon
        fields = ['id', 'code', 'name', 'display_order', 'is_active', 'photo']


class OtherPestTaxonSerializer(serializers.ModelSerializer):
    class Meta:
        model = OtherPestTaxon
        fields = ['id', 'code', 'name', 'display_order', 'is_active', 'photo']


class UserProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    active_technicians = serializers.SerializerMethodField()

    def get_active_technicians(self, obj):
        if obj.role != UserProfile.ROLE_PRODUCER:
            return []
        rows = obj.technician_assignments.filter(
            is_active=True,
            technician__profile__license_status=UserProfile.LICENSE_STATUS_ACTIVE,
        ).select_related('technician')
        return [
            {
                'id': row.technician_id,
                'username': row.technician.username,
                'display_name': str(row.technician.get_full_name() or row.technician.username),
            }
            for row in rows
        ]

    class Meta:
        model = UserProfile
        fields = [
            'id',
            'username',
            'role',
            'active_technicians',
            'department',
            'farm_name',
            'farm_address',
            'street_address',
            'postal_code',
            'city',
            'latitude',
            'longitude',
            'crops_grown',
            'tracked_plants',
        ]
        read_only_fields = [
            'role',
            'active_technicians',
            'department',
            'farm_address',
        ]


class CropSerializer(serializers.ModelSerializer):
    class Meta:
        model = Crop
        fields = ['id', 'name', 'is_active']


class ConductTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConductType
        fields = ['id', 'name', 'is_active']


class VarietySerializer(serializers.ModelSerializer):
    class Meta:
        model = Variety
        fields = ['id', 'crop', 'name', 'is_active', 'created_by']
        read_only_fields = ['created_by']


class PlantSeriesSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlantSeries
        fields = [
            'id',
            'name',
            'photo',
            'user',
            'crop',
            'conduct_type',
            'organic_mode',
            'variety',
            'greenhouse',
            'planting_week',
            'plants_count',
            'leaves_per_plant',
            'is_active',
            'created_at',
        ]
        read_only_fields = ['user', 'created_at']
