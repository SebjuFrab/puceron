from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class UsernameOrEmailBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(get_user_model().USERNAME_FIELD) or kwargs.get('email')
        if username is None or password is None:
            return None

        UserModel = get_user_model()
        identifier = str(username).strip()
        if not identifier:
            return None

        username_field = UserModel.USERNAME_FIELD
        username_lookup = {f'{username_field}__iexact': identifier}
        user = UserModel._default_manager.filter(**username_lookup).first()
        if user is None:
            email_matches = list(
                UserModel._default_manager.filter(email__iexact=identifier).only('id', 'password', 'is_active')[:2]
            )
            if len(email_matches) != 1:
                UserModel().set_password(password)
                return None
            user = email_matches[0]

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
