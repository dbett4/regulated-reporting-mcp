"""
Shared state for operation logging and rate limit tracking.
Used by both MCP server and web dashboard.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass
class OperationRecord:
    timestamp: float
    tool_name: str
    endpoint: str
    method: str
    status_code: int
    duration_ms: float
    error: str | None = None
    extras: dict | None = None  # tool-specific fields (e.g., capture: tokens, cost_usd, payload_snippet)

    def to_dict(self) -> dict:
        out = {
            "timestamp": self.timestamp,
            "tool_name": self.tool_name,
            "endpoint": self.endpoint,
            "method": self.method,
            "status_code": self.status_code,
            "duration_ms": round(self.duration_ms, 1),
            "error": self.error,
            "time_ago": _time_ago(self.timestamp),
        }
        if self.extras is not None:
            out["extras"] = self.extras
        return out


def _time_ago(ts: float) -> str:
    delta = time.time() - ts
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    return f"{int(delta / 3600)}h ago"


@dataclass
class ToolRunRecord:
    timestamp: float
    tool_name: str
    params: dict
    result_summary: str
    status: str  # "success" or "error"
    duration_ms: float

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "tool_name": self.tool_name,
            "description": self._describe(),
            "params": self.params,
            "result_summary": self.result_summary,
            "status": self.status,
            "duration_ms": round(self.duration_ms, 1),
            "time_ago": _time_ago(self.timestamp),
        }

    def _describe(self) -> str:
        """Generate a human-readable description of what this tool did."""
        name = self.tool_name
        p = self.params

        # File operations
        if name == "workiva_list_files":
            f = p.get("filter", "")
            return f"Listed files{' with filter: ' + f if f else ''}"
        if name == "workiva_get_file":
            return f"Viewed file {p.get('file_id', '?')}"
        if name == "workiva_create_file":
            return f"Created {p.get('kind', 'file')}: {p.get('name', '?')}"
        if name == "workiva_import_file":
            return f"Imported file into {p.get('file_id', '?')}"
        if name == "workiva_export_file":
            return f"Exported file {p.get('file_id', '?')}"
        if name == "workiva_update_file":
            return f"Updated file {p.get('file_id', '?')}"
        if name == "workiva_file_permissions":
            return f"{p.get('operation', 'Checked')} permissions on file {p.get('file_id', '?')}"

        # Spreadsheet operations
        if name == "workiva_list_spreadsheets":
            return "Listed spreadsheets"
        if name == "workiva_get_spreadsheet":
            return f"Viewed spreadsheet {p.get('spreadsheet_id', '?')}"
        if name == "workiva_list_sheets":
            return f"Listed sheets on spreadsheet {p.get('spreadsheet_id', '?')}"
        if name == "workiva_get_sheet":
            return f"Viewed sheet {p.get('sheet_id', '?')}"
        if name == "workiva_create_sheet":
            return f"Created sheet on spreadsheet {p.get('spreadsheet_id', '?')}"
        if name == "workiva_delete_sheet":
            return f"Deleted sheet {p.get('sheet_id', '?')}"
        if name == "workiva_copy_sheet":
            return f"Copied sheet {p.get('sheet_id', '?')}"
        if name == "workiva_spreadsheet_sheets":
            op = p.get("operation", "list")
            return f"{op.title()} sheets on spreadsheet {p.get('spreadsheet_id', '?')} (deprecated)"
        if name == "workiva_export_spreadsheet":
            return f"Exported spreadsheet {p.get('spreadsheet_id', '?')} as {p.get('format', 'xlsx')}"
        if name == "workiva_publish_links":
            return f"Published links on {p.get('file_type', 'file')} {p.get('file_id', '?')}"
        if name == "workiva_reapply_filters":
            return f"Reapplied filters on {p.get('file_id', '?')}"

        # Cell operations
        if name == "workiva_read_cells":
            return f"Read cells from table {p.get('table_id', '?')}"
        if name == "workiva_write_cells":
            return f"Wrote cells to table {p.get('table_id', '?')}"
        if name == "workiva_format_cells":
            return f"Formatted cells in table {p.get('table_id', '?')}"
        if name == "workiva_edit_table_structure":
            return f"{p.get('operation', 'Edited')} structure of table {p.get('table_id', '?')}"

        # Document operations
        if name == "workiva_list_documents":
            return "Listed documents"
        if name == "workiva_get_document":
            return f"Viewed document {p.get('document_id', '?')}"
        if name == "workiva_export_document":
            return f"Exported document {p.get('document_id', '?')} as {p.get('format', 'pdf')}"
        if name == "workiva_list_document_sections":
            return f"Listed sections on document {p.get('document_id', '?')}"
        if name == "workiva_get_document_section":
            return f"Viewed section {p.get('section_id', '?')}"
        if name == "workiva_create_document_section":
            return f"Created section on document {p.get('document_id', '?')}"
        if name == "workiva_delete_document_section":
            return f"Deleted section {p.get('section_id', '?')}"
        if name == "workiva_copy_document_section":
            return f"Copied section {p.get('section_id', '?')}"
        if name == "workiva_bulk_edit_document_sections":
            return f"Bulk edited sections on document {p.get('document_id', '?')}"
        if name == "workiva_document_sections":
            op = p.get("operation", "list")
            return f"{op.title()} sections on document {p.get('document_id', '?')} (deprecated)"
        if name == "workiva_read_rich_text_paragraphs":
            return f"Read rich text paragraphs on {p.get('rich_text_id', '?')}"
        if name == "workiva_batch_edit_rich_text":
            return f"Batch edited rich text on {p.get('rich_text_id', '?')}"
        if name == "workiva_duplicate_rich_text":
            return f"Duplicated rich text {p.get('rich_text_id', '?')}"
        if name == "workiva_rich_text":
            op = p.get("operation", "read")
            return f"{op.replace('_', ' ').title()} rich text on {p.get('rich_text_id', '?')} (deprecated)"

        # Linking
        if name == "workiva_create_anchor":
            return f"Created anchor on table {p.get('table_id', '?')} at row {p.get('start_row', '?')}, col {p.get('start_column', '?')}"
        if name == "workiva_create_destination_link":
            return f"Created destination link in {p.get('target_type', '?')} {p.get('target_id', '?')}"
        if name == "workiva_list_range_links":
            return f"Listed range links on table {p.get('table_id', '?')}"
        if name == "workiva_get_range_link":
            return f"Viewed range link {p.get('range_link_id', '?')}"
        if name == "workiva_edit_range_link":
            return f"Edited range link {p.get('range_link_id', '?')}"
        if name == "workiva_get_range_link_destinations":
            return f"Got destinations for range link {p.get('range_link_id', '?')}"
        if name == "workiva_range_links":
            return f"{p.get('operation', 'List').title()} range links on table {p.get('table_id', '?')} (deprecated)"
        if name == "workiva_full_link_flow":
            return f"Full link flow: table {p.get('table_id', '?')} -> {p.get('target_type', '?')} {p.get('target_id', '?')}"

        # Workflows
        if name == "workiva_link_dates":
            mode = "Survey" if p.get("dry_run", True) else "Link"
            return f"{mode} dates from sheet {p.get('sheet_id', '?')} to document {p.get('document_id', '?')}"
        if name == "workiva_range_link_batch":
            mode = "Survey" if p.get("dry_run", True) else "Create"
            count = len(p.get("ranges", []))
            return f"{mode} {count} range links: spreadsheet {p.get('spreadsheet_id', '?')} -> document {p.get('document_id', '?')}"

        # Wdata
        if name == "workiva_wdata_list_tables":
            return "Listed Wdata tables"
        if name == "workiva_wdata_get_table":
            return f"Viewed Wdata table {p.get('table_id', '?')}"
        if name == "workiva_wdata_create_table":
            return f"Created Wdata table: {p.get('name', '?')}"
        if name == "workiva_wdata_delete_table":
            return f"Deleted Wdata table {p.get('table_id', '?')}"
        if name == "workiva_wdata_update_table":
            return f"Updated Wdata table {p.get('table_id', '?')}"
        if name == "workiva_wdata_tables":
            return f"{p.get('operation', 'List').title()} Wdata tables (deprecated)"
        if name == "workiva_wdata_list_queries":
            return "Listed Wdata queries"
        if name == "workiva_wdata_get_query":
            return f"Viewed query {p.get('query_id', '?')}"
        if name == "workiva_wdata_create_query":
            return f"Created query: {p.get('name', '?')}"
        if name == "workiva_wdata_delete_query":
            return f"Deleted query {p.get('query_id', '?')}"
        if name == "workiva_wdata_validate_query":
            return f"Validated query {p.get('query_id', '?') or 'raw SQL'}"
        if name == "workiva_wdata_describe_query":
            return f"Described query {p.get('query_id', '?')}"
        if name == "workiva_wdata_queries":
            return f"{p.get('operation', 'List').title()} Wdata queries (deprecated)"
        if name == "workiva_wdata_run_query":
            return f"Ran query {p.get('query_id', '?')}"
        if name == "workiva_wdata_get_query_result":
            return f"Got result for query {p.get('query_id', '?')}"
        if name == "workiva_wdata_cancel_query_run":
            return f"Cancelled run {p.get('run_id', '?')}"
        if name == "workiva_wdata_query_run":
            return f"{p.get('operation', 'Run').title()} query {p.get('query_id', '?')} (deprecated)"
        if name == "workiva_wdata_list_connections":
            return "Listed Wdata connections"
        if name == "workiva_wdata_get_connection":
            return f"Viewed connection {p.get('connection_id', '?')}"
        if name == "workiva_wdata_refresh_connection":
            return f"Refreshed connection {p.get('connection_id', '?')}"
        if name == "workiva_wdata_batch_refresh_connections":
            return f"Batch refreshed {len(p.get('connection_ids', []) or [])} connections"
        if name == "workiva_wdata_connections":
            return f"{p.get('operation', 'List').title()} Wdata connections (deprecated)"
        if name == "workiva_wdata_list_files":
            return "Listed Wdata files"
        if name == "workiva_wdata_get_file":
            return f"Viewed Wdata file {p.get('file_id', '?')}"
        if name == "workiva_wdata_upload_file":
            return f"Uploaded Wdata file {p.get('file_path', '?')}"
        if name == "workiva_wdata_delete_file":
            return f"Deleted Wdata file {p.get('file_id', '?')}"
        if name == "workiva_wdata_files":
            return f"{p.get('operation', 'List').title()} Wdata files (deprecated)"

        # Chains
        if name == "workiva_list_chains":
            return "Listed chains"
        if name == "workiva_get_chain":
            return f"Viewed chain {p.get('chain_id', '?')}"
        if name == "workiva_list_chain_runs":
            return f"Listed runs for chain {p.get('chain_id', '?')}"
        if name == "workiva_list_chain_run_nodes":
            return f"Listed nodes for run {p.get('run_id', '?')}"
        if name == "workiva_chains":
            return f"{p.get('operation', 'List').title()} chains (deprecated)"
        if name == "workiva_run_chain":
            return f"Triggered chain run: {p.get('chain_id', '?')}"
        if name == "workiva_chain_run":
            return f"Triggered chain run: {p.get('chain_id', '?')} (deprecated)"

        # Tasks
        if name == "workiva_list_tasks":
            return "Listed tasks"
        if name == "workiva_get_task":
            return f"Viewed task {p.get('task_id', '?')}"
        if name == "workiva_create_task":
            return f"Created task: {p.get('name', '?')}"
        if name == "workiva_update_task":
            return f"Updated task {p.get('task_id', '?')}"
        if name == "workiva_delete_task":
            return f"Deleted task {p.get('task_id', '?')}"
        if name == "workiva_tasks":
            return f"{p.get('operation', 'List').title()} tasks (deprecated)"
        if name == "workiva_submit_task":
            return f"Submitted task {p.get('task_id', '?')}"
        if name == "workiva_approve_task":
            return f"Approved task {p.get('task_id', '?')}"
        if name == "workiva_reject_task":
            return f"Rejected task {p.get('task_id', '?')}"
        if name == "workiva_reassign_task":
            return f"Reassigned task {p.get('task_id', '?')}"
        if name == "workiva_task_actions":
            return f"{p.get('action', 'Action')} on task {p.get('task_id', '?')} (deprecated)"

        # Admin
        if name == "workiva_list_organizations":
            return "Listed organizations"
        if name == "workiva_get_organization":
            return f"Viewed organization {p.get('org_id', '?')}"
        if name == "workiva_list_organization_users":
            return f"Listed users in org {p.get('org_id', '?')}"
        if name == "workiva_list_organization_roles":
            return f"Listed roles in org {p.get('org_id', '?')}"
        if name == "workiva_list_organization_solutions":
            return f"Listed solutions in org {p.get('org_id', '?')}"
        if name == "workiva_organization":
            return f"{p.get('operation', 'List').title()} organizations (deprecated)"
        if name == "workiva_list_workspaces":
            return "Listed workspaces"
        if name == "workiva_get_workspace":
            return f"Viewed workspace {p.get('workspace_id', '?')}"
        if name == "workiva_list_workspace_groups":
            return f"Listed groups in workspace {p.get('workspace_id', '?')}"
        if name == "workiva_list_workspace_memberships":
            return f"Listed memberships in workspace {p.get('workspace_id', '?')}"
        if name == "workiva_workspace":
            return f"{p.get('operation', 'List').title()} workspaces (deprecated)"

        # Content
        if name == "workiva_get_table_properties":
            return f"Got table properties for {p.get('table_id', '?')}"
        if name == "workiva_update_table_properties":
            return f"Updated table properties for {p.get('table_id', '?')}"
        if name == "workiva_get_table_columns":
            return f"Got columns for table {p.get('table_id', '?')}"
        if name == "workiva_get_table_rows":
            return f"Got rows for table {p.get('table_id', '?')}"
        if name == "workiva_table_properties":
            return f"{p.get('operation', 'Get').title()} table properties for {p.get('table_id', '?')} (deprecated)"
        if name == "workiva_get_style_guide":
            return f"Got style guide {p.get('style_guide_id', '?')}"
        if name == "workiva_export_style_guide":
            return f"Exported style guide {p.get('style_guide_id', '?')}"
        if name == "workiva_import_style_guide":
            return f"Imported style guide {p.get('style_guide_id', '?')}"
        if name == "workiva_style_guide":
            return f"{p.get('operation', 'Get').title()} style guide {p.get('style_guide_id', '?')} (deprecated)"

        # Presentations
        if name == "workiva_get_presentation":
            return f"Viewed presentation {p.get('presentation_id', '?')}"
        if name == "workiva_list_presentation_layouts":
            return f"Listed layouts on presentation {p.get('presentation_id', '?')}"
        if name == "workiva_list_presentation_slides":
            return f"Listed slides on presentation {p.get('presentation_id', '?')}"
        if name == "workiva_get_presentation_slide":
            return f"Viewed slide {p.get('slide_id', '?')}"
        if name == "workiva_update_presentation_slide":
            return f"Updated slide {p.get('slide_id', '?')}"
        if name == "workiva_presentation_slides":
            return f"{p.get('operation', 'List').title()} slides on presentation {p.get('presentation_id', '?')} (deprecated)"

        # Fallback
        params_str = ", ".join(f"{k}={v}" for k, v in p.items() if v) if p else ""
        return f"{name}({params_str})"


class AppState:
    def __init__(self, max_records: int = 500):
        self.operations: deque[OperationRecord] = deque(maxlen=max_records)
        self.tool_runs: deque[ToolRunRecord] = deque(maxlen=200)
        self._request_timestamps: deque[float] = deque(maxlen=1000)
        self.start_time = time.time()

    def log_operation(
        self,
        tool_name: str,
        endpoint: str,
        method: str,
        status_code: int,
        duration_ms: float,
        error: str | None = None,
        extras: dict | None = None,
    ):
        self.operations.appendleft(OperationRecord(
            timestamp=time.time(),
            tool_name=tool_name,
            endpoint=endpoint,
            method=method,
            status_code=status_code,
            duration_ms=duration_ms,
            error=error,
            extras=extras,
        ))
        self._request_timestamps.append(time.time())

    @property
    def total_calls(self) -> int:
        return len(self.operations)

    @property
    def error_count(self) -> int:
        return sum(1 for op in self.operations if op.status_code >= 400)

    @property
    def success_rate(self) -> float:
        if not self.operations:
            return 100.0
        ok = sum(1 for op in self.operations if op.status_code < 400)
        return round(ok / len(self.operations) * 100, 1)

    @property
    def avg_response_ms(self) -> float:
        if not self.operations:
            return 0.0
        return round(sum(op.duration_ms for op in self.operations) / len(self.operations), 1)

    @property
    def requests_last_minute(self) -> int:
        cutoff = time.time() - 60
        return sum(1 for ts in self._request_timestamps if ts > cutoff)

    def recent_operations(self, limit: int = 50) -> list[dict]:
        return [op.to_dict() for op in list(self.operations)[:limit]]

    def recent_errors(self, limit: int = 20) -> list[dict]:
        errors = [op.to_dict() for op in self.operations if op.error]
        return errors[:limit]

    def log_tool_run(
        self,
        tool_name: str,
        params: dict,
        result_summary: str,
        status: str,
        duration_ms: float,
    ):
        self.tool_runs.appendleft(ToolRunRecord(
            timestamp=time.time(),
            tool_name=tool_name,
            params=params,
            result_summary=result_summary,
            status=status,
            duration_ms=duration_ms,
        ))

    def recent_tool_runs(self, limit: int = 100) -> list[dict]:
        return [run.to_dict() for run in list(self.tool_runs)[:limit]]

    def stats(self) -> dict:
        return {
            "total_calls": self.total_calls,
            "error_count": self.error_count,
            "success_rate": self.success_rate,
            "avg_response_ms": self.avg_response_ms,
            "requests_last_minute": self.requests_last_minute,
            "uptime_seconds": round(time.time() - self.start_time),
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
app_state = AppState()
