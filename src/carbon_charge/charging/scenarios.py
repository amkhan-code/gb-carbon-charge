"""Charging scenarios: the default car plus a small sensitivity set."""

from dataclasses import dataclass, replace
from datetime import date, time

CHARGER_KW = 7.0


@dataclass(frozen=True)
class Scenario:
    name: str
    plug_in: time = time(18, 0)  # UK local, on the evening of the "night" date
    energy_kwh: float = 8.0
    power_kw: float = CHARGER_KW
    deadline: time = time(7, 0)  # UK local, on the morning after
    nights: str = "every"  # "every" | "mon_wed_fri"

    def includes(self, night: date) -> bool:
        return self.nights == "every" or night.weekday() in (0, 2, 4)


DEFAULT = Scenario("default")


def sensitivity_set() -> list[Scenario]:
    """Default plus one-at-a-time variations: plug-in time, energy needed, charging frequency."""
    out = [DEFAULT]
    out += [replace(DEFAULT, name=f"plug_in_{h}:00", plug_in=time(h)) for h in (17, 19, 20, 21)]
    out += [replace(DEFAULT, name=f"energy_{e}kWh", energy_kwh=float(e)) for e in (4, 12, 16, 20)]
    out += [replace(DEFAULT, name="mon_wed_fri_only", nights="mon_wed_fri")]
    return out
