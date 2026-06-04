from app.auth import ADMIN_GROUP, ALLOWED_GROUP
from app.routers.public import router


@router.get("/config")
async def get_config():
    return {
        "admin_group": ADMIN_GROUP,
        "allowed_group": ALLOWED_GROUP,
    }
