"""F3a contracts: explicit dual-user demands, bound E3 plans and job receipts."""
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .schemas import Contract, ModelUsage, Session, Source, TimedContract


class PolicyDemand(Contract):
    user_slot: str | None = None
    direction: Literal['downlink', 'uplink'] | None = None
    protocol: Literal['UDP', 'TCP'] | None = None
    min_bandwidth_mbps: float | None = Field(default=None, ge=0)
    max_bandwidth_mbps: float | None = Field(default=None, gt=0)
    priority: Literal['high', 'normal', 'low'] | None = None
    valid_for_seconds: int | None = Field(default=None, gt=0)
    measurement_window_seconds: int | None = Field(default=None, gt=0)
    destination: Literal['core', 'edge'] | None = None
    max_latency_ms: float | None = Field(default=None, gt=0)
    max_loss_percent: float | None = Field(default=None, ge=0, le=100)
    sources: dict[str, Source] = Field(default_factory=dict)

    @model_validator(mode='after')
    def sourced_fields(self):
        populated = {key for key in type(self).model_fields if key != 'sources' and getattr(self, key) is not None}
        if set(self.sources) != populated:
            raise ValueError('Every explicit demand needs exactly one source; unknown fields have none')
        return self

    def missing(self):
        required = ('user_slot', 'direction', 'protocol', 'min_bandwidth_mbps', 'max_bandwidth_mbps',
                    'priority', 'valid_for_seconds', 'measurement_window_seconds')
        return [key for key in required if getattr(self, key) is None]


class PolicyExtraction(Contract):
    scope: Literal['controlled_c1_downlink'] | None = None
    scope_source: Source | None = None
    requests: tuple[PolicyDemand, ...] = Field(default=(), max_length=8)
    ambiguities: tuple[str, ...] = ()
    unsupported_goals: tuple[Source, ...] = ()

    @model_validator(mode='after')
    def sourced_scope(self):
        if (self.scope is None) != (self.scope_source is None):
            raise ValueError('Explicit experiment scope needs its user source')
        return self


class PolicyReply(ModelUsage):
    extraction: PolicyExtraction


class LiveObservation(TimedContract):
    mode: Literal['live'] = 'live'
    ref: str
    observed_at: datetime
    expires_at: datetime
    release_id: str
    state: dict


class LivePlan(TimedContract):
    mode: Literal['live'] = 'live'
    ref: str
    requirement_ref: str
    requirement_version: int = Field(ge=1)
    observation_ref: str
    selected_mode: Literal['equal', 'guarantee-ue1', 'guarantee-ue2']
    expires_at: datetime
    release_id: str
    batch: dict
    policy: dict


class LivePreview(Contract):
    status: Literal['planned', 'rejected', 'needs_clarification']
    plan: LivePlan | None = None
    detail: str

    @model_validator(mode='after')
    def consistent_plan(self):
        if (self.status == 'planned') != (self.plan is not None):
            raise ValueError('Only a planned preview contains a live plan')
        return self


class OperationSnapshot(Contract):
    operation_id: str
    status: Literal['running', 'completed', 'unknown']
    result: dict | None = None
    evidence: str


class LiveSession(Session):
    mode: Literal['real_model_live'] = 'real_model_live'
    requirement: PolicyExtraction | None = None
    observation: LiveObservation | None = None
    plan: LivePlan | None = None
