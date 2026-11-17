"""
Centralized status enums for the CRM application.

Using (str, enum.Enum) so enum values are plain strings,
compatible with existing VARCHAR columns without migration.
"""
import enum


class InvoiceStatus(str, enum.Enum):
    DRAFT = "Draft"
    PENDING = "Pending"
    PAID = "Paid"
    OVERDUE = "Overdue"
    CANCELLED = "Cancelled"


class TaskStatus(str, enum.Enum):
    PENDING = "Pending"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"


class TaskPriority(str, enum.Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class UserRole(str, enum.Enum):
    SALES = "sales"
    MANAGER = "manager"
    ADMIN = "admin"
    MD = "md"
    PURCHASE = "purchase"


class UserStatus(str, enum.Enum):
    ACTIVE = "active"
    PENDING = "pending"
    DISABLED = "disabled"


class LeadStatus(str, enum.Enum):
    NEW = "New"
    ACTIVE = "Active"
    CONTACTED = "Contacted"
    QUALIFIED = "Qualified"
    PROPOSAL = "Proposal"
    CONVERTED = "Converted"
    LOST = "Lost"
    LOST_CLIENT = "Lost Client"


# Every status a lead can have while someone is still working it. The screens show them all as Active.
WORKING_LEAD_STATUSES = (
    LeadStatus.NEW,
    LeadStatus.ACTIVE,
    LeadStatus.CONTACTED,
    LeadStatus.QUALIFIED,
    LeadStatus.PROPOSAL,
)


class CompanyStatus(str, enum.Enum):
    ACTIVE = "active"
    PENDING = "pending"
    SUSPENDED = "suspended"
    REJECTED = "rejected"
    TRIAL = "trial"


class TransferRequestStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class LeaveStatus(str, enum.Enum):
    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"


class DealStageType(str, enum.Enum):
    OPEN = "open"
    WON = "won"
    LOST = "lost"


class QuoteStatus(str, enum.Enum):
    DRAFT = "draft"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class SalesOrderStatus(str, enum.Enum):
    OPEN = "open"
    INVOICED = "invoiced"


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class MeetingStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class CallDirection(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class SavedReportType(str, enum.Enum):
    LEADS_INVOICES = "leads_invoices"
    DEALS_PIPELINE = "deals_pipeline"
    GST_INVOICES = "gst_invoices"


class DashboardWidgetViz(str, enum.Enum):
    KPI = "kpi"
    CHART = "chart"
    TABLE = "table"


class ApiKeyAccess(str, enum.Enum):
    READ = "read"
    WRITE = "write"
