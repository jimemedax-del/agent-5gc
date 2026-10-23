"""Versioned, strict contracts shared by the offline Agent components."""
from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True, allow_inf_nan=False,
                              revalidate_instances='always')


def fingerprint(value: BaseModel) -> str:
    payload = json.dumps(value.model_dump(mode='json'), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


class Phase(str, Enum):
    READY = 'ready'
    EXTRACTING = 'extracting'
    WAITING = 'waiting_input'
    INSPECTING = 'inspecting'
    PLANNING = 'planning'
    EXECUTING = 'executing'
    RECONCILING = 'reconciling'
    SUCCEEDED = 'succeeded'
    REJECTED = 'rejected'
    FAILED = 'failed'
    UNKNOWN = 'result_unknown'
    RESTORE_FAILED = 'restore_failed'
    BUDGET = 'budget_exceeded'
    REQUESTING_READ = 'requesting_read'
    INTERPRETING = 'interpreting'
    READ_ONLY_COMPLETE = 'read_only_complete'


class Message(Contract):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=8000)


class Source(Contract):
    message_id: str
    quote: str = Field(min_length=1)


class Requirement(Contract):
    """Goals remain separate from capability implementations and experiment IDs."""
    version: Literal['cnf-agent-requirement-v1'] = 'cnf-agent-requirement-v1'
    user_id: str | None = Field(default=None, min_length=1)
    service_id: str | None = Field(default=None, min_length=1)
    business_type: Literal['realtime_interaction', 'continuous_media', 'background_transfer'] | None = None
    direction: Literal['downlink', 'uplink'] | None = None
    destination: Literal['core', 'edge'] | None = None
    min_bandwidth_mbps: float | None = Field(default=None, ge=0)
    max_bandwidth_mbps: float | None = Field(default=None, gt=0)
    max_latency_ms: float | None = Field(default=None, gt=0)
    max_loss_percent: float | None = Field(default=None, ge=0, le=100)
    sources: dict[str, Source] = Field(default_factory=dict)

    @model_validator(mode='after')
    def source_fields(self):
        fields = set(type(self).model_fields) - {'version', 'sources'}
        populated = {key for key in fields if getattr(self, key) is not None}
        if set(self.sources) != populated:
            raise ValueError('Every known goal needs a source; unknown goals must have none')
        return self

    def missing(self) -> list[str]:
        # These are the inputs of the F1 simulator, not universal CNF requirements.
        required = ('user_id', 'service_id', 'direction', 'destination', 'max_bandwidth_mbps')
        return [key for key in required if getattr(self, key) is None]

    def conflicts(self) -> list[str]:
        low, high = self.min_bandwidth_mbps, self.max_bandwidth_mbps
        return ['最低带宽超过最高带宽'] if low is not None and high is not None and low > high else []


class Extraction(Contract):
    requirement: Requirement
    ambiguities: tuple[str, ...] = ()


class ModelUsage(Contract):
    tokens: int = Field(ge=0)
    metadata: dict = Field(default_factory=dict)


class ModelReply(ModelUsage):
    extraction: Extraction


class ReadRequest(ModelUsage):
    tool_call_id: str
    tool_name: Literal['inspect_network'] = 'inspect_network'
    scope: Literal['overview'] = 'overview'


class ReadFacts(Contract):
    mode: Literal['read_only_observed', 'read_only_fixture', 'unavailable']
    observed_at: str | None = None
    nodes_total: int | None = Field(default=None, ge=0)
    nodes_ready: int | None = Field(default=None, ge=0)
    pods_total: int | None = Field(default=None, ge=0)
    pods_ready: int | None = Field(default=None, ge=0)
    proxy_paths: int | None = Field(default=None, ge=0)
    detail: str


class Interpretation(Contract):
    summary: str = Field(min_length=1, max_length=2000)
    limitations: tuple[str, ...] = Field(min_length=1)
    network_config_changed: Literal[False] = False
    goal_verified: Literal[False] = False


