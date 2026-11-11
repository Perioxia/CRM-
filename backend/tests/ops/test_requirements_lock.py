"""requirements.txt is generated from requirements.in (see its header); every pin in
requirements.in must appear in requirements.txt with the same version."""
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
PIN = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^]]*\])?==([^\s;]+)")


def _pins(path: Path) -> dict[str, str]:
    pins = {}
    for line in path.read_text().splitlines():
        m = PIN.match(line.strip())
        if m:
            pins[m.group(1).lower().replace("_", "-")] = m.group(2)
    return pins


def test_every_direct_dependency_is_pinned():
    unpinned = [
        line for line in (BACKEND / "requirements.in").read_text().splitlines()
        if line.strip() and not line.startswith("#") and not PIN.match(line.strip())
    ]
    assert not unpinned, f"pin these in requirements.in: {unpinned}"


def test_lock_matches_requirements_in():
    direct, lock = _pins(BACKEND / "requirements.in"), _pins(BACKEND / "requirements.txt")
    stale = {name: (version, lock.get(name)) for name, version in direct.items() if lock.get(name) != version}
    assert not stale, f"requirements.txt is out of date (name: in, lock): {stale}; regenerate it (see requirements.in)"
