"""The common job format used throughout the pipeline."""

from dataclasses import dataclass


@dataclass
class Job:
    company: str
    title: str
    location: str
    application_url: str
    source: str  # URL of the public job list this listing came from.
