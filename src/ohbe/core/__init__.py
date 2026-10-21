"""OHBE core schemas.

This package is the versioned document model for the benchmark engine.
It must not import Inspect AI, LiteLLM, the gateway package, or harness
adapters. Egress enforcement lives in `ohbe.environment`. The LiteLLM proxy
client lives in `ohbe.gateway`. The OpenBot and Inspect adapters live in
`ohbe.adapters`. Objective checks live in `ohbe.eval`, the offline cost
calculator lives in `ohbe.cost`, paired aggregation lives in
`ohbe.aggregate`, the pilot audit lives in `ohbe.audit`, and the v0.1
report renderer lives in `ohbe.report`. Round plans, frozen suites, dispatch
stubs, and model-fit reports live in `ohbe.core.round`. The HTTP workbench
surface lives in `ohbe.api` and is not imported here.
"""

from ohbe.core.base import SCHEMA_VERSION, AwareDatetime, OHBEModel
from ohbe.core.execution import Execution, ExecutionEnvironment, ExecutionStatus
from ohbe.core.experiment import Experiment
from ohbe.core.extensions import OracleStub, RoutingRegretStub
from ohbe.core.fingerprint import RuntimeFingerprint, current_runtime_fingerprint
from ohbe.core.harness import HarnessConfig, HarnessRef
from ohbe.core.integrity import (
    GENESIS_HASH,
    TraceIntegrityError,
    canonical_json,
    canonical_model_hash,
    sha256_canonical,
    verify_chain,
)
from ohbe.core.invocation import InvocationStatus, ModelInvocation
from ohbe.core.model import ModelDefinition, ModelPool
from ohbe.core.pricing import CostAssessment, CostStatus, PricingRef, select_pricing_ref
from ohbe.core.round import (
    DIMENSION_IDS,
    BudgetCaps,
    DimensionSummary,
    DifficultyWeight,
    DispatchJob,
    FixedHarnessModelMetrics,
    ModelFitRecommendation,
    ModelPoolRef,
    ObservationEvent,
    RoundPlan,
    RoundReport,
    StopConditions,
    SuiteTask,
    TaskSuite,
    TaxonomyFilter,
    build_round_report,
    campaign_stage,
    confirm_suite,
    draft_suite,
    fork_plan,
    generate_suite_tasks,
    not_measured_report,
    observation_stub,
    recommend_model_fit,
    visible_events,
)
from ohbe.core.task import Task, hash_task_set
from ohbe.core.trace import (
    ExecutionTrace,
    IntegrityReport,
    TraceEventType,
    TraceLog,
    TraceRecord,
)
from ohbe.core.usage import TokenUsage, UsageStatus

__all__ = [
    "DIMENSION_IDS",
    "GENESIS_HASH",
    "SCHEMA_VERSION",
    "AwareDatetime",
    "BudgetCaps",
    "CostAssessment",
    "CostStatus",
    "DimensionSummary",
    "DifficultyWeight",
    "DispatchJob",
    "Execution",
    "ExecutionEnvironment",
    "ExecutionStatus",
    "ExecutionTrace",
    "Experiment",
    "FixedHarnessModelMetrics",
    "HarnessConfig",
    "HarnessRef",
    "IntegrityReport",
    "InvocationStatus",
    "ModelDefinition",
    "ModelFitRecommendation",
    "ModelInvocation",
    "ModelPool",
    "ModelPoolRef",
    "OHBEModel",
    "ObservationEvent",
    "OracleStub",
    "PricingRef",
    "RoundPlan",
    "RoundReport",
    "RoutingRegretStub",
    "RuntimeFingerprint",
    "StopConditions",
    "SuiteTask",
    "Task",
    "TaskSuite",
    "TaxonomyFilter",
    "TokenUsage",
    "TraceEventType",
    "TraceIntegrityError",
    "TraceLog",
    "TraceRecord",
    "UsageStatus",
    "build_round_report",
    "campaign_stage",
    "canonical_json",
    "canonical_model_hash",
    "confirm_suite",
    "current_runtime_fingerprint",
    "draft_suite",
    "fork_plan",
    "generate_suite_tasks",
    "hash_task_set",
    "not_measured_report",
    "observation_stub",
    "recommend_model_fit",
    "select_pricing_ref",
    "sha256_canonical",
    "verify_chain",
    "visible_events",
]
