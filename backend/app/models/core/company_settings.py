from sqlalchemy import Column, Integer, String, Text, Float, ForeignKey, Numeric, DateTime
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database import Base


class CompanySettings(Base):
    __tablename__ = "company_settings"
    
    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    company_name = Column(String(255), nullable=False, default="Company Name")
    address = Column(Text, nullable=True)
    gst_number = Column(String(50), nullable=True)
    logo_url = Column(String(500), nullable=True)
    pan = Column(String(20), nullable=True)
    cin = Column(String(30), nullable=True)
    state = Column(String(100), nullable=True)
    state_code = Column(String(2), nullable=True)
    contact_phone = Column(String(50), nullable=True)
    contact_email = Column(String(255), nullable=True)
    bank_account_name = Column(String(255), nullable=True)
    bank_name = Column(String(255), nullable=True)
    bank_account_number = Column(String(50), nullable=True)
    bank_ifsc = Column(String(20), nullable=True)
    quote_validity_days = Column(Integer, default=5)
    
    # Invoice settings
    invoice_prefix = Column(String(20), default="INV")
    tax_rate = Column(Float, default=18.0)
    payment_terms = Column(String(50), default="Net 30 days")
    
    # Pipeline settings (stored as JSON strings)
    lead_stages = Column(Text, default='["New", "Contacted", "Qualified", "Proposal", "Won", "Lost"]')
    lost_reasons = Column(Text, default='["No budget", "Timing not right", "Competitor", "No response"]')
    
    # Notification settings
    task_reminders_enabled = Column(Integer, default=1)  # 1=true, 0=false
    followup_alerts_enabled = Column(Integer, default=1)
    whatsapp_api_key = Column(String(255), nullable=True)
    whatsapp_source = Column(String(20), nullable=True)
    whatsapp_cadence_template_id = Column(Integer, nullable=True)
    exotel_sid = Column(String(64), nullable=True)
    exotel_api_key = Column(String(64), nullable=True)
    exotel_api_token_encrypted = Column(Text, nullable=True)
    exotel_subdomain = Column(String(255), nullable=True)
    exotel_caller_id = Column(String(20), nullable=True)
    twilio_account_sid = Column(String(64), nullable=True)
    twilio_auth_token_encrypted = Column(Text, nullable=True)
    twilio_api_key_sid = Column(String(64), nullable=True)
    twilio_api_key_secret_encrypted = Column(Text, nullable=True)
    twilio_twiml_app_sid = Column(String(64), nullable=True)
    twilio_phone_number = Column(String(20), nullable=True)
    twilio_default_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    twilio_ring_mobile_default = Column(Integer, default=1)
    onboarding_dismissed = Column(Integer, default=0)
    retention_days = Column(Integer, nullable=True)

    # Public meeting booking page (/book/{slug}); live only when both are set
    booking_slug = Column(String(64), nullable=True, index=True)
    booking_host_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Live GST e-invoice (NIC/IRP); live only when url + user + secrets set
    einvoice_base_url = Column(String(500), nullable=True)
    einvoice_username = Column(String(100), nullable=True)
    einvoice_password_encrypted = Column(Text, nullable=True)
    einvoice_client_id = Column(String(100), nullable=True)
    einvoice_client_secret_encrypted = Column(Text, nullable=True)

    deal_approval_amount_threshold = Column(Numeric(12, 2), nullable=True)
    discount_approval_percent_threshold = Column(Float, nullable=True)

    report_schedule_enabled = Column(Integer, default=0)
    report_schedule_frequency = Column(String(20), nullable=True)
    report_schedule_saved_report_id = Column(Integer, ForeignKey("saved_reports.id"), nullable=True)
    report_schedule_last_sent_at = Column(DateTime, nullable=True)

    # Google Sheet lead pull. Share the sheet as anyone-with-the-link.
    lead_sheet_url = Column(String(500), nullable=True)
    lead_sheet_last_synced_at = Column(DateTime, nullable=True)
    lead_sheet_last_result = Column(String(255), nullable=True)

    # Relationships
    company = relationship("Company", backref="settings")
