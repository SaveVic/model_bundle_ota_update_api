"""demo_api — OTA model-bundle update server.

Implements the update mechanism from
``week-1/pipeline-app/METHODOLOGY.md`` §2: a per-channel release index, a
one-round-trip ``check`` endpoint, and signature-gated serving of the bundle
artifacts (manifest, SHA256SUMS(.sig), individual model files, bundle.zip).
"""

__version__ = "0.1.0"
