"""
外掛市集網頁後端入口，第四階段開發重點，見 design.md 第 6.3、7 節。
"""

from fastapi import FastAPI

app = FastAPI(title="Discord Plugin Platform")


@app.get("/health")
async def health_check() -> dict:
    """
    健康檢查端點，確認服務是否正常運作。

    Returns:
        dict，固定回傳 {"status": "ok"}
    """
    return {"status": "ok"}
