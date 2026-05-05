from __future__ import annotations

from modules.notifications_module.profiles import PROFILES


PROFILES_BY_SENDER: dict[str, tuple] = {}
FALLBACK_PROFILES: list = []

for profile in PROFILES:
    reporter_emails = tuple(profile.rule.reporter_emails)
    if not reporter_emails:
        FALLBACK_PROFILES.append(profile)
        continue

    for sender_email in reporter_emails:
        normalized_sender = sender_email.lower()
        existing_profiles = PROFILES_BY_SENDER.get(normalized_sender, ())
        PROFILES_BY_SENDER[normalized_sender] = (*existing_profiles, profile)


def get_candidate_profiles(reporter_email: str) -> tuple:
    normalized_email = reporter_email.lower().strip()
    bucket_profiles = PROFILES_BY_SENDER.get(normalized_email, ())
    if not FALLBACK_PROFILES:
        return bucket_profiles
    return (*bucket_profiles, *FALLBACK_PROFILES)