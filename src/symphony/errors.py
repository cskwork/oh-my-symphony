"""SPEC §5.5, §10.6, §11.4 — typed error classes."""

from __future__ import annotations


class SymphonyError(Exception):
    code: str = "symphony_error"

    def __init__(self, message: str = "", **context: object) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.context = context

    def __str__(self) -> str:
        if not self.context:
            return f"{self.code}: {self.message}"
        ctx = " ".join(f"{k}={v!r}" for k, v in self.context.items())
        return f"{self.code}: {self.message} ({ctx})"


# §5.5
class MissingWorkflowFile(SymphonyError):
    code = "missing_workflow_file"


class WorkflowParseError(SymphonyError):
    code = "workflow_parse_error"


class WorkflowFrontMatterNotAMap(SymphonyError):
    code = "workflow_front_matter_not_a_map"


class TemplateParseError(SymphonyError):
    code = "template_parse_error"


class TemplateRenderError(SymphonyError):
    code = "template_render_error"


# §11.4
class UnsupportedTrackerKind(SymphonyError):
    code = "unsupported_tracker_kind"


class MissingTrackerApiKey(SymphonyError):
    code = "missing_tracker_api_key"


class MissingTrackerProjectSlug(SymphonyError):
    code = "missing_tracker_project_slug"


class LinearApiRequestError(SymphonyError):
    code = "linear_api_request"


class LinearApiStatusError(SymphonyError):
    code = "linear_api_status"


class LinearGraphQLErrors(SymphonyError):
    code = "linear_graphql_errors"


class LinearUnknownPayload(SymphonyError):
    code = "linear_unknown_payload"


class LinearMissingEndCursor(SymphonyError):
    code = "linear_missing_end_cursor"


class JiraApiRequestError(SymphonyError):
    code = "jira_api_request"


class JiraApiStatusError(SymphonyError):
    code = "jira_api_status"


class JiraUnknownPayload(SymphonyError):
    code = "jira_unknown_payload"


class JiraTransitionNotFound(SymphonyError):
    code = "jira_transition_not_found"


class JiraCommentConflict(SymphonyError):
    code = "jira_comment_conflict"


class JiraCommentDeliveryUncertain(SymphonyError):
    code = "jira_comment_delivery_uncertain"


class MissingTrackerEmail(SymphonyError):
    code = "missing_tracker_email"


class MissingTrackerEndpoint(SymphonyError):
    code = "missing_tracker_endpoint"


# §10.6
class CodexNotFound(SymphonyError):
    code = "codex_not_found"


class InvalidWorkspaceCwd(SymphonyError):
    code = "invalid_workspace_cwd"


class WorkspaceBoardUnreachable(SymphonyError):
    """The workspace's board directory is not the host board.

    Raised before the first turn is spent: a workspace whose board link
    points at a private copy makes every ticket write invisible to the
    orchestrator, which then re-dispatches the ticket forever.
    """

    code = "workspace_board_unreachable"


class ResponseTimeout(SymphonyError):
    code = "response_timeout"


class TurnTimeout(SymphonyError):
    code = "turn_timeout"


class PortExit(SymphonyError):
    code = "port_exit"


class ResponseError(SymphonyError):
    code = "response_error"


class TurnFailed(SymphonyError):
    code = "turn_failed"


class TurnCancelled(SymphonyError):
    code = "turn_cancelled"


class TurnInputRequired(SymphonyError):
    code = "turn_input_required"


# §6.3
class ConfigValidationError(SymphonyError):
    code = "config_validation_error"


class ProtectedSourceRepository(SymphonyError):
    """A workflow points at the checkout that supplies Symphony's runtime."""

    code = "protected_source_repository"


# board dependency DAG (symphony.trackers.validate)
class BoardDependencyError(SymphonyError):
    code = "board_dependency_error"


# operator chat (symphony.chat)
class ChatBusyError(SymphonyError):
    code = "chat_busy"


class ChatBackendUnavailableError(SymphonyError):
    code = "chat_backend_unavailable"


class ChatSessionExistsError(SymphonyError):
    code = "chat_session_exists"


class ChatNoSessionError(SymphonyError):
    code = "chat_no_session"


class ChatProjectActionError(SymphonyError):
    """A requested server-owned Chat project action cannot proceed."""

    code = "chat_project_action"


class ChatProjectAuthorizationError(SymphonyError):
    """A project action lacks its browser-held confirmation capability."""

    code = "chat_project_confirmation_forbidden"


class ChatIntentActionError(SymphonyError):
    """A requested Intent approval cannot proceed."""

    code = "chat_intent_action"


class ChatIntentAuthorizationError(SymphonyError):
    """An Intent approval lacks its browser-held confirmation capability."""

    code = "chat_intent_confirmation_forbidden"
