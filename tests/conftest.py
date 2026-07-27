import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

SERVICES_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "services")
)


def service_path(name: str) -> str:
    return os.path.join(SERVICES_DIR, name)
