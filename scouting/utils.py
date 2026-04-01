def display_user_name(user):
    if not user:
        return ''
    full_name = user.get_full_name().strip()
    return full_name or user.username
