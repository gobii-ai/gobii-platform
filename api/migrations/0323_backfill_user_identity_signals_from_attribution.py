from ipaddress import ip_address, ip_network

from django.db import migrations


BACKFILL_SOURCE = "backfill_attribution"
BATCH_SIZE = 500


def _clean_value(raw):
    if raw is None:
        return ""
    return str(raw).strip().strip('"')


def _normalize_ga_client_id(raw):
    value = _clean_value(raw)
    if not value:
        return ""

    parts = value.split(".")
    if len(parts) >= 4 and parts[0].upper().startswith("GA"):
        trailing_parts = parts[-2:]
        if all(part.isdigit() for part in trailing_parts):
            return ".".join(trailing_parts)
    return value


def _normalize_ip(raw):
    value = _clean_value(raw)
    if not value:
        return ""
    try:
        return ip_address(value).compressed
    except ValueError:
        return ""


def _normalize_ip_prefix(raw):
    normalized_ip = _normalize_ip(raw)
    if not normalized_ip:
        return ""

    parsed = ip_address(normalized_ip)
    if parsed.version == 4:
        network = ip_network(f"{parsed}/24", strict=False)
    else:
        network = ip_network(f"{parsed}/56", strict=False)
    return str(network)


def _build_signal(UserIdentitySignal, *, user_id, signal_type, signal_value, observed_at):
    return UserIdentitySignal(
        user_id=user_id,
        signal_type=signal_type,
        signal_value=signal_value,
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        first_seen_source=BACKFILL_SOURCE,
        last_seen_source=BACKFILL_SOURCE,
        observation_count=1,
    )


def backfill_user_identity_signals(apps, schema_editor):
    UserAttribution = apps.get_model("api", "UserAttribution")
    UserIdentitySignal = apps.get_model("api", "UserIdentitySignal")
    alias = schema_editor.connection.alias

    pending = []
    queryset = UserAttribution.objects.using(alias).only(
        "user_id",
        "ga_client_id",
        "fbp",
        "last_client_ip",
        "last_touch_at",
        "updated_at",
        "created_at",
    )

    for attribution in queryset.iterator(chunk_size=BATCH_SIZE):
        observed_at = attribution.last_touch_at or attribution.updated_at or attribution.created_at

        ga_client_id = _normalize_ga_client_id(attribution.ga_client_id)
        if ga_client_id:
            pending.append(
                _build_signal(
                    UserIdentitySignal,
                    user_id=attribution.user_id,
                    signal_type="ga_client_id",
                    signal_value=ga_client_id,
                    observed_at=observed_at,
                )
            )

        fbp = _clean_value(attribution.fbp)
        if fbp:
            pending.append(
                _build_signal(
                    UserIdentitySignal,
                    user_id=attribution.user_id,
                    signal_type="fbp",
                    signal_value=fbp,
                    observed_at=observed_at,
                )
            )

        ip_exact = _normalize_ip(attribution.last_client_ip)
        if ip_exact:
            pending.append(
                _build_signal(
                    UserIdentitySignal,
                    user_id=attribution.user_id,
                    signal_type="ip_exact",
                    signal_value=ip_exact,
                    observed_at=observed_at,
                )
            )

        ip_prefix = _normalize_ip_prefix(attribution.last_client_ip)
        if ip_prefix:
            pending.append(
                _build_signal(
                    UserIdentitySignal,
                    user_id=attribution.user_id,
                    signal_type="ip_prefix",
                    signal_value=ip_prefix,
                    observed_at=observed_at,
                )
            )

        if len(pending) >= BATCH_SIZE:
            UserIdentitySignal.objects.using(alias).bulk_create(
                pending,
                batch_size=BATCH_SIZE,
                ignore_conflicts=True,
            )
            pending = []

    if pending:
        UserIdentitySignal.objects.using(alias).bulk_create(
            pending,
            batch_size=BATCH_SIZE,
            ignore_conflicts=True,
        )


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("api", "0322_usertrialeligibility_useridentitysignal"),
    ]

    operations = [
        migrations.RunPython(
            backfill_user_identity_signals,
            migrations.RunPython.noop,
        ),
    ]
