"""Execution isolation for the process that calls models.

Tool sandboxes with ``network: none`` do not cover this process. OHBE allows
that process to reach one gateway host and port, and denies every other
destination. The Docker runner puts the process on an internal network so a
raw socket that skips the Python guard still cannot reach a model provider.
"""

from ohbe.environment.docker_runner import (
    DockerEgressRunner,
    DockerProbeReport,
    EgressBypassError,
    docker_available,
)
from ohbe.environment.guard import EgressGuard
from ohbe.environment.policy import (
    CLOUD_LITELLM_SUFFIXES,
    DENIED_PROVIDER_HOSTS,
    EGRESS_POLICY_VERSION,
    CloudLiteLLMDenied,
    EgressDenied,
    EgressSimulator,
    EgressVerdict,
    NetworkEgressPolicy,
    controlled_track_policy,
    is_cloud_litellm_host,
)

__all__ = [
    "CLOUD_LITELLM_SUFFIXES",
    "DENIED_PROVIDER_HOSTS",
    "EGRESS_POLICY_VERSION",
    "CloudLiteLLMDenied",
    "DockerEgressRunner",
    "DockerProbeReport",
    "EgressBypassError",
    "EgressDenied",
    "EgressGuard",
    "EgressSimulator",
    "EgressVerdict",
    "NetworkEgressPolicy",
    "controlled_track_policy",
    "docker_available",
    "is_cloud_litellm_host",
]
