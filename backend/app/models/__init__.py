"""SQLAlchemy ORM models (PLAN Section 5, as amended by the Phase 1 contract).

Importing this package registers every table on ``app.db.Base.metadata`` (Alembic relies on it).
RLS policies, grants, triggers and SQL functions are not expressed here: they live in the
migrations (``0002_core_schema``, ``0005_import_sync_runs``) and are documented in ADRs 0008 and
0017.
"""

from app.models.accounts import Account, BalanceSnapshot, BankConnection, Institution
from app.models.agent import AgentConversation, AgentFinding, AgentMessage
from app.models.audit import AuditLog
from app.models.budgets import Budget, BudgetLine
from app.models.credit import CreditInquiry, CreditReport, CreditScoreEntry, Tradeline
from app.models.documents import Document, ParsedField
from app.models.household import Household, HouseholdSetting, Person
from app.models.identity import AppUser, AuthSession, TotpRecoveryCode, WebauthnCredential
from app.models.imports import ImportRun, SyncRun
from app.models.investments import Holding, InvestmentTxn, Liability, Security
from app.models.jobs import JobRun
from app.models.tax import EstimateSnapshot, TaxItem, TaxScenario, TaxYear
from app.models.transactions import Category, Rule, Transaction, TransactionSplit

__all__ = [
    "Account",
    "AgentConversation",
    "AgentFinding",
    "AgentMessage",
    "AppUser",
    "AuditLog",
    "AuthSession",
    "BalanceSnapshot",
    "BankConnection",
    "Budget",
    "BudgetLine",
    "Category",
    "CreditInquiry",
    "CreditReport",
    "CreditScoreEntry",
    "Document",
    "EstimateSnapshot",
    "Holding",
    "Household",
    "HouseholdSetting",
    "ImportRun",
    "Institution",
    "InvestmentTxn",
    "JobRun",
    "Liability",
    "ParsedField",
    "Person",
    "Rule",
    "Security",
    "SyncRun",
    "TaxItem",
    "TaxScenario",
    "TaxYear",
    "TotpRecoveryCode",
    "Tradeline",
    "Transaction",
    "TransactionSplit",
    "WebauthnCredential",
]
