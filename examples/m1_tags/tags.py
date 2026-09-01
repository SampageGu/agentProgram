def normalize_tags(tags: list[str]) -> list[str]:
    """Trim tags, discard empty entries, and preserve first-seen order."""

    return [tag.strip().lower() for tag in tags]