class InterpretationReply(ModelUsage):
    interpretation: Interpretation


class TimedContract(Contract):
    @field_validator('*')
    @classmethod
    def timezone_required(cls, value):
        if isinstance(value, datetime) and value.utcoffset() is None:
            raise ValueError('Timestamp requires a timezone')
        return value


class Capability(Contract):
    id: str
    adapter_version: str
    directions: tuple[Literal['downlink', 'uplink'], ...]
    destinations: tuple[Literal['core', 'edge'], ...]
    max_bandwidth_mbps: float = Field(gt=0)


class Binding(Contract):
    user_id: str
    session_id: str


class Observation(TimedContract):
    ref: str
    mode: Literal['simulated'] = 'simulated'
    observed_at: datetime
    expires_at: datetime
    capabilities: tuple[Capability, ...]
    bindings: tuple[Binding, ...]


class BindService(Contract):
    kind: Literal['bind_service'] = 'bind_service'
    user_id: str
    service_id: str
    destination: Literal['core', 'edge']


class LimitBandwidth(Contract):
    kind: Literal['limit_bandwidth'] = 'limit_bandwidth'
    user_id: str
    service_id: str
    direction: Literal['downlink', 'uplink']
    max_mbps: float = Field(gt=0)


Action = Annotated[BindService | LimitBandwidth, Field(discriminator='kind')]


class Plan(TimedContract):
    ref: str
    mode: Literal['simulated'] = 'simulated'
    requirement_ref: str
    requirement_version: int = Field(ge=1)
    observation_ref: str
    session_id: str
    adapter_version: str
    expires_at: datetime
    actions: tuple[Action, ...] = Field(min_length=1)


class Preview(Contract):
    status: Literal['planned', 'rejected']
    plan: Plan | None = None
    reason: str

    @model_validator(mode='after')
    def consistent_plan(self):
        if (self.status == 'planned') != (self.plan is not None):
            raise ValueError('Only a planned preview contains a plan')
        return self


class Check(Contract):
    name: str
    expected: str
    actual: str
    passed: bool


class OperationResult(Contract):
    operation_id: str
    mode: Literal['simulated'] = 'simulated'
    plan_ref: str
    outcome: Literal['succeeded', 'target_unmet', 'failed', 'unknown']
    restoration: Literal['restored', 'failed', 'unknown']
    checks: tuple[Check, ...] = ()
    detail: str

    @model_validator(mode='after')
    def success_needs_evidence(self):
        if self.outcome == 'succeeded' and (
            self.restoration != 'restored' or not self.checks or not all(c.passed for c in self.checks)
        ):
            raise ValueError('Simulated success requires checks and confirmed restoration')
        return self


class Budget(Contract):
    model_calls: int = Field(default=4, ge=1)
    queries: int = Field(default=6, ge=1)
    experiments: int = Field(default=1, ge=0)
    steps: int = Field(default=20, ge=1)
    tokens: int = Field(default=4096, ge=1)
    elapsed_seconds: float = Field(default=60, gt=0)


class Usage(Contract):
    model_calls: int = Field(default=0, ge=0)
    queries: int = Field(default=0, ge=0)
    experiments: int = Field(default=0, ge=0)
    steps: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0, ge=0)


class Session(Contract):
    schema_version: Literal['cnf-agent-session-v1'] = 'cnf-agent-session-v1'
    id: str
    mode: Literal['simulated', 'real_model_readonly'] = 'simulated'
    revision: int = 0
    phase: Phase = Phase.WAITING
    requirement_version: int = 0
    messages: tuple[Message, ...] = ()
    requirement: Requirement | None = None
    observation: Observation | None = None
    plan: Plan | None = None
    operation_id: str | None = None
    result: OperationResult | None = None
    budget: Budget = Field(default_factory=Budget)
    usage: Usage = Field(default_factory=Usage)
    notes: tuple[str, ...] = ()
    read_facts: ReadFacts | None = None
    interpretation: Interpretation | None = None
