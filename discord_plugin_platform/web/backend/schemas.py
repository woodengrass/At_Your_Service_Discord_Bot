"""
Pydantic 請求/回應驗證模型，第四階段開發重點，見 design.md 第 6.4 節列出的五個必要模型。
"""

from pydantic import BaseModel


class PluginSubmissionRequest(BaseModel):
    manifest_json: str
    source_code: str


class PluginSubmissionResponse(BaseModel):
    plugin_id: str
    status: str


class PluginReviewDecision(BaseModel):
    plugin_id: str
    approved: bool
    reason: str | None = None


class PluginInstallationRequest(BaseModel):
    guild_id: int
    plugin_id: str
    granted_capabilities: list[str]


class CapabilityConsentSummary(BaseModel):
    general_risk_capabilities: list[str]
    high_risk_capabilities: list[str]


class PluginListingResponse(BaseModel):
    plugin_id: str
    name: str
    description: str
    install_count: int
