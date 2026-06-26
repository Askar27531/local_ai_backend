from __future__ import annotations

from typing import Any


class DomainError(Exception):
    code = "domain_error"

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class EntityNotFound(DomainError):
    code = "entity_not_found"


class InvalidStateTransition(DomainError):
    code = "invalid_state_transition"


class DuplicateEntity(DomainError):
    code = "duplicate_entity"


class ConfigurationError(DomainError):
    code = "configuration_error"


class WorkflowNotFound(DomainError):
    code = "workflow_not_found"


class WorkflowExecutionError(DomainError):
    code = "workflow_execution_error"


class UnsafePathError(DomainError):
    code = "unsafe_path"


class ArtifactStorageError(DomainError):
    code = "artifact_storage_error"


class ArtifactValidationError(DomainError):
    code = "artifact_validation_error"


class ToolNotFound(DomainError):
    code = "tool_not_found"


class ToolPermissionDenied(DomainError):
    code = "tool_permission_denied"


class ToolExecutionError(DomainError):
    code = "tool_execution_error"


class ToolInputValidationError(DomainError):
    code = "tool_input_validation_error"


class ApprovalRequired(DomainError):
    code = "approval_required"


class ModelProfileNotFound(DomainError):
    code = "model_profile_not_found"


class ModelProviderError(DomainError):
    code = "model_provider_error"


class ModelTimeoutError(DomainError):
    code = "model_timeout"


class ModelOutputValidationError(DomainError):
    code = "model_output_validation_error"


class PlanValidationError(DomainError):
    code = "plan_validation_error"


class CandidateValidationError(DomainError):
    code = "candidate_validation_error"
