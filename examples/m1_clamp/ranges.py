def clamp(value: int, lower: int, upper: int) -> int:
    if value <= lower:
        return lower
    if value >= upper:
        return upper
    return upper
