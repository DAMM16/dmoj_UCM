from judge.models import Profile


def filter_users_by_shared_class(queryset, user):
    """Limit a user queryset to members sharing a class with ``user``.

    Users without a class (including anonymous users) retain the site-wide
    view. A profile may belong to more than one class, so the result is the
    distinct union of all of their classmates.
    """
    if not user.is_authenticated:
        return queryset

    classes = user.profile.classes.all()
    if not classes.exists():
        return queryset

    classmate_ids = Profile.objects.filter(class__in=classes).values('id')
    return queryset.filter(id__in=classmate_ids)
