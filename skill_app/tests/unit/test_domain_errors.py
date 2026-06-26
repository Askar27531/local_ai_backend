from __future__ import annotations

from skill_app.domain.errors import DomainError, EntityNotFound


def test_domain_error_exposes_code_message_and_details():
    error = DomainError("Something failed.", {"task_id": "task-1"})

    assert error.code == "domain_error"
    assert error.message == "Something failed."
    assert error.details == {"task_id": "task-1"}
    assert str(error) == "Something failed."


def test_domain_error_subclass_uses_specific_code():
    error = EntityNotFound("Task was not found.")

    assert error.code == "entity_not_found"
