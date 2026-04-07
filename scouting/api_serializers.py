from rest_framework import serializers

from .models import (
    AuxiliaryTaxon,
    ConductType,
    Crop,
    LeafAuxiliaryObservation,
    LeafObservation,
    PlantSeries,
    ScoutingRecord,
    UserProfile,
    Variety,
)


class LeafAuxiliaryObservationSerializer(serializers.ModelSerializer):
    taxon_name = serializers.CharField(source='taxon.name', read_only=True)

    class Meta:
        model = LeafAuxiliaryObservation
        fields = ['id', 'taxon', 'taxon_name', 'count']


class LeafObservationSerializer(serializers.ModelSerializer):
    auxiliary_observations = LeafAuxiliaryObservationSerializer(many=True, required=False)

    class Meta:
        model = LeafObservation
        fields = ['id', 'plant_number', 'leaf_position', 'aphid_present', 'auxiliary_observations']


class ScoutingRecordSerializer(serializers.ModelSerializer):
    leaf_observations = LeafObservationSerializer(many=True, required=False)
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
            'aphid_infested_percent',
            'auxiliary_total',
            'comment',
            'risk_level',
            'auxiliaries_per_plant',
            'leaf_observations',
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
            'aphid_infested_percent',
            'auxiliary_total',
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
            leaf = LeafObservation.objects.create(record=record, **leaf_data)
            for aux in aux_data:
                LeafAuxiliaryObservation.objects.create(leaf_observation=leaf, **aux)

    def validate(self, attrs):
        request = self.context['request']
        user = request.user
        profile = UserProfile.objects.get_or_create(user=user)[0]
        if not profile.department:
            raise serializers.ValidationError('Le departement doit etre renseigne dans Mon profil.')
        series = attrs.get('plant_series')
        if series and not user.is_superuser:
            if profile.role == UserProfile.ROLE_PRODUCER and series.user_id != user.id:
                raise serializers.ValidationError('Cette serie ne vous appartient pas.')
            if profile.role == UserProfile.ROLE_TECHNICIAN:
                owner_profile = UserProfile.objects.get_or_create(user=series.user)[0]
                if owner_profile.assigned_technician_id not in [None, user.id]:
                    raise serializers.ValidationError("Cette serie n'est pas rattachee a votre compte technicien.")
                if owner_profile.assigned_technician_id is None and owner_profile.department != profile.department:
                    raise serializers.ValidationError("Cette serie n'est pas dans votre perimetre technicien.")
        return attrs


class AuxiliaryTaxonSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuxiliaryTaxon
        fields = ['id', 'code', 'name', 'display_order', 'is_active', 'photo']


class UserProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    assigned_technician_username = serializers.CharField(source='assigned_technician.username', read_only=True)

    class Meta:
        model = UserProfile
        fields = [
            'id',
            'username',
            'role',
            'assigned_technician',
            'assigned_technician_username',
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
            'assigned_technician',
            'assigned_technician_username',
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
            'planting_week',
            'plants_count',
            'leaves_per_plant',
            'is_active',
            'created_at',
        ]
        read_only_fields = ['user', 'created_at']
