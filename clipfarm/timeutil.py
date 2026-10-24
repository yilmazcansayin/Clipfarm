"""Zaman ifadelerini saniyeye çevirme yardımcıları."""

from __future__ import annotations


def parse_time(value: str | float | int | None) -> float | None:
    """'1:02:03', '12:30', '95', '95.5' veya '1h2m3s' biçimini saniyeye çevirir."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = value.strip().lower()
    if any(unit in text for unit in "hms") and ":" not in text:
        total, number = 0.0, ""
        for ch in text:
            if ch.isdigit() or ch == ".":
                number += ch
            elif ch in "hms":
                if not number:
                    raise ValueError(f"Geçersiz zaman: {value!r}")
                total += float(number) * {"h": 3600, "m": 60, "s": 1}[ch]
                number = ""
            else:
                raise ValueError(f"Geçersiz zaman: {value!r}")
        if number:
            total += float(number)
        return total
    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError(f"Geçersiz zaman: {value!r}")
    try:
        nums = [float(p) for p in parts]
    except ValueError as exc:
        raise ValueError(f"Geçersiz zaman: {value!r}") from exc
    total = 0.0
    for n in nums:
        total = total * 60 + n
    return total


def ass_timestamp(seconds: float) -> str:
    """Saniyeyi ASS altyazı zaman damgasına (H:MM:SS.cc) çevirir."""
    seconds = max(0.0, seconds)
    cs = int(round(seconds * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"
