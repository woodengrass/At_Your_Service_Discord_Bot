"""
Pydantic 請求/回應驗證模型，第四階段開發重點，見 design.md 第 6.4 節列出的必要模型。
只涵蓋一般使用者能做的操作——審核（PluginReviewDecision）屬於平台操作者的動作，
不放在這個公開網頁後端，定義在獨立的 web/admin/backend/schemas.py（見 design.md 第 3.5、6.3 節）。
"""

from pydantic import BaseModel


class PluginSubmissionRequest(BaseModel):
    manifest_json: str
    source_code: str


class PluginSubmissionResponse(BaseModel):
    plugin_id: str
    status: str


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
