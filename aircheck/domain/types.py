"""Closed vocabularies for the AIRCheck foundation domain."""

from enum import Enum, IntEnum


class SourceKind(str, Enum):
    SPEC = "SPEC"
    PROFILE = "PROFILE"
    REFERENCE = "REFERENCE"


class ProposedObligation(str, Enum):
    MUST = "MUST"
    SHOULD = "SHOULD"
    MAY = "MAY"
    UNKNOWN = "UNKNOWN"


class NormalizationStatus(str, Enum):
    EXECUTABLE = "EXECUTABLE"
    AMBIGUOUS = "AMBIGUOUS"
    CONTRADICTORY = "CONTRADICTORY"
    UNSUPPORTED = "UNSUPPORTED"
    EXTERNAL_DEPENDENCY = "EXTERNAL_DEPENDENCY"


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


class ApplicabilityStatus(str, Enum):
    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNRESOLVED = "UNRESOLVED"


class AutomationDisposition(str, Enum):
    AUTO_REMEDIATE = "AUTO_REMEDIATE"
    HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"
    REPORT_ONLY = "REPORT_ONLY"
    FORBIDDEN = "FORBIDDEN"


class MeasurementValueType(str, Enum):
    BOOL = "bool"
    STRING = "str"
    ENUM = "enum"
    INT = "int"
    FLOAT = "float"
    RATIONAL = "rational"


class ConstraintOperator(str, Enum):
    EQUALS = "EQUALS"
    MIN = "MIN"
    MAX = "MAX"
    RANGE = "RANGE"
    ONE_OF = "ONE_OF"
    MATCHES_PATTERN = "MATCHES_PATTERN"
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"


class ConditionField(str, Enum):
    ASSET_ROLE = "asset.role"
    RUN_CONTENT_TYPE = "run.content_type"
    RUN_DESTINATION_PROFILE = "run.destination_profile"


class ConditionOperator(str, Enum):
    EQ = "EQ"
    NEQ = "NEQ"
    IN = "IN"


class AssetRole(str, Enum):
    PROGRAM_MASTER = "PROGRAM_MASTER"
    CAPTIONS = "CAPTIONS"
    MANIFEST = "MANIFEST"
    OTHER = "OTHER"


class AssetProtection(str, Enum):
    ORIGINAL = "ORIGINAL"
    WORKING = "WORKING"
    DERIVATIVE = "DERIVATIVE"


class AssetRelation(str, Enum):
    COPIED_FROM = "COPIED_FROM"
    RENAMED_FROM = "RENAMED_FROM"
    CONVERTED_FROM = "CONVERTED_FROM"
    DERIVED_FROM = "DERIVED_FROM"


class PlanSource(str, Enum):
    PROPOSAL = "PROPOSAL"
    FALLBACK = "FALLBACK"


class MeasurementStatus(str, Enum):
    OK = "OK"
    ERROR = "ERROR"


class PredicateOutcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_EVALUATED = "NOT_EVALUATED"


class FindingLifecycle(str, Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"


class DecisionKind(str, Enum):
    TIER2_APPROVAL = "TIER2_APPROVAL"
    ESCALATION = "ESCALATION"


class DecisionRequestStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"


class DecisionChoice(str, Enum):
    APPROVED = "APPROVED"
    DENIED = "DENIED"


class AuthorizationStatus(str, Enum):
    ISSUED = "ISSUED"
    CONSUMED = "CONSUMED"
    VOID = "VOID"


class ActionActor(str, Enum):
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"


class ArtifactKind(str, Enum):
    TOOL_OUTPUT = "TOOL_OUTPUT"
    QC_REPORT = "QC_REPORT"
    MANIFEST = "MANIFEST"
    CHECKSUMS = "CHECKSUMS"
    LEDGER_EXPORT = "LEDGER_EXPORT"
    LINEAGE = "LINEAGE"
    DERIVATIVE = "DERIVATIVE"


class TerminalOutcome(str, Enum):
    DELIVERY_READY = "DELIVERY_READY"
    BLOCKED = "BLOCKED"


class AuthorityTier(IntEnum):
    INSPECT = 0
    REVERSIBLE = 1
    CONTENT_AFFECTING = 2
